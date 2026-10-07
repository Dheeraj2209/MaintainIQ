"""MQTT device simulator: N fake ESP32 nodes feeding live telemetry (contract §10).

M6 replaces the dataset-replay stand-in with a real MQTT feed, but nobody
should need a soldering iron to see it work. This module plays the device side
of design/M6_LIVE_TELEMETRY.md faithfully enough that everything downstream —
ingest, idempotency, the edge-buffer KPIs, the online/offline rule — is
exercised exactly as real hardware would exercise it:

* one paho client per device, each with its own client id, LWT and boot_id,
  so the broker sees N independent devices rather than one multiplexed client;
* snapshots and retained status heartbeats in the §3/§4 wire format, built by
  src.telemetry.protocol (the same module ingest parses with, so the two
  cannot drift);
* network outages that are REAL at the broker: the network loop is stopped and
  the socket dropped without an MQTT DISCONNECT, so the broker fires the LWT
  and ingest sees the device go offline, while capture continues into the
  bounded edge buffer that is flushed oldest-first on reconnect;
* an optional loss rate that consumes a sequence number without sending,
  which is what the transmission-success KPI measures from seq gaps.

Layering: everything that decides WHAT to send (signal sources, EdgeBuffer,
OutageSchedule, SimulatedDevice) is pure and unit-tested without a broker.
DeviceRunner is the thin network layer that decides WHEN and pushes bytes
through paho; it takes an injectable client factory so even it can be driven
by a fake client in tests.

Run:  python -m src.telemetry.simulator --broker localhost --devices 3
"""
from __future__ import annotations

import argparse
import logging
import math
import secrets
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

import numpy as np

from src.telemetry import protocol

logger = logging.getLogger(__name__)

FIRMWARE = "maintainiq-simulator/0.1.0"

DEFAULT_DATA_DIR = Path("local_data/xjtu_full/XJTU-SY_Bearing_Datasets")
DEFAULT_SAMPLES = 32768
DEFAULT_SAMPLE_RATE_HZ = 25600.0
DEFAULT_LIFE_SNAPSHOTS = 300

# The three XJTU-SY operating conditions (speed, load). Synthetic devices
# cycle through them so a simulated fleet sits inside the speed/load range the
# RUL model was trained on instead of at one arbitrary point. Duplicated from
# src.ingestion.xjtu_sy.OPERATING_CONDITIONS on purpose: importing that module
# drags in the whole feature stack (scipy, pandas), which the synthetic path
# never needs. test_simulator asserts the two tables agree.
SYNTHETIC_CONDITIONS = (
    (2100.0, 12.0),
    (2250.0, 11.0),
    (2400.0, 10.0),
)


# --- Signal sources -----------------------------------------------------------------


class SignalSource(Protocol):
    """What a device samples from. `capture(index)` must be a pure function of
    the index so a buffered (late) snapshot carries exactly the data that was
    "sensed" at capture time, regardless of when it is encoded."""

    sample_rate_hz: float
    speed_rpm: float
    load_kn: float

    def capture(self, index: int) -> tuple[np.ndarray, np.ndarray]: ...


