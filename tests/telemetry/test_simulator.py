"""Unit tests for the MQTT device simulator (src/telemetry/simulator.py).

No broker needed: the pure layer (sources, edge buffer, outage schedule,
device state machine) is tested directly, and the network layer is driven by
a fake paho client that records what would have gone over the wire. Every
produced payload is pushed through the real protocol parsers, so "the
simulator speaks the contract" is checked by the same code ingest runs.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.telemetry import protocol
from src.telemetry import simulator as sim

T0 = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
PREFIX = protocol.DEFAULT_TOPIC_PREFIX


def _small_source(**kw) -> sim.SyntheticBearingSource:
    defaults = dict(seed=7, samples=2048, sample_rate_hz=25600.0, life_snapshots=50)
    defaults.update(kw)
    return sim.SyntheticBearingSource(**defaults)


def _device(**kw) -> sim.SimulatedDevice:
    defaults = dict(
        device_id="simdev-01",
        machine_id="sim-01",
        source=_small_source(),
        buffer_capacity=5,
        boot_id="abcd1234",
        rng=np.random.default_rng(0),
        clock=lambda: 0.0,
    )
    defaults.update(kw)
    return sim.SimulatedDevice(**defaults)


def _parse(outbound: sim.Outbound) -> protocol.Snapshot:
    return protocol.parse_snapshot(protocol.telemetry_topic("sim-01"), outbound.payload)


def _kurtosis(x: np.ndarray) -> float:
    x = x - x.mean()
    return float(np.mean(x ** 4) / np.mean(x ** 2) ** 2)


# --- SyntheticBearingSource -------------------------------------------------------------


def test_synthetic_is_deterministic_per_seed_and_index():
    a, b = _small_source(), _small_source()
    for index in (0, 3, 49):
        ha, va = a.capture(index)
        hb, vb = b.capture(index)
        assert np.array_equal(ha, hb) and np.array_equal(va, vb)
    # Different index or seed -> different data.
    assert not np.array_equal(a.capture(0)[0], a.capture(1)[0])
    assert not np.array_equal(a.capture(0)[0], _small_source(seed=8).capture(0)[0])


def test_synthetic_shape_and_finite():
    h, v = _small_source().capture(10)
    assert h.shape == v.shape == (2048,)
    assert np.isfinite(h).all() and np.isfinite(v).all()


def test_synthetic_fault_energy_grows_then_holds():
    src = _small_source(samples=8192)
    h_new, _ = src.capture(0)
    h_mid, _ = src.capture(25)
    h_failed, v_failed = src.capture(50)
    rms = lambda x: float(np.sqrt(np.mean(x ** 2)))  # noqa: E731
    assert rms(h_new) < rms(h_mid) < rms(h_failed)
    # The defect is impulsive: kurtosis well above Gaussian (3) at failure.
    assert _kurtosis(h_failed) > _kurtosis(h_new) + 2.0
    assert rms(v_failed) < rms(h_failed)  # vertical is the attenuated axis
    # Past life_snapshots the severity is held, not extrapolated.
    assert src.severity(50) == src.severity(500) == 1.0
    assert rms(src.capture(500)[0]) == pytest.approx(rms(h_failed), rel=0.15)


def test_synthetic_low_sample_rate_keeps_resonance_below_nyquist():
    src = _small_source(sample_rate_hz=3200.0, samples=4096)
    assert src._resonance_hz < 1600.0
    h, _ = src.capture(50)
    assert np.isfinite(h).all()


def test_synthetic_rejects_bad_config():
    with pytest.raises(ValueError):
        _small_source(samples=8)
    with pytest.raises(ValueError):
        _small_source(life_snapshots=0)


def test_synthetic_conditions_match_xjtu_operating_conditions():
    from src.ingestion.xjtu_sy import OPERATING_CONDITIONS

    expected = tuple(
        (c["speed_rpm"], c["load_kn"]) for _, c in sorted(OPERATING_CONDITIONS.items())
    )
    assert sim.SYNTHETIC_CONDITIONS == expected


# --- XjtuSource ---------------------------------------------------------------------------


def _write_snapshot(path, value: float, rows: int = 64) -> None:
    lines = ["Horizontal_vibration_signals,Vertical_vibration_signals"]
    lines += [f"{value + i * 1e-3:.6f},{-value:.6f}" for i in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def fake_xjtu(tmp_path):
    """Mimic the extracted dataset: <root>/<condition dir>/BearingC_B/<n>.csv."""
    root = tmp_path / "XJTU-SY_Bearing_Datasets"
    b11 = root / "35Hz12kN" / "Bearing1_1"
    b23 = root / "37.5Hz11kN" / "Bearing2_3"
    for d in (b11, b23):
        d.mkdir(parents=True)
    # Numeric, not lexical, order: 1, 2, 10.
    for n, value in ((1, 1.0), (2, 2.0), (10, 10.0)):
        _write_snapshot(b11 / f"{n}.csv", value)
    _write_snapshot(b23 / "1.csv", 5.0)
    (root / "not_a_bearing").mkdir()
    return root


def test_xjtu_source_replays_in_numeric_order_and_holds_at_end(fake_xjtu):
    src = sim.XjtuSource(fake_xjtu, bearing_index=0)
    assert src.bearing_id == "Bearing1_1"
    assert (src.speed_rpm, src.load_kn) == (2100.0, 12.0)
    assert src.sample_rate_hz == 25600.0
    assert src.life_snapshots == 3
    firsts = [src.capture(i)[0][0] for i in range(5)]
    assert firsts == [1.0, 2.0, 10.0, 10.0, 10.0]
    h, v = src.capture(0)
    assert h.shape == v.shape == (64,)
    assert v[0] == -1.0


def test_xjtu_source_uses_bearing_condition_and_wraps(fake_xjtu):
    second = sim.XjtuSource(fake_xjtu, bearing_index=1)
    assert second.bearing_id == "Bearing2_3"
    assert (second.speed_rpm, second.load_kn) == (2250.0, 11.0)
    wrapped = sim.XjtuSource(fake_xjtu, bearing_index=2)  # 2 bearings -> wraps to the first
    assert wrapped.bearing_id == "Bearing1_1"


def test_xjtu_source_missing_dataset(tmp_path):
    with pytest.raises(FileNotFoundError):
        sim.XjtuSource(tmp_path / "nope")


def test_xjtu_device_payload_parses(fake_xjtu):
    device = _device(source=sim.XjtuSource(fake_xjtu))
    (ob,) = device.step(T0, connected=True)
    snap = _parse(ob)
    assert snap.sample_rate_hz == 25600.0 and snap.speed_rpm == 2100.0 and snap.load_kn == 12.0
    assert snap.horizontal[0] == pytest.approx(1.0)


# --- EdgeBuffer -------------------------------------------------------------------------


def test_edge_buffer_drop_oldest_and_counters():
    buf = sim.EdgeBuffer(3)
    assert [buf.push(i) for i in range(3)] == [None, None, None]
    assert buf.push(3) == 0 and buf.push(4) == 1
    assert buf.depth == 3 and buf.pushed_total == 5 and buf.dropped_total == 2
    assert buf.pop_all() == [2, 3, 4]
    assert buf.depth == 0 and buf.pop_all() == []


def test_edge_buffer_requeue_front_keeps_order_and_drops_oldest():
    buf = sim.EdgeBuffer(4)
    buf.push("c")
    buf.push("d")
    buf.requeue_front(["a", "b"])
    assert buf.pop_all() == ["a", "b", "c", "d"]
    buf.push("y")
    buf.push("z")
    buf.push("zz")
    buf.requeue_front(["w", "x"])  # room for one: the OLDER "w" is dropped
    assert buf.pop_all() == ["x", "y", "z", "zz"]
    assert buf.dropped_total == 1


def test_edge_buffer_rejects_zero_capacity():
    with pytest.raises(ValueError):
        sim.EdgeBuffer(0)


# --- OutageSchedule ---------------------------------------------------------------------


def test_outage_schedule_without_jitter():
    sched = sim.OutageSchedule(every=10, duration=5)
    pattern = [sched.in_outage(t) for t in range(0, 40)]
    expected = [10 <= t < 15 or 25 <= t < 30 for t in range(0, 40)]
    assert pattern == expected


def test_outage_schedule_disabled_and_jitter_bounded():
    assert not any(sim.OutageSchedule(0, 20).in_outage(t) for t in range(0, 1000, 7))
    sched = sim.OutageSchedule(every=10, duration=5, jitter=2, rng=np.random.default_rng(1))
    ts = np.arange(0, 200, 0.1)
    flags = [sched.in_outage(float(t)) for t in ts]
    # Run lengths of outages lie within duration ± jitter.
    runs, current = [], 0
    for f in flags:
        if f:
            current += 1
        elif current:
            runs.append(current * 0.1)
            current = 0
    assert runs and all(2.9 <= r <= 7.1 for r in runs)


# --- SimulatedDevice ---------------------------------------------------------------------


def test_device_online_step_publishes_live_snapshot():
    device = _device()
    out = device.step(T0, connected=True)
    assert len(out) == 1 and not out[0].buffered
    snap = _parse(out[0])
    assert snap.device_id == "simdev-01" and snap.machine_id == "sim-01"
    assert snap.boot_id == "abcd1234" and snap.seq == 0
    assert snap.time_synced and not snap.buffered
    assert snap.sampled_at == T0
    assert snap.n_samples == 2048
    np.testing.assert_allclose(snap.horizontal, out[0].snapshot.horizontal, rtol=1e-6)


def test_device_outage_buffers_then_flushes_oldest_first_before_live():
    device = _device(buffer_capacity=10)
    published: list[sim.Outbound] = []
    timeline = [True, True, False, False, False, True, True]
    for i, connected in enumerate(timeline):
        out = device.step(T0 + timedelta(seconds=2 * i), connected=connected)
        for ob in out:
            device.record_publish(True, buffered=ob.buffered)
        published.extend(out)
        if not connected:
            assert out == []

    snaps = [_parse(ob) for ob in published]
    seqs = [s.seq for s in snaps]
    assert seqs == list(range(7))  # strictly increasing, nothing lost
    assert [s.buffered for s in snaps] == [False, False, True, True, True, False, False]
    # Buffered snapshots keep their original capture time.
    assert snaps[2].sampled_at == T0 + timedelta(seconds=4)
    assert device.sent_total == 7 and device.sent_buffered_total == 3
    assert device.buffer.depth == 0


def test_device_buffer_overflow_drops_oldest_and_status_reports_it():
    device = _device(buffer_capacity=2)
    for i in range(5):
        device.step(T0 + timedelta(seconds=i), connected=False)
    assert device.buffer.depth == 2 and device.buffer.dropped_total == 3
    flushed = device.step(T0 + timedelta(seconds=10), connected=True)
    assert [ob.seq for ob in flushed] == [3, 4, 5]
    assert [ob.buffered for ob in flushed] == [True, True, False]

    status = protocol.parse_status(protocol.status_topic("simdev-01"), device.status_payload(T0))
    assert status.online and status.buffer_dropped_total == 3
    assert status.buffer_capacity == 2 and status.buffer_depth == 0


def test_device_loss_rate_consumes_seq_without_sending():
    device = _device(loss_rate=0.5, rng=np.random.default_rng(3))
    sent = []
    for i in range(40):
        sent.extend(device.step(T0 + timedelta(seconds=i), connected=True))
    seqs = [ob.seq for ob in sent]
    assert seqs == sorted(set(seqs))
    assert device.next_seq == 40 and device.captured_total == 40
    assert device.lost_total == 40 - len(sent) > 0
    assert len(sent) > 0
    # Gaps in seq are exactly the lost snapshots.
    assert set(range(40)) - set(seqs) and len(set(range(40)) - set(seqs)) == device.lost_total


def test_device_requeue_after_failed_publish_preserves_order():
    device = _device(buffer_capacity=10)
    for i in range(3):
        device.step(T0 + timedelta(seconds=i), connected=False)
    out = device.step(T0 + timedelta(seconds=3), connected=True)  # seqs 0..3
    device.record_publish(True, buffered=True)  # seq 0 delivered
    device.record_publish(False)  # seq 1 failed
    device.requeue(out[1:])
    assert device.publish_attempts_total == 2 and device.publish_failures_total == 1
    retry = device.step(T0 + timedelta(seconds=4), connected=True)
    assert [ob.seq for ob in retry] == [1, 2, 3, 4]
    # The previously-live seq 3 was held, so it is now flagged buffered.
    assert [ob.buffered for ob in retry] == [True, True, True, False]


@pytest.mark.parametrize("encoding", protocol.ENCODINGS)
def test_device_payloads_parse_in_every_encoding(encoding):
    device = _device(encoding=encoding)
    (ob,) = device.step(T0, connected=True)
    snap = _parse(ob)
    assert snap.encoding == encoding
    tol = 1e-3 if encoding == protocol.ENCODING_I16 else 1e-6
    peak = float(np.max(np.abs(ob.snapshot.horizontal)))
    np.testing.assert_allclose(snap.horizontal, ob.snapshot.horizontal, atol=tol * max(peak, 1.0))


def test_device_status_and_lwt_parse():
    clock = iter([100.0, 142.5])
    device = _device(clock=lambda: next(clock), snapshot_interval_s=2.0, heartbeat_interval_s=10.0)
    status = protocol.parse_status(protocol.status_topic("simdev-01"), device.status_payload(T0))
    assert status.online and status.machine_id == "sim-01" and status.boot_id == "abcd1234"
    assert status.uptime_s == pytest.approx(42.5)
    assert status.firmware == sim.FIRMWARE
    assert status.snapshot_interval_s == 2.0 and status.heartbeat_interval_s == 10.0
    assert status.wifi_rssi_dbm is not None and status.reported_at == T0

    lwt = protocol.parse_status(protocol.status_topic("simdev-01"), device.lwt_payload())
    assert lwt.online is False and lwt.machine_id is None


def test_device_boot_id_is_random_even_with_seed():
    a = sim.SimulatedDevice(device_id="d-1", machine_id="m-1", source=_small_source(),
                            rng=np.random.default_rng(1))
    b = sim.SimulatedDevice(device_id="d-1", machine_id="m-1", source=_small_source(),
                            rng=np.random.default_rng(1))
    assert protocol.is_valid_boot_id(a.boot_id) and a.boot_id != b.boot_id


def test_device_start_index_offsets_source():
    src = _small_source()
    device = _device(source=src, start_index=20)
    captured = device.capture(T0)
    assert captured.seq == 0
    np.testing.assert_allclose(captured.horizontal, src.capture(20)[0].astype(np.float32))


# --- CLI / fleet ------------------------------------------------------------------------


def test_parse_args_defaults_follow_contract():
    args = sim.parse_args([])
    assert (args.broker, args.port, args.devices, args.machine_prefix) == ("localhost", 1883, 3, "sim")
    assert args.source == "synthetic" and args.interval == 2.0 and args.heartbeat == 10.0
    assert args.samples == 32768 and args.sample_rate == 25600.0 and args.life_snapshots == 300
    assert args.outage_every == 0 and args.outage_duration == 20 and args.buffer_capacity == 50
    assert args.loss_rate == 0.0 and args.count == 0 and args.encoding == "f32le-b64"
    assert args.topic_prefix == PREFIX
    assert sim.parse_args(["--tls"]).port == 8883


@pytest.mark.parametrize("bad", [["--loss-rate", "1.0"], ["--devices", "0"], ["--topic-prefix", "a/#"],
                                 ["--samples", "10"], ["--interval", "0"]])
def test_parse_args_rejects_bad_values(bad):
    with pytest.raises(SystemExit):
        sim.parse_args(bad)


def test_build_fleet_ids_conditions_and_stagger():
    args = sim.parse_args(["--devices", "4", "--samples", "256", "--seed", "5", "--stagger", "0.5",
                           "--life-snapshots", "100"])
    fleet = sim.build_fleet(args)
    assert [(d.device_id, d.machine_id) for d in fleet] == [
        ("simdev-01", "sim-01"), ("simdev-02", "sim-02"), ("simdev-03", "sim-03"), ("simdev-04", "sim-04"),
    ]
    assert [d.source.speed_rpm for d in fleet] == [2100.0, 2250.0, 2400.0, 2100.0]
    assert [d._start_index for d in fleet] == [0, 12, 25, 38]
    assert len({d.boot_id for d in fleet}) == 4


def test_build_fleet_xjtu(fake_xjtu):
    args = sim.parse_args(["--source", "xjtu", "--data-dir", str(fake_xjtu), "--devices", "2"])
    fleet = sim.build_fleet(args)
    assert [d.source.bearing_id for d in fleet] == ["Bearing1_1", "Bearing2_3"]


def test_make_paho_client_sets_retained_lwt():
    args = sim.parse_args([])
    device = _device()
    client = sim.make_paho_client(device, args)
    # paho keeps the will on private attributes; assert them if present so a
    # paho upgrade that renames them degrades to a smoke test, not a failure.
    topic = getattr(client, "_will_topic", None)
    if topic is not None:
        assert (topic.decode() if isinstance(topic, bytes) else topic) == protocol.status_topic("simdev-01")
        assert client._will_retain is True and client._will_qos == 1
        lwt = protocol.parse_status(protocol.status_topic("simdev-01"), bytes(client._will_payload))
        assert lwt.online is False


# --- Network layer with a fake paho client ---------------------------------------------


class _Info:
    def __init__(self, ok: bool):
        self._ok = ok

    def wait_for_publish(self, timeout=None):
        if not self._ok:
            raise RuntimeError("not connected")

    def is_published(self):
        return self._ok


class _Sock:
    def __init__(self, log):
        self.log = log

    def shutdown(self, how):
        self.log.append("sock.shutdown")

    def close(self):
        self.log.append("sock.close")


class FakeClient:
    """Records the wire-visible behaviour of a paho client."""

    def __init__(self):
        self.on_connect = None
        self.on_disconnect = None
        self.log: list[str] = []
        self.published: list[tuple[str, bytes, bool]] = []
        self.connected = False

    def connect_async(self, host, port, keepalive=60):
        self.log.append(f"connect_async {host}:{port}")

    def loop_start(self):
        self.log.append("loop_start")
        if not self.connected:
            self.connected = True
            self.on_connect(self, None, {}, 0, None)

    def loop_stop(self):
        self.log.append("loop_stop")

    def socket(self):
        return _Sock(self.log)

    def reconnect(self):
        self.log.append("reconnect")
        self.connected = False  # CONNACK arrives once the loop runs

    def publish(self, topic, payload, qos=0, retain=False):
        assert qos == 1
        self.published.append((topic, payload, retain))
        return _Info(True)

    def disconnect(self):
        self.log.append("disconnect")


class _CountOutage:
    """Outage while the device has captured [start, end) snapshots."""

    def __init__(self, device, start, end):
        self.device, self.start, self.end = device, start, end

    def in_outage(self, t):
        return self.start <= self.device.captured_total < self.end


def test_runner_outage_drops_socket_without_disconnect_and_flushes_in_order():
    args = sim.parse_args(["--interval", "0.01", "--heartbeat", "0.05", "--count", "10",
                           "--summary-every", "100", "--drain-timeout", "5"])
    device = _device(buffer_capacity=20, snapshot_interval_s=0.01, heartbeat_interval_s=0.05)
    client = FakeClient()
    runner = sim.DeviceRunner(
        device, args, threading.Event(),
        schedule=_CountOutage(device, 3, 7),
        client_factory=lambda d, a: client,
    )
    runner.run()  # synchronous: returns after --count snapshots and a drained buffer
    assert runner.error is None

    telemetry = [(t, p) for t, p, _ in client.published if "/telemetry/" in t]
    statuses = [(t, p, r) for t, p, r in client.published if "/status/" in t]
    snaps = [protocol.parse_snapshot(t, p) for t, p in telemetry]
    assert [s.seq for s in snaps] == list(range(10))
    buffered = [s.seq for s in snaps if s.buffered]
    assert buffered == [3, 4, 5, 6]

    # Outage = loop stopped + socket torn down, with no DISCONNECT until the end.
    i_down = client.log.index("loop_stop")
    assert client.log[i_down + 1:i_down + 3] == ["sock.shutdown", "sock.close"]
    assert client.log.count("disconnect") == 1 and client.log[-2:] == ["disconnect", "loop_stop"]
    assert "reconnect" in client.log and runner.outages_total == 1 and runner.reconnects_total == 1

    # Statuses: retained, parseable, first online at connect, last offline at exit.
    parsed = [protocol.parse_status(t, p) for t, p, _ in statuses]
    assert all(r for _, _, r in statuses)
    assert parsed[0].online and parsed[-1].online is False
    assert len([s for s in parsed if s.online]) >= 2  # connect + post-reconnect/flush
    assert device.sent_total == 10 and device.publish_failures_total == 0


def test_runner_failed_publish_is_retried_in_order():
    args = sim.parse_args(["--interval", "0.01", "--heartbeat", "100", "--count", "6",
                           "--summary-every", "100", "--drain-timeout", "5"])
    device = _device(buffer_capacity=20)
    client = FakeClient()
    failures = {2}  # the 3rd telemetry publish attempt fails once
    attempts = {"n": 0}
    real_publish = client.publish

    def flaky_publish(topic, payload, qos=0, retain=False):
        if "/telemetry/" in topic:
            n = attempts["n"]
            attempts["n"] += 1
            if n in failures:
                return _Info(False)
        return real_publish(topic, payload, qos=qos, retain=retain)

    client.publish = flaky_publish
    runner = sim.DeviceRunner(device, args, threading.Event(), client_factory=lambda d, a: client)
    runner.run()
    snaps = [protocol.parse_snapshot(t, p) for t, p, _ in client.published if "/telemetry/" in t]
    assert [s.seq for s in snaps] == list(range(6))
    assert device.publish_failures_total == 1 and device.publish_attempts_total == 7
    assert snaps[2].buffered  # the retried snapshot went out as a late (buffered) one


def test_runner_stop_event_publishes_offline_status():
    args = sim.parse_args(["--interval", "0.01", "--summary-every", "100"])
    client = FakeClient()
    stop = threading.Event()
    runner = sim.DeviceRunner(_device(), args, stop, client_factory=lambda d, a: client)
    runner.start()
    import time
    time.sleep(0.15)
    stop.set()
    runner.join(timeout=5)
    assert not runner.is_alive() and runner.error is None
    last_topic, last_payload, retained = client.published[-1]
    assert "/status/" in last_topic and retained
    assert protocol.parse_status(last_topic, last_payload).online is False
    assert client.log[-2:] == ["disconnect", "loop_stop"]


# --- Payload size guard (M6 review regression) ----------------------------------------


def test_parse_args_rejects_json_snapshots_over_the_broker_limit():
    """json with the default 32768 samples is ~1.3 MB: the broker would
    disconnect the client and the requeued snapshot would loop forever."""
    with pytest.raises(SystemExit):
        sim.parse_args(["--encoding", "json"])
    with pytest.raises(SystemExit):
        sim.parse_args(["--encoding", "json", "--source", "xjtu"])
    assert sim.parse_args(["--encoding", "json", "--samples", "16384"]).encoding == "json"
    assert sim.parse_args(["--samples", "65536"]).samples == 65536  # f32 fits


@pytest.mark.parametrize("encoding", protocol.ENCODINGS)
def test_payload_estimate_is_an_upper_bound(encoding):
    n = 4096
    rng = np.random.default_rng(0)
    real = protocol.build_snapshot_payload(
        device_id="d", machine_id="m", boot_id="b1", seq=0,
        horizontal=rng.normal(0, 1, n), vertical=rng.normal(0, 1, n),
        sample_rate_hz=25600.0, speed_rpm=2100.0, load_kn=12.0, encoding=encoding)
    assert len(real) <= sim.estimated_payload_bytes(n, encoding)