class SyntheticBearingSource:
    """Deterministic run-to-failure vibration for one bearing.

    Physics-flavoured, not physics-accurate — enough that the feature
    extractor sees the signatures it was built for:

    * shaft harmonics at 1x/2x/3x of speed_rpm/60 (imbalance/misalignment
      floor present on any rotating machine);
    * broadband Gaussian noise that rises gently with wear;
    * an outer-race defect: an impulse train at a BPFO-like rate (about 3.57x
      shaft speed, typical of small deep-groove ball bearings) with slight
      slip jitter, each impulse ringing a structural resonance with an
      exponential decay. That impulsiveness is what drives kurtosis up, and
      its amplitude grows quadratically over `life_snapshots`, then holds at
      failure severity.

    snapshot `index` is generated from its own RNG stream seeded by
    (seed, index), so the same index always yields the same arrays.
    """

    BPFO_ORDER = 3.57
    RESONANCE_HZ = 3500.0
    RING_DECAY_S = 0.0008

    def __init__(
        self,
        *,
        seed: int = 0,
        samples: int = DEFAULT_SAMPLES,
        sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
        speed_rpm: float = 2100.0,
        load_kn: float = 12.0,
        life_snapshots: int = DEFAULT_LIFE_SNAPSHOTS,
    ) -> None:
        if not protocol.MIN_SAMPLES <= samples <= protocol.MAX_SAMPLES:
            raise ValueError(
                f"samples must be between {protocol.MIN_SAMPLES} and {protocol.MAX_SAMPLES}, got {samples}"
            )
        if not 0 < sample_rate_hz <= protocol.MAX_SAMPLE_RATE_HZ:
            raise ValueError(f"sample_rate_hz out of range: {sample_rate_hz}")
        if speed_rpm <= 0 or load_kn < 0:
            raise ValueError("speed_rpm must be > 0 and load_kn >= 0")
        if life_snapshots < 1:
            raise ValueError("life_snapshots must be >= 1")
        self.seed = int(seed)
        self.samples = int(samples)
        self.sample_rate_hz = float(sample_rate_hz)
        self.speed_rpm = float(speed_rpm)
        self.load_kn = float(load_kn)
        self.life_snapshots = int(life_snapshots)
        # Keep the ringing below Nyquist for low-rate configs (e.g. an
        # ADXL345-like 3.2 kHz) so it stays a ringing tone, not aliasing.
        self._resonance_hz = min(self.RESONANCE_HZ, 0.35 * self.sample_rate_hz)
        ring_len = max(4, int(6 * self.RING_DECAY_S * self.sample_rate_hz))
        t = np.arange(ring_len) / self.sample_rate_hz
        self._kernel = np.exp(-t / self.RING_DECAY_S) * np.sin(2 * np.pi * self._resonance_hz * t)

    def severity(self, index: int) -> float:
        """0 = new, 1 = failed. Holds at 1 after life_snapshots."""
        return min(max(index, 0) / self.life_snapshots, 1.0)

    def capture(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng([self.seed, int(index)])
        s = self.severity(index)
        n, fs = self.samples, self.sample_rate_hz
        t = np.arange(n) / fs
        shaft_hz = self.speed_rpm / 60.0

        def harmonics(gain: float, phase: np.ndarray) -> np.ndarray:
            return gain * sum(
                amp * np.sin(2 * np.pi * order * shaft_hz * t + phase[order - 1])
                for order, amp in ((1, 0.08), (2, 0.04), (3, 0.02))
            )

        noise_std = 0.15 * (1.0 + 1.5 * s)
        fault_amp = 0.05 + 2.5 * s ** 2
        bpfo_hz = self.BPFO_ORDER * shaft_hz

        # Impulse instants: nominal BPFO period with ~1% slip jitter, random
        # starting phase. Rounded to sample indices and rung through the
        # resonance kernel.
        period = 1.0 / bpfo_hz
        count = int(n / fs / period) + 2
        instants = rng.uniform(0, period) + np.arange(count) * period
        instants += rng.normal(0.0, 0.01 * period, size=count)
        idx = np.rint(instants * fs).astype(np.int64)
        idx = idx[(idx >= 0) & (idx < n)]
        impulses = np.zeros(n)
        np.add.at(impulses, idx, fault_amp * rng.uniform(0.8, 1.2, size=idx.shape[0]))
        fault = np.convolve(impulses, self._kernel)[:n]

        phase_h = rng.uniform(0, 2 * np.pi, size=3)
        phase_v = rng.uniform(0, 2 * np.pi, size=3)
        horizontal = harmonics(1.0, phase_h) + fault + rng.normal(0.0, noise_std, n)
        # The vertical sensor sits further from the load zone: same defect,
        # attenuated, with its own noise and harmonic phases.
        vertical = harmonics(0.8, phase_v) + 0.6 * fault + rng.normal(0.0, 0.9 * noise_std, n)
        return horizontal, vertical


class XjtuSource:
    """Replays one real XJTU-SY run-to-failure bearing, snapshot by snapshot.

    Snapshot order and the speed/load for the bearing come from
    src.ingestion.xjtu_sy exactly as src/ingestion/backfill.py derives them
    (discover_bearings → condition → OPERATING_CONDITIONS), so a live replay
    of Bearing2_3 reaches ingest with the same operating point the model saw
    for it in training. Read-only use: that module is inside the ML boundary.

    Past the last file the source holds the final (failed) snapshot — a
    failed bearing stays failed, matching SyntheticBearingSource.
    """

    def __init__(self, data_dir: Path, bearing_index: int = 0) -> None:
        # Imported lazily: it pulls in pandas/scipy via the feature stack,
        # which the synthetic path should not pay for.
        from src.ingestion import xjtu_sy

        bearings = xjtu_sy.discover_bearings(Path(data_dir))
        if not bearings:
            raise FileNotFoundError(
                f"no Bearing1_1-style folders found below {data_dir}; "
                "point --data-dir at the extracted XJTU-SY dataset"
            )
        directory, bearing_id, condition = bearings[bearing_index % len(bearings)]
        # Same ordering key training uses (numeric file stem), so "snapshot 7"
        # means the same file here as in the feature table.
        files = sorted(directory.glob("*.csv"), key=xjtu_sy._number_from_name)
        if not files:
            raise FileNotFoundError(f"{directory} contains no CSV snapshots")
        operating = xjtu_sy.OPERATING_CONDITIONS[condition]
        self.bearing_id = bearing_id
        self.condition = condition
        self.files = files
        self.speed_rpm = float(operating["speed_rpm"])
        self.load_kn = float(operating["load_kn"])
        self.sample_rate_hz = float(xjtu_sy.SAMPLE_RATE_HZ)
        self._read = xjtu_sy.read_snapshot

    @property
    def life_snapshots(self) -> int:
        return len(self.files)

    def capture(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        path = self.files[min(max(index, 0), len(self.files) - 1)]
        return self._read(path)


# --- Edge buffer and outages -----------------------------------------------------------


class EdgeBuffer:
    """Bounded FIFO standing in for the device's flash/RAM snapshot buffer.

    Drop-OLDEST on overflow (contract §3): during a long outage the newest
    data is the most valuable for a live health view, and keeping the tail
    contiguous means ingest sees one gap instead of many. Counters are
    cumulative since boot, as the status payload requires.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("buffer capacity must be >= 1")
        self.capacity = int(capacity)
        self._items: deque = deque()
        self.pushed_total = 0
        self.dropped_total = 0

    def __len__(self) -> int:
        return len(self._items)

    @property
    def depth(self) -> int:
        return len(self._items)

    def push(self, item: Any) -> Any | None:
        """Append; returns the evicted oldest item if the buffer was full."""
        self.pushed_total += 1
        evicted = None
        if len(self._items) >= self.capacity:
            evicted = self._items.popleft()
            self.dropped_total += 1
        self._items.append(item)
        return evicted

    def pop_all(self) -> list:
        """Remove and return every item, oldest first."""
        items = list(self._items)
        self._items.clear()
        return items

    def requeue_front(self, items: Iterable[Any]) -> None:
        """Put unsent items back at the head, preserving their order.

        They are older than anything already buffered, so if there is no
        room it is THEY that get dropped — the same drop-oldest rule.
        """
        for item in reversed(list(items)):
            if len(self._items) >= self.capacity:
                self.dropped_total += 1
                continue
            self._items.appendleft(item)


class OutageSchedule:
    """When the simulated network is down, as a function of seconds since start.

    Outages start `every` seconds after the previous one ended (the first
    after `every` seconds of uptime) and last `duration` seconds, each start
    and duration perturbed by up to ±jitter so devices in a fleet drift apart
    instead of failing in lockstep. `every <= 0` disables outages. Queries
    must be monotonic in t (the schedule only walks forward).
    """

    def __init__(
        self,
        every: float,
        duration: float,
        jitter: float = 0.0,
        rng: np.random.Generator | None = None,
    ) -> None:
        self.every = float(every)
        self.duration = float(duration)
        self.jitter = max(float(jitter), 0.0)
        self._rng = rng or np.random.default_rng()
        self._start = math.inf
        self._end = math.inf
        if self.every > 0 and self.duration > 0:
            self._plan(0.0)

    def _wiggle(self) -> float:
        return float(self._rng.uniform(-self.jitter, self.jitter)) if self.jitter else 0.0

    def _plan(self, after: float) -> None:
        self._start = after + max(self.every + self._wiggle(), 0.5)
        self._end = self._start + max(self.duration + self._wiggle(), 0.5)

    def in_outage(self, t: float) -> bool:
        while t >= self._end:
            self._plan(self._end)
        return self._start <= t < self._end


# --- Device state machine ---------------------------------------------------------------


@dataclass(frozen=True)
class CapturedSnapshot:
    seq: int
    sampled_at: datetime
    horizontal: np.ndarray
    vertical: np.ndarray


@dataclass(frozen=True)
class Outbound:
    """One snapshot ready to publish. `buffered` is decided at send time —
    the same capture is live if sent immediately, buffered if held."""

    snapshot: CapturedSnapshot
    buffered: bool
    payload: bytes

    @property
    def seq(self) -> int:
        return self.snapshot.seq


def new_boot_id() -> str:
    # Always random, even with --seed: a seeded re-run reusing a boot_id
    # would collide with the previous run's (device_id, boot_id, seq)
    # idempotency keys and be silently discarded by ingest as duplicates.
    return secrets.token_hex(4)


class SimulatedDevice:
    """Everything an ESP32 node decides, with no I/O.

    Per capture tick the device consumes one `seq` (captured, not sent —
    contract §3), may lose the snapshot (`loss_rate`), and then either queues
    it in the edge buffer (offline) or returns the buffer contents followed by
    the live snapshot (online). Ordering guarantee: everything held is
    returned oldest-first with buffered=True BEFORE the newer live snapshot,
    so ingest sees each (device_id, boot_id) in seq order.

    The network layer reports each publish outcome back via record_publish /
    requeue; on failure the unsent tail goes back to the head of the buffer
    to be retried (ingest dedups the rare double delivery by idempotency key).
    """

    def __init__(
        self,
        *,
        device_id: str,
        machine_id: str,
        source: SignalSource,
        buffer_capacity: int = 50,
        loss_rate: float = 0.0,
        encoding: str = protocol.ENCODING_F32,
        snapshot_interval_s: float = 2.0,
        heartbeat_interval_s: float = 10.0,
        start_index: int = 0,
        boot_id: str | None = None,
        rng: np.random.Generator | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not protocol.is_valid_id(device_id) or not protocol.is_valid_id(machine_id):
            raise ValueError(f"invalid device/machine id: {device_id!r}/{machine_id!r}")
        if not 0.0 <= loss_rate < 1.0:
            raise ValueError("loss_rate must be in [0, 1)")
        if encoding not in protocol.ENCODINGS:
            raise ValueError(f"unknown encoding {encoding!r}")
        self.device_id = device_id
        self.machine_id = machine_id
        self.source = source
        self.buffer = EdgeBuffer(buffer_capacity)
        self.loss_rate = float(loss_rate)
        self.encoding = encoding
        self.snapshot_interval_s = float(snapshot_interval_s)
        self.heartbeat_interval_s = float(heartbeat_interval_s)
        self.boot_id = boot_id or new_boot_id()
        if not protocol.is_valid_boot_id(self.boot_id):
            raise ValueError(f"invalid boot_id {self.boot_id!r}")
        self._rng = rng or np.random.default_rng()
        # RSSI jitter gets its own stream so the loss pattern for a given
        # seed does not depend on how many heartbeats happened in between.
        self._rssi_rng = np.random.default_rng(int(self._rng.integers(2**32)))
        self._clock = clock
        self._booted_at = clock()
        self._start_index = int(start_index)
        self.next_seq = 0
        self.captured_total = 0
        self.lost_total = 0
        self.publish_attempts_total = 0
        self.publish_failures_total = 0
        self.sent_total = 0
        self.sent_buffered_total = 0

    # -- capture / send decisions --

    def capture(self, sampled_at: datetime) -> CapturedSnapshot | None:
        """One capture tick. Always consumes a seq; returns None if lost."""
        seq = self.next_seq
        self.next_seq += 1
        self.captured_total += 1
        if self.loss_rate and self._rng.random() < self.loss_rate:
            self.lost_total += 1
            return None
        horizontal, vertical = self.source.capture(self._start_index + seq)
        # Held as float32: what the wire format carries anyway, and it halves
        # the memory of a full buffer (50 x 2 x 32768 samples per device).
        return CapturedSnapshot(
            seq=seq,
            sampled_at=sampled_at,
            horizontal=np.asarray(horizontal, dtype=np.float32),
            vertical=np.asarray(vertical, dtype=np.float32),
        )

    def step(self, sampled_at: datetime, *, connected: bool) -> list[Outbound]:
        """Capture, then return what to publish now, in order (possibly empty)."""
        captured = self.capture(sampled_at)
        if not connected:
            if captured is not None:
                self.buffer.push(captured)
            return []
        out = self.drain()
        if captured is not None:
            out.append(self.encode(captured, buffered=False))
        return out

    def drain(self) -> list[Outbound]:
        """Empty the edge buffer, oldest first, flagged buffered=True."""
        return [self.encode(item, buffered=True) for item in self.buffer.pop_all()]

    def requeue(self, unsent: Iterable[Outbound]) -> None:
        """Put snapshots whose publish failed back at the buffer head."""
        self.buffer.requeue_front(ob.snapshot for ob in unsent)

    def record_publish(self, ok: bool, *, buffered: bool = False) -> None:
        self.publish_attempts_total += 1
        if ok:
            self.sent_total += 1
            if buffered:
                self.sent_buffered_total += 1
        else:
            self.publish_failures_total += 1

    # -- payloads --

    def encode(self, snapshot: CapturedSnapshot, *, buffered: bool) -> Outbound:
        payload = protocol.build_snapshot_payload(
            device_id=self.device_id,
            machine_id=self.machine_id,
            boot_id=self.boot_id,
            seq=snapshot.seq,
            horizontal=snapshot.horizontal,
            vertical=snapshot.vertical,
            sample_rate_hz=self.source.sample_rate_hz,
            speed_rpm=self.source.speed_rpm,
            load_kn=self.source.load_kn,
            sampled_at=snapshot.sampled_at,
            time_synced=True,  # the simulator's clock is the host's, NTP-synced
            buffered=buffered,
            encoding=self.encoding,
        )
        return Outbound(snapshot=snapshot, buffered=buffered, payload=payload)

    def uptime_s(self) -> float:
        return max(self._clock() - self._booted_at, 0.0)

    def status_payload(self, reported_at: datetime, *, online: bool = True) -> bytes:
        return protocol.build_status_payload(
            device_id=self.device_id,
            online=online,
            machine_id=self.machine_id,
            boot_id=self.boot_id,
            reported_at=reported_at,
            uptime_s=round(self.uptime_s(), 3),
            firmware=FIRMWARE,
            snapshot_interval_s=self.snapshot_interval_s,
            heartbeat_interval_s=self.heartbeat_interval_s,
            buffer_depth=self.buffer.depth,
            buffer_capacity=self.buffer.capacity,
            buffer_dropped_total=self.buffer.dropped_total,
            publish_attempts_total=self.publish_attempts_total,
            publish_failures_total=self.publish_failures_total,
            wifi_rssi_dbm=float(round(self._rssi_rng.normal(-62.0, 4.0), 1)),
        )

    def lwt_payload(self) -> bytes:
        return protocol.build_lwt_payload(self.device_id)

    def summary(self) -> str:
        return (
            f"{self.device_id} -> {self.machine_id} boot={self.boot_id} "
            f"captured={self.captured_total} sent={self.sent_total} "
            f"(buffered={self.sent_buffered_total}) lost={self.lost_total} "
            f"buffer={self.buffer.depth}/{self.buffer.capacity} "
            f"dropped={self.buffer.dropped_total} "
            f"publish_failures={self.publish_failures_total}/{self.publish_attempts_total}"
        )


# --- Fleet construction (pure) ----------------------------------------------------------


def device_ids(machine_prefix: str, index: int) -> tuple[str, str]:
    """(device_id, machine_id) for 1-based `index`: sim-01 / simdev-01."""
    machine_id = f"{machine_prefix}-{index:02d}"
    device_id = f"{machine_prefix}dev-{index:02d}"
    for ident in (machine_id, device_id):
        if not protocol.is_valid_id(ident):
            raise ValueError(f"--machine-prefix produces an invalid id: {ident!r}")
    return device_id, machine_id


def build_fleet(args: argparse.Namespace, *, clock: Callable[[], float] = time.monotonic) -> list[SimulatedDevice]:
    """Instantiate the configured devices and their signal sources."""
    base_seed = args.seed if args.seed is not None else secrets.randbits(32)
    devices: list[SimulatedDevice] = []
    for k in range(args.devices):
        device_id, machine_id = device_ids(args.machine_prefix, k + 1)
        if args.source == "xjtu":
            source: Any = XjtuSource(args.data_dir, bearing_index=k)
        else:
            speed, load = SYNTHETIC_CONDITIONS[k % len(SYNTHETIC_CONDITIONS)]
            source = SyntheticBearingSource(
                seed=base_seed + k,
                samples=args.samples,
                sample_rate_hz=args.sample_rate,
                speed_rpm=speed,
                load_kn=load,
                life_snapshots=args.life_snapshots,
            )
        # --stagger spreads the fleet across life so the dashboard shows a mix
        # of health states immediately instead of N identical new bearings.
        start = int(round(args.stagger * source.life_snapshots * k / max(args.devices, 1)))
        devices.append(SimulatedDevice(
            device_id=device_id,
            machine_id=machine_id,
            source=source,
            buffer_capacity=args.buffer_capacity,
            loss_rate=args.loss_rate,
            encoding=args.encoding,
            snapshot_interval_s=args.interval,
            heartbeat_interval_s=args.heartbeat,
            start_index=start,
            rng=np.random.default_rng([base_seed, k, 1]),
            clock=clock,
        ))
    return devices


# --- Network layer ------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_paho_client(device: SimulatedDevice, args: argparse.Namespace):
    """Real paho client for one device: own id, LWT, optional auth/TLS."""
    import paho.mqtt.client as mqtt

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=device.device_id,
        # A device that reboots comes back with a new boot_id; there is no
        # session state worth resuming, and the edge buffer — not the broker —
        # is the device-side store-and-forward (contract §3).
        clean_session=True,
    )
    client.will_set(
        protocol.status_topic(device.device_id, args.topic_prefix),
        device.lwt_payload(),
        qos=protocol.STATUS_QOS,
        retain=True,
    )
    if args.username and args.password:
        client.username_pw_set(args.username, args.password)
    if args.tls:
        client.tls_set(ca_certs=args.ca_cert)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


class DeviceRunner(threading.Thread):
    """Drives one SimulatedDevice over one MQTT connection.

    All device state is touched only from this thread; paho callbacks (on the
    paho network thread) just flip Events, so no locking is needed around
    the state machine.
    """

    POLL_S = 0.05
    RETRY_S = 2.0

    def __init__(
        self,
        device: SimulatedDevice,
        args: argparse.Namespace,
        stop: threading.Event,
        *,
        schedule: OutageSchedule | None = None,
        client_factory: Callable[[SimulatedDevice, argparse.Namespace], Any] = make_paho_client,
        now: Callable[[], datetime] = _utcnow,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name=f"sim-{device.device_id}", daemon=True)
        self.device = device
        self.args = args
        self.stop_event = stop
        self.schedule = schedule or OutageSchedule(0, 0)
        self._client_factory = client_factory
        self._now = now
        self._clock = clock
        self._connected = threading.Event()
        self._just_connected = threading.Event()
        self._in_outage = False
        self._next_reconnect = 0.0
        self.outages_total = 0
        self.reconnects_total = 0
        self.error: BaseException | None = None
        self.telemetry_topic = protocol.telemetry_topic(device.machine_id, args.topic_prefix)
        self.status_topic = protocol.status_topic(device.device_id, args.topic_prefix)

    # -- paho callbacks (network thread) --

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if getattr(reason_code, "is_failure", False):
            logger.warning("%s: broker refused connection: %s", self.device.device_id, reason_code)
            return
        self._connected.set()
        self._just_connected.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if self._connected.is_set():
            logger.info("%s: disconnected (%s)", self.device.device_id, reason_code)
        self._connected.clear()

    # -- publishing --

    def _publish(self, topic: str, payload: bytes, *, retain: bool = False) -> bool:
        try:
            info = self._client.publish(topic, payload, qos=1, retain=retain)
            info.wait_for_publish(timeout=self.args.publish_timeout)
            return bool(info.is_published())
        except (RuntimeError, ValueError, OSError) as exc:
            logger.debug("%s: publish to %s failed: %s", self.device.device_id, topic, exc)
            return False

    def _publish_status(self, *, online: bool = True) -> None:
        self._publish(self.status_topic, self.device.status_payload(self._now(), online=online), retain=True)

    def _publish_snapshots(self, outbound: list[Outbound]) -> None:
        flushed = False
        for i, ob in enumerate(outbound):
            ok = self._publish(self.telemetry_topic, ob.payload)
            self.device.record_publish(ok, buffered=ob.buffered)
            if not ok:
                # Stop at the first failure: sending later snapshots past a
                # failed one would break per-boot seq order at ingest.
                self.device.requeue(outbound[i:])
                return
            flushed = flushed or ob.buffered
        if flushed and self.device.buffer.depth == 0:
            self._publish_status()  # §4: status right after a buffer flush

    # -- outages --

    def _begin_outage(self) -> None:
        """Lose the network the way a device does: no DISCONNECT packet.

        Stopping the loop and tearing the socket down makes the broker see an
        abrupt close and publish the retained LWT (online=false); a clean
        disconnect() would suppress it and ingest would never see the drop.
        """
        self._in_outage = True
        self.outages_total += 1
        self._connected.clear()
        self._client.loop_stop()
        sock = self._client.socket()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        logger.info("%s: network outage started (capture continues into the edge buffer)", self.device.device_id)

    def _end_outage(self) -> None:
        now = self._clock()
        if now < self._next_reconnect:
            return
        try:
            self._client.reconnect()
        except (OSError, ValueError) as exc:
            logger.info("%s: reconnect failed (%s); retrying", self.device.device_id, exc)
            self._next_reconnect = now + self.RETRY_S
            return
        self._client.loop_start()
        self._in_outage = False
        self.reconnects_total += 1
        logger.info(
            "%s: network back; flushing %d buffered snapshot(s)", self.device.device_id, self.device.buffer.depth
        )

    # -- main loop --

    def run(self) -> None:
        try:
            self._run()
        except BaseException as exc:  # surfaced by main(); never die silently
            self.error = exc
            logger.exception("%s: simulator thread crashed", self.device.device_id)

    def _run(self) -> None:
        args, device = self.args, self.device
        self._client = self._client_factory(device, args)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.connect_async(args.broker, args.port, keepalive=args.keepalive)
        self._client.loop_start()

        t0 = self._clock()
        next_capture = t0
        next_heartbeat = t0 + args.heartbeat
        next_summary = t0 + args.summary_every
        next_flush_retry = t0
        finish_deadline: float | None = None

        while not self.stop_event.is_set():
            now = self._clock()
            done_capturing = bool(args.count) and device.captured_total >= args.count

            # Outages only matter while capturing; once a finite run is done,
            # come back online so the buffer can drain before exit.
            want_outage = self.schedule.in_outage(now - t0) and not done_capturing
            if want_outage and not self._in_outage:
                self._begin_outage()
            elif self._in_outage and not want_outage:
                self._end_outage()

            connected = self._connected.is_set() and not self._in_outage
            if connected and self._just_connected.is_set():
                self._just_connected.clear()
                self._publish_status()  # §4: status at connect
                next_heartbeat = now + args.heartbeat
            if connected and device.buffer.depth and now >= next_flush_retry:
                self._publish_snapshots(device.drain())
                next_flush_retry = now + 1.0

            if not done_capturing and now >= next_capture:
                self._publish_snapshots(device.step(self._now(), connected=connected))
                next_capture += args.interval
                if next_capture < now:  # fell behind (slow publish): don't burst
                    next_capture = now + args.interval

            if connected and now >= next_heartbeat:
                self._publish_status()
                next_heartbeat = now + args.heartbeat
            if now >= next_summary:
                logger.info("%s connected=%s", device.summary(), connected)
                next_summary = now + args.summary_every

            if done_capturing:
                if finish_deadline is None:
                    finish_deadline = now + args.drain_timeout
                if (connected and device.buffer.depth == 0) or now >= finish_deadline:
                    break
            self.stop_event.wait(self.POLL_S)

        self._shutdown()

    def _shutdown(self) -> None:
        if self._connected.is_set() and not self._in_outage:
            # Announce the clean exit ourselves; a normal DISCONNECT tells the
            # broker NOT to publish the LWT.
            self._publish_status(online=False)
            self._client.disconnect()
        self._client.loop_stop()
        logger.info("final: %s", self.device.summary())


# --- CLI --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.telemetry.simulator",
        description="Simulate ESP32 vibration nodes publishing M6 telemetry over MQTT.",
    )
    net = p.add_argument_group("broker")
    net.add_argument("--broker", default="localhost")
    net.add_argument("--port", type=int, default=None, help="default 1883, or 8883 with --tls")
    net.add_argument("--username", default="")
    net.add_argument("--password", default="")
    net.add_argument("--tls", action="store_true")
    net.add_argument("--ca-cert", default=None, help="CA bundle for --tls (default: system store)")
    net.add_argument("--topic-prefix", default=protocol.DEFAULT_TOPIC_PREFIX)
    net.add_argument("--keepalive", type=int, default=15,
                     help="MQTT keepalive (s); bounds how long a silent half-open link goes unnoticed")
    net.add_argument("--publish-timeout", type=float, default=10.0,
                     help="seconds to wait for a QoS-1 PUBACK before counting a publish as failed")

    fleet = p.add_argument_group("fleet")
    fleet.add_argument("--devices", type=int, default=3)
    fleet.add_argument("--machine-prefix", default="sim")
    fleet.add_argument("--source", choices=("synthetic", "xjtu"), default="synthetic")
    fleet.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    fleet.add_argument("--life-snapshots", type=int, default=DEFAULT_LIFE_SNAPSHOTS,
                       help="synthetic: snapshots from new to failure, then held at failure")
    fleet.add_argument("--stagger", type=float, default=0.0,
                       help="0..1: start device k at k/N * stagger of its life (0 = all start new)")

    sig = p.add_argument_group("signal / timing")
    sig.add_argument("--interval", type=float, default=2.0, help="snapshot period (s)")
    sig.add_argument("--heartbeat", type=float, default=10.0, help="status heartbeat period (s)")
    sig.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="synthetic samples per axis")
    sig.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE_HZ, help="synthetic sample rate (Hz)")
    sig.add_argument("--encoding", choices=protocol.ENCODINGS, default=protocol.ENCODING_F32)

    faults = p.add_argument_group("network faults")
    faults.add_argument("--outage-every", type=float, default=0.0, help="seconds between outages (0 = never)")
    faults.add_argument("--outage-duration", type=float, default=20.0)
    faults.add_argument("--outage-jitter", type=float, default=5.0, help="± seconds on outage start and length")
    faults.add_argument("--buffer-capacity", type=int, default=50)
    faults.add_argument("--loss-rate", type=float, default=0.0,
                        help="probability a captured snapshot is lost (seq consumed, never sent)")

    run = p.add_argument_group("run")
    run.add_argument("--count", type=int, default=0, help="snapshots per device (0 = forever)")
    run.add_argument("--seed", type=int, default=None, help="signal/loss/outage RNG seed (boot_id stays random)")
    run.add_argument("--summary-every", type=float, default=30.0, help="per-device summary log period (s)")
    run.add_argument("--drain-timeout", type=float, default=30.0,
                     help="with --count: max seconds to wait to flush the buffer before exiting")
    run.add_argument("--log-level", default="INFO")
    return p


# Worst-case-ish bytes per sample for `--encoding json`: a float64 repr such
# as "-0.12345678901234567" plus its comma. Measured average is ~19.6; this
# errs high so the check below never lets an oversize combination through.
_JSON_BYTES_PER_SAMPLE = 22
# Everything in a snapshot that is not the two axes (ids, timestamps, ...).
_PAYLOAD_OVERHEAD_BYTES = 2048
# XjtuSource always replays the dataset's full 32768-sample snapshots.
XJTU_SAMPLES = 32768


def estimated_payload_bytes(samples: int, encoding: str) -> int:
    """Upper estimate of one snapshot's size for `samples` per axis.

    Exists so parse_args can refuse a combination the broker would refuse:
    a snapshot over Mosquitto's 1 MiB limit gets the client disconnected, the
    snapshot is requeued at the head of the edge buffer, and it is re-sent
    first on every reconnect — an endless disconnect loop that delivers
    nothing (and fires the LWT each time)."""
    if encoding == protocol.ENCODING_JSON:
        per_axis = samples * _JSON_BYTES_PER_SAMPLE
    else:
        width = 2 if encoding == protocol.ENCODING_I16 else 4
        per_axis = 4 * math.ceil(samples * width / 3)  # base64
    return 2 * per_axis + _PAYLOAD_OVERHEAD_BYTES


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.port is None:
        args.port = 8883 if args.tls else 1883
    try:
        args.topic_prefix = protocol.normalize_prefix(args.topic_prefix)
    except ValueError as exc:
        parser.error(str(exc))
    checks = (
        (args.devices >= 1, "--devices must be >= 1"),
        (args.interval > 0, "--interval must be > 0"),
        (args.heartbeat > 0, "--heartbeat must be > 0"),
        (args.buffer_capacity >= 1, "--buffer-capacity must be >= 1"),
        (0.0 <= args.loss_rate < 1.0, "--loss-rate must be in [0, 1)"),
        (args.count >= 0, "--count must be >= 0"),
        (args.outage_every >= 0 and args.outage_duration >= 0, "outage timings must be >= 0"),
        (0.0 <= args.stagger <= 1.0, "--stagger must be in [0, 1]"),
        (args.life_snapshots >= 1, "--life-snapshots must be >= 1"),
        (protocol.MIN_SAMPLES <= args.samples <= protocol.MAX_SAMPLES,
         f"--samples must be between {protocol.MIN_SAMPLES} and {protocol.MAX_SAMPLES}"),
    )
    for ok, message in checks:
        if not ok:
            parser.error(message)
    samples = XJTU_SAMPLES if args.source == "xjtu" else args.samples
    size = estimated_payload_bytes(samples, args.encoding)
    if size > protocol.MAX_PAYLOAD_BYTES:
        parser.error(
            f"--encoding {args.encoding} with {samples} samples per axis makes ~{size} byte snapshots, "
            f"over the {protocol.MAX_PAYLOAD_BYTES}-byte MQTT payload limit; use f32le-b64/i16le-b64"
            + ("" if args.source == "xjtu" else " or fewer --samples")
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.seed is None:
        # Resolve once so the fleet and the outage schedules share it; logged
        # so a surprising run can be reproduced with --seed.
        args.seed = secrets.randbits(32)
    devices = build_fleet(args)
    base_seed = args.seed
    stop = threading.Event()
    runners = [
        DeviceRunner(
            device,
            args,
            stop,
            schedule=OutageSchedule(
                args.outage_every, args.outage_duration, args.outage_jitter,
                rng=np.random.default_rng([base_seed, k, 2]),
            ),
        )
        for k, device in enumerate(devices)
    ]
    logger.info(
        "simulating %d device(s) -> %s:%d prefix=%s source=%s interval=%.1fs count=%s seed=%d",
        len(runners), args.broker, args.port, args.topic_prefix, args.source, args.interval,
        args.count or "forever", args.seed,
    )
    for runner in runners:
        runner.start()
    try:
        while any(r.is_alive() for r in runners):
            for r in runners:
                r.join(timeout=0.5)
    except KeyboardInterrupt:
        logger.info("stopping: publishing offline status and disconnecting")
        stop.set()
        for r in runners:
            r.join(timeout=args.publish_timeout + 5)
    return 1 if any(r.error for r in runners) else 0


if __name__ == "__main__":
    sys.exit(main())
