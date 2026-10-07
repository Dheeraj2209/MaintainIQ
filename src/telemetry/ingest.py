"""Turn one MQTT message into DB rows and a model prediction (M6 contract §6).

This is the live counterpart of src/ingestion/replay_service.py: a snapshot
that arrives over MQTT goes through the SAME predictor, the SAME
rul_store persistence and the SAME src/prediction/pipeline.handle_prediction
fan-out as a replayed one, so an alert raised by a real (or simulated) sensor
is indistinguishable from any other — only `source = 'mqtt'` tells them apart.

Deliberately broker-agnostic: TelemetryIngestor takes (topic, payload bytes,
receive time) and knows nothing about paho. The network layer
(src/telemetry/mqtt_service.py) only hands messages over; everything worth
testing — validation, idempotency, auto-registration, error recording — lives
here and is tested by calling it directly against a temp SQLite file.

Every message leaves an audit trail. Accepted, rejected and errored snapshots
each get a telemetry_messages row (that table is the source for the system
KPIs, so a message that silently vanished would make the KPIs lie), and any
message attributable to a device bumps device_status.last_seen_at. The one
exception is a duplicate: the (device_id, boot_id, seq) row already exists, so
it is counted in the caller's in-memory stats instead of being re-inserted —
QoS 1 is at-least-once, and redeliveries after a reconnect are normal, not an
error.

Nothing here raises to the caller. The worker threads that call in (one for
snapshots, one for status) are the only consumers of their ingest queues; an
exception escaping one would stop that half of live ingest until the app
restarts. A missing model file (FileNotFoundError from the
predictor cache) is therefore recorded as an `error` outcome with the reading
kept, so telemetry keeps flowing while the model is being (re)trained. Such a
row carries its reading_id, and the transport KPIs (src/kpi/calculations.py)
count "error with a stored reading" as delivered: the snapshot made it from
device to database, only the model failed — that must not read as a sensor or
network outage.

Once a snapshot has parsed, every row written for it carries its own
(device_id, boot_id, seq) key — including the last-resort error row written
when something unexpected fails part-way (a locked DB, a changed predictor
result shape). Without the key the KPIs would count that seq as lost, the
error would be attributed to no device, and a redelivery would pass the
duplicate check and store a second reading for the same snapshot.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from src.ingestion.xjtu_sy import extract_snapshot_features  # read-only use (ML boundary)
from src.prediction import health_epoch, pipeline, rul_store
from src.prediction.rul_realtime import StaleEpoch
from src.telemetry import device_health, protocol
from src.telemetry.device_health import DEFAULT_HEARTBEAT_S, OFFLINE_AFTER_HEARTBEATS  # noqa: F401  re-exported
from src.telemetry.protocol import DeviceStatus, ProtocolError, Snapshot

logger = logging.getLogger(__name__)

# readings.dataset / machines.dataset for everything that arrived live.
LIVE_DATASET = "live_mqtt"
# Alert `source` for MQTT-originated predictions (contract §6).
MQTT_SOURCE = "mqtt"
# device_id recorded when a rejected message carried no usable one (§6).
UNKNOWN_DEVICE = "?"
# Contract §4 constants (DEFAULT_HEARTBEAT_S, OFFLINE_AFTER_HEARTBEATS) now
# live in src/telemetry/device_health.py and are re-exported above under the
# same names for existing callers.
# model_inference_log.model_version is NOT NULL; when the predictor could not
# even be built (no model file) there is no version to record.
UNAVAILABLE_MODEL_VERSION = "unavailable"

# Outcome statuses returned to the service (stats keys follow from these).
ACCEPTED = "accepted"
REJECTED = "rejected"
ERROR = "error"
DUPLICATE = "duplicate"

# The six readings columns promoted out of the feature dict (contract §6).
_SUMMARY_COLUMNS = {
    "vibration_h_rms": "h_rms",
    "vibration_h_kurtosis": "h_kurtosis",
    "vibration_v_rms": "v_rms",
    "vibration_v_kurtosis": "v_kurtosis",
    "cross_axis_rms_ratio": "cross_axis_rms_ratio",
    "cross_axis_correlation": "cross_axis_correlation",
}

# Optional device_status columns a status message may carry. On update each
# falls back to the stored value, so an LWT ({"online": false} only) marks the
# device offline without erasing its last known firmware and buffer figures.
_STATUS_COLUMNS = (
    "machine_id",
    "boot_id",
    "reported_at",
    "uptime_s",
    "firmware",
    "snapshot_interval_s",
    "heartbeat_interval_s",
    "buffer_depth",
    "buffer_capacity",
    "buffer_dropped_total",
    "publish_attempts_total",
    "publish_failures_total",
    "wifi_rssi_dbm",
)


@dataclass(frozen=True)
class IngestOutcome:
    """What happened to one message. `status` is one of accepted / rejected /
    error / duplicate; `kind` is telemetry or status."""

    status: str
    kind: str
    device_id: str | None = None
    machine_id: str | None = None
    error: str | None = None
    reading_id: int | None = None
    event: str | None = None  # alert event raised by the fan-out, if any
    latency_ms: float | None = None


def _utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# Moved to device_health with the state rule; the old name stays for callers here.
_parse_iso = device_health.parse_iso


def device_online(online, last_seen_at, heartbeat_interval_s, *, now: datetime | None = None) -> bool:
    """Contract §4 online boolean: the latest status said online AND the node
    was heard within 3 × heartbeat (10 s if unknown).

    A thin wrapper over the shared state rule (device_health.device_state):
    "online" here means online or stale. reported=True is safe because the
    boolean only ever cared about online-ness, and online = 1 implies a
    status was stored."""
    return device_health.is_heard(device_health.device_state(
        online, last_seen_at, heartbeat_interval_s, reported=True, now=now,
    ))


def _payload_size(payload) -> int | None:
    try:
        return len(payload)
    except TypeError:
        return None


def _default_fan_out(conn, result: dict, features: dict, timestamp: str, *,
                     prediction_id=None, reading_id=None) -> dict:
    """Production fan-out: alerts + email + realtime (and, through the realtime
    manager's listeners, the downstream MQTT alert topic). The ids link an
    opened alert to the stored reading and its prediction row."""
    return pipeline.handle_prediction(
        conn, result, source=MQTT_SOURCE, features=features, timestamp=timestamp,
        prediction_id=prediction_id, reading_id=reading_id,
    )


def ensure_telemetry_schema(conn: sqlite3.Connection) -> None:
    """Create the §6 tables (and device_incidents) if missing. Idempotent and
    additive only.

    A DB already at schema v1 is brought to the current version through the
    normal migration runner, so it gets stamped and later runs skip it. Below
    v1 (an empty or legacy-IMS file) only the telemetry DDL is applied:
    migration 1 drops legacy dataset tables, and that must stay an explicit
    backfill decision, never a side effect of a sensor connecting.
    """
    from src.storage.db import DEVICE_HEALTH_SCHEMA, TELEMETRY_SCHEMA
    from src.storage.migrations import current_version, run_migrations

    previous = conn.row_factory
    conn.row_factory = sqlite3.Row  # current_version reads row["v"]
    try:
        if current_version(conn) >= 1:
            run_migrations(conn)
        else:
            # The device-health table rides along: the silence watchdog calls
            # this too, and must work on whatever file ingest would accept.
            conn.executescript(TELEMETRY_SCHEMA + DEVICE_HEALTH_SCHEMA)
    finally:
        conn.row_factory = previous


class TelemetryIngestor:
    """Processes one message at a time per message kind. The MQTT service
    calls handle_snapshot from a single snapshot worker — which is what makes
    the check-then-insert idempotency test race-free and keeps per-device
    `seq` order intact — and handle_status from a separate status worker, so
    heartbeats are not stuck behind slow predictions. The two paths share no
    mutable state except the one-time schema check (locked), each opens its
    own connection, and the only row they both write (device_status) is
    updated with monotonic max() semantics."""

    def __init__(
        self,
        *,
        predictor_provider: Callable[[], object],
        connection_factory: Callable[[], sqlite3.Connection],
        topic_prefix: str = protocol.DEFAULT_TOPIC_PREFIX,
        auto_register: bool = True,
        # Called as (conn, result, features, timestamp, *, prediction_id, reading_id).
        on_prediction: Callable[..., object] | None = None,
    ) -> None:
        self._predictor_provider = predictor_provider
        self._connection_factory = connection_factory
        self._prefix = protocol.normalize_prefix(topic_prefix)
        self._auto_register = auto_register
        # Injectable like ReplayService.on_prediction so tests can observe the
        # fan-out without SMTP or an event loop.
        self._on_prediction = on_prediction or _default_fan_out
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    @property
    def topic_prefix(self) -> str:
        return self._prefix

    def _connect(self) -> sqlite3.Connection:
        """Open a connection, making sure the M6 tables exist the first time.

        The app never runs migrations at startup (backfill/seed do), so a
        developer's existing v1 maintainiq.db has no telemetry_messages /
        device_status until something migrates it — and without them every
        live message would end up as an `error`. Doing it lazily here, on the
        first message, rather than in the app lifespan keeps a plain API start
        (and the test suite's lifespan runs) from writing to that DB at all.
        """
        conn = self._connection_factory()
        if not self._schema_ready:
            with self._schema_lock:  # both workers may hit the first message
                if not self._schema_ready:
                    try:
                        ensure_telemetry_schema(conn)
                        self._schema_ready = True
                    except Exception:
                        # Leave the flag unset so the next message retries; this
                        # one fails on the missing table and is logged as usual.
                        logger.exception("Could not ensure the live-telemetry tables exist")
        return conn

    # --- Dispatch -----------------------------------------------------------------

    def handle_message(self, topic: str, payload, received_at: datetime | None = None,
                       *, retained: bool = False) -> IngestOutcome:
        """Route by topic kind. Anything that is neither telemetry nor status
        (a malformed topic, or our own alerts topic if someone subscribes
        widely) is recorded as rejected."""
        try:
            kind = protocol.parse_topic(topic, self._prefix).kind
        except ProtocolError as exc:
            return self._reject_unroutable(topic, payload, received_at, str(exc))
        if kind == protocol.KIND_TELEMETRY:
            return self.handle_snapshot(topic, payload, received_at)
        if kind == protocol.KIND_STATUS:
            return self.handle_status(topic, payload, received_at, retained=retained)
        return self._reject_unroutable(topic, payload, received_at, f"ingest does not accept {kind!r} topics")

    def _reject_unroutable(self, topic, payload, received_at, error) -> IngestOutcome:
        received = _utc(received_at)
        start = time.perf_counter()
        try:
            conn = self._connect()
            try:
                self._insert_message(
                    conn, device_id=UNKNOWN_DEVICE, received_at=received, status=REJECTED,
                    error=f"{error} (topic {topic!r})", payload_bytes=_payload_size(payload),
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.exception("Could not record rejected message on %r", topic)
        return IngestOutcome(status=REJECTED, kind="unknown", device_id=UNKNOWN_DEVICE, error=error)

    # --- Snapshots ----------------------------------------------------------------

    def handle_snapshot(self, topic: str, payload, received_at: datetime | None = None) -> IngestOutcome:
        """Validate, store and predict one §3 snapshot. Never raises."""
        received = _utc(received_at)
        start = time.perf_counter()
        try:
            conn = self._connect()
        except Exception as exc:
            logger.exception("No DB connection for telemetry on %r", topic)
            return IngestOutcome(status=ERROR, kind=protocol.KIND_TELEMETRY, error=repr(exc))
        # Filled in by _process_snapshot as it goes, so the handler below can
        # still attribute a failure to the snapshot's own key and reading.
        ctx: dict = {}
        try:
            return self._process_snapshot(conn, topic, payload, received, start, ctx)
        except Exception as exc:
            # Last line of defence: a bug or a DB failure mid-way. Record what
            # we can so the message is not invisible, then keep the worker alive.
            logger.exception("Telemetry ingest failed for %r", topic)
            common = ctx.get("common") or {}
            reading_id = ctx.get("reading_id")
            error = f"internal error: {exc!r}"
            try:
                conn.rollback()
                if common:
                    # The device spoke; the touch may have been rolled back.
                    self._touch_device(conn, common["device_id"], received,
                                       machine_id=common["machine_id"], boot_id=common["boot_id"])
                    self._insert_message(
                        conn, status=ERROR, error=error, reading_id=reading_id,
                        latency_ms=(time.perf_counter() - start) * 1000.0,
                        ignore_conflict=True, **common,
                    )
                else:
                    self._insert_message(
                        conn, device_id=UNKNOWN_DEVICE, received_at=received, status=ERROR,
                        error=error, payload_bytes=_payload_size(payload),
                        latency_ms=(time.perf_counter() - start) * 1000.0, ignore_conflict=True,
                    )
                conn.commit()
            except Exception:
                logger.exception("Could not record ingest error for %r", topic)
            return IngestOutcome(status=ERROR, kind=protocol.KIND_TELEMETRY,
                                 device_id=common.get("device_id"), machine_id=common.get("machine_id"),
                                 error=repr(exc), reading_id=reading_id)
        finally:
            conn.close()

    def _process_snapshot(self, conn, topic, payload, received: datetime, start: float,
                          ctx: dict | None = None) -> IngestOutcome:
        kind = protocol.KIND_TELEMETRY
        ctx = {} if ctx is None else ctx
        payload_bytes = _payload_size(payload)

        def elapsed_ms() -> float:
            return (time.perf_counter() - start) * 1000.0

        try:
            snap = protocol.parse_snapshot(topic, payload, prefix=self._prefix)
        except ProtocolError as exc:
            device_id = exc.device_id or UNKNOWN_DEVICE
            self._insert_message(
                conn, device_id=device_id, boot_id=exc.boot_id, seq=exc.seq,
                machine_id=exc.machine_id, received_at=received, status=REJECTED,
                error=str(exc), payload_bytes=payload_bytes, latency_ms=elapsed_ms(),
                # A malformed message can still carry a key that was already
                # used; the rejection is counted either way.
                ignore_conflict=True,
            )
            if exc.device_id:
                self._touch_device(conn, exc.device_id, received, machine_id=exc.machine_id, boot_id=exc.boot_id)
            conn.commit()
            return IngestOutcome(status=REJECTED, kind=kind, device_id=device_id,
                                 machine_id=exc.machine_id, error=str(exc), latency_ms=elapsed_ms())

        # An unsynced clock has no meaningful capture time; the receive time is
        # the best available stand-in (contract §3). time_synced=0 on the row
        # lets the KPIs exclude it from sync-lag figures.
        sampled_at = snap.sampled_at or received
        sampled_iso = protocol.format_utc(sampled_at)
        common = dict(
            device_id=snap.device_id, boot_id=snap.boot_id, seq=snap.seq,
            machine_id=snap.machine_id, sampled_at=sampled_iso, received_at=received,
            buffered=snap.buffered, time_synced=snap.time_synced, payload_bytes=payload_bytes,
        )
        # From here on a failure is attributable to this snapshot's key.
        ctx["common"] = common

        # The device spoke, whatever happens next.
        self._touch_device(conn, snap.device_id, received, machine_id=snap.machine_id, boot_id=snap.boot_id)

        if self._is_duplicate(conn, snap):
            # Not ours to record again; the error handler must not either.
            ctx.pop("common", None)
            conn.commit()
            return IngestOutcome(status=DUPLICATE, kind=kind, device_id=snap.device_id,
                                 machine_id=snap.machine_id, latency_ms=elapsed_ms())

        if not self._ensure_machine(conn, snap):
            self._insert_message(conn, status=REJECTED, error="unknown machine",
                                 latency_ms=elapsed_ms(), **common)
            conn.commit()
            return IngestOutcome(status=REJECTED, kind=kind, device_id=snap.device_id,
                                 machine_id=snap.machine_id, error="unknown machine", latency_ms=elapsed_ms())

        try:
            base = extract_snapshot_features(snap.horizontal, snap.vertical, snap.sample_rate_hz)
        except Exception as exc:  # protocol already validated the axes; this is defensive
            error = f"feature extraction failed: {exc}"
            self._insert_message(conn, status=ERROR, error=error, latency_ms=elapsed_ms(), **common)
            conn.commit()
            return IngestOutcome(status=ERROR, kind=kind, device_id=snap.device_id,
                                 machine_id=snap.machine_id, error=error, latency_ms=elapsed_ms())

        reading_id = self._insert_reading(conn, snap, base, sampled_at, sampled_iso)
        conn.commit()  # the reading is durable even if prediction fails below
        ctx["reading_id"] = reading_id

        # --- Predict + persist: the same path replay and /predictions/rul use.
        predictor = None
        infer_start = time.perf_counter()
        try:
            predictor = self._predictor_provider()
            # Synced to the machine's DB health epoch first, atomically with
            # the prediction (plan D4).
            result = health_epoch.predict_synced(
                predictor, conn, snap.machine_id,
                lambda: predictor.predict(
                    snap.machine_id, snap.horizontal, snap.vertical,
                    snap.sample_rate_hz, snap.speed_rpm, snap.load_kn,
                ),
            )
        except StaleEpoch:
            # The machine was reset mid-prediction: not a failure. The reading
            # is stored; the stale result is neither persisted nor fanned out.
            logger.info("Skipping the prediction for %s: its health state was reset "
                        "during the prediction", snap.machine_id)
            result = None
        except Exception as exc:
            # FileNotFoundError (no trained model yet) lands here too. Keep the
            # reading, record the failure in both logs, and move on.
            error = f"prediction failed: {exc!r}"
            logger.warning("Prediction failed for %s: %r", snap.machine_id, exc)
            try:
                rul_store.log_inference(
                    conn, machine_id=snap.machine_id,
                    model_version=str(getattr(predictor, "model_version", None) or UNAVAILABLE_MODEL_VERSION),
                    latency_ms=(time.perf_counter() - infer_start) * 1000.0, error=error,
                )
            except Exception:
                logger.exception("Could not log failed inference for %s", snap.machine_id)
            self._insert_message(conn, status=ERROR, error=error, reading_id=reading_id,
                                 latency_ms=elapsed_ms(), **common)
            conn.commit()
            return IngestOutcome(status=ERROR, kind=kind, device_id=snap.device_id,
                                 machine_id=snap.machine_id, error=error, reading_id=reading_id,
                                 latency_ms=elapsed_ms())
        infer_ms = (time.perf_counter() - infer_start) * 1000.0

        event = None
        if result is not None:
            event = self._persist_and_fan_out(conn, snap, result, base, sampled_iso,
                                              reading_id=reading_id, infer_ms=infer_ms)

        latency = elapsed_ms()
        self._insert_message(conn, status=ACCEPTED, reading_id=reading_id, latency_ms=latency, **common)
        conn.commit()
        return IngestOutcome(status=ACCEPTED, kind=kind, device_id=snap.device_id,
                             machine_id=snap.machine_id, reading_id=reading_id, event=event,
                             latency_ms=latency)

    def _persist_and_fan_out(self, conn, snap: Snapshot, result: dict, base: dict, sampled_iso: str,
                             *, reading_id: int, infer_ms: float):
        """Store the prediction and fan it out; returns the alert event, if any."""
        prediction_id = rul_store.persist_prediction(conn, result, reading_id=reading_id)
        rul_store.log_inference(
            conn, machine_id=snap.machine_id, model_version=result["model_version"],
            latency_ms=infer_ms, result=result,
        )
        if prediction_id is None:
            # Overtaken by a reset or re-arm: not stored, so no alerting (D6).
            logger.info("Skipping fan-out of a stale prediction for %s", snap.machine_id)
            return None

        # As in replay: the prediction row is the durable record, alerting and
        # paging are consequences of it. A fan-out failure is logged, not
        # allowed to turn an accepted reading into an error.
        try:
            fanned = self._on_prediction(conn, result, base, sampled_iso,
                                         prediction_id=prediction_id, reading_id=reading_id)
            if isinstance(fanned, dict):
                return fanned.get("event")
        except Exception:
            logger.exception("Alert fan-out failed for %s", snap.machine_id)
        return None

    def _is_duplicate(self, conn, snap: Snapshot) -> bool:
        return conn.execute(
            "SELECT 1 FROM telemetry_messages WHERE device_id = ? AND boot_id = ? AND seq = ?",
            snap.idempotency_key,
        ).fetchone() is not None

    def _ensure_machine(self, conn, snap: Snapshot) -> bool:
        exists = conn.execute(
            "SELECT 1 FROM machines WHERE machine_id = ?", (snap.machine_id,)
        ).fetchone() is not None
        if exists:
            return True
        if not self._auto_register:
            return False
        conn.execute(
            """INSERT INTO machines
                   (machine_id, bearing_id, speed_rpm, load_kn, dataset, is_documented_failure)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (snap.machine_id, snap.machine_id, snap.speed_rpm, snap.load_kn, LIVE_DATASET),
        )
        logger.info("Auto-registered live machine %s", snap.machine_id)
        return True

    def _insert_reading(self, conn, snap: Snapshot, base: dict, sampled_at: datetime, sampled_iso: str) -> int:
        cycle = conn.execute(
            "SELECT COALESCE(MAX(cycle), -1) + 1 FROM readings WHERE machine_id = ?",
            (snap.machine_id,),
        ).fetchone()[0]
        first_live = conn.execute(
            "SELECT MIN(timestamp) FROM readings WHERE machine_id = ? AND dataset = ?",
            (snap.machine_id, LIVE_DATASET),
        ).fetchone()[0]
        first = _parse_iso(first_live)
        # Clamped at 0: a device that only NTP-syncs after its first snapshots
        # can deliver a capture time earlier than its first (receive-time
        # stamped) reading, and a negative age would break elapsed-time charts.
        elapsed = 0.0 if first is None else max(0.0, (sampled_at - first).total_seconds() / 60.0)

        summary = {column: float(base.get(key, 0.0)) for column, key in _SUMMARY_COLUMNS.items()}
        cur = conn.execute(
            """INSERT INTO readings
                   (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm, load_kn,
                    sample_rate_hz, vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
                    vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation,
                    rul_minutes, features_json, dataset)
               VALUES (:machine_id, :timestamp, :cycle, :elapsed, :speed_rpm, :load_kn,
                       :sample_rate_hz, :vibration_h_rms, :vibration_h_kurtosis, :vibration_v_rms,
                       :vibration_v_kurtosis, :cross_axis_rms_ratio, :cross_axis_correlation,
                       NULL, :features_json, :dataset)""",
            {
                "machine_id": snap.machine_id,
                "timestamp": sampled_iso,
                "cycle": cycle,
                "elapsed": elapsed,
                "speed_rpm": snap.speed_rpm,
                "load_kn": snap.load_kn,
                "sample_rate_hz": snap.sample_rate_hz,
                **summary,
                "features_json": json.dumps(base),
                "dataset": LIVE_DATASET,
            },
        )
        return cur.lastrowid

    # --- Status -------------------------------------------------------------------

    def handle_status(self, topic: str, payload, received_at: datetime | None = None,
                      *, retained: bool = False) -> IngestOutcome:
        """Upsert device_status from a §4 status / heartbeat / LWT. Never raises.

        `retained` marks a message the broker replayed on subscribe rather than
        one the device just sent. For those, last_seen_at uses the device's own
        reported_at (when present) instead of "now": otherwise every app restart
        would make a long-dead device look freshly seen."""
        received = _utc(received_at)
        start = time.perf_counter()
        kind = protocol.KIND_STATUS
        try:
            conn = self._connect()
        except Exception as exc:
            logger.exception("No DB connection for status on %r", topic)
            return IngestOutcome(status=ERROR, kind=kind, error=repr(exc))
        try:
            try:
                status = protocol.parse_status(topic, payload, prefix=self._prefix)
            except ProtocolError as exc:
                device_id = exc.device_id or UNKNOWN_DEVICE
                if retained:
                    # The broker replays retained status on every SUBSCRIBE,
                    # i.e. on every app start and reconnect. A malformed one
                    # was recorded when it was first published; recording it
                    # again would add a fresh rejected row each time and bump
                    # last_seen_at to "now" — making a long-dead device look
                    # online (its stored online flag is untouched here).
                    logger.debug("Ignoring retained malformed status on %r: %s", topic, exc)
                    return IngestOutcome(status=REJECTED, kind=kind, device_id=device_id, error=str(exc))
                # Recorded in telemetry_messages (seq NULL) so a misbehaving
                # device shows up in the rejected-rate KPI too.
                self._insert_message(
                    conn, device_id=device_id, boot_id=exc.boot_id, machine_id=exc.machine_id,
                    received_at=received, status=REJECTED, error=f"status: {exc}",
                    payload_bytes=_payload_size(payload),
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                )
                if exc.device_id:
                    self._touch_device(conn, exc.device_id, received)
                conn.commit()
                return IngestOutcome(status=REJECTED, kind=kind, device_id=device_id, error=str(exc))

            seen = received
            if retained:
                seen = status.reported_at or None  # None ⇒ keep the stored last_seen_at
            self._upsert_status(conn, status, seen, received)
            conn.commit()
            return IngestOutcome(status=ACCEPTED, kind=kind, device_id=status.device_id,
                                 machine_id=status.machine_id,
                                 latency_ms=(time.perf_counter() - start) * 1000.0)
        except Exception as exc:
            logger.exception("Status ingest failed for %r", topic)
            try:
                conn.rollback()
            except Exception:
                pass
            return IngestOutcome(status=ERROR, kind=kind, error=repr(exc))
        finally:
            conn.close()

    def _upsert_status(self, conn, status: DeviceStatus, seen: datetime | None, received: datetime) -> None:
        values = {name: getattr(status, name) for name in _STATUS_COLUMNS}
        if values["reported_at"] is not None:
            values["reported_at"] = protocol.format_utc(values["reported_at"])
        # A brand-new device seen only via a retained message without
        # reported_at still needs a NOT NULL last_seen_at; receive time is the
        # only honest value then.
        insert_seen = protocol.format_utc(seen or received)
        update_seen = protocol.format_utc(seen) if seen is not None else None
        assignments = ",\n".join(
            f"{name} = COALESCE(excluded.{name}, device_status.{name})" for name in _STATUS_COLUMNS
        )
        conn.execute(
            f"""INSERT INTO device_status
                    (device_id, online, last_seen_at, payload_json, {", ".join(_STATUS_COLUMNS)})
                VALUES (:device_id, :online, :insert_seen, :payload_json,
                        {", ".join(":" + name for name in _STATUS_COLUMNS)})
                ON CONFLICT(device_id) DO UPDATE SET
                    online = excluded.online,
                    -- max(): an old retained message must never move last_seen backwards.
                    last_seen_at = CASE WHEN :update_seen IS NULL THEN device_status.last_seen_at
                                        ELSE max(device_status.last_seen_at, :update_seen) END,
                    payload_json = excluded.payload_json,
                    {assignments}""",
            {
                "device_id": status.device_id,
                "online": int(status.online),
                "insert_seen": insert_seen,
                "update_seen": update_seen,
                "payload_json": json.dumps(status.raw),
                **values,
            },
        )

    # --- Shared row writers -------------------------------------------------------

    def _touch_device(self, conn, device_id: str, received: datetime, *,
                      machine_id: str | None = None, boot_id: str | None = None) -> None:
        """Bump last_seen_at for any message from a device (contract §6). A
        device first seen through a snapshot gets online = 0: per §4 only a
        status message can declare it online."""
        seen = protocol.format_utc(received)
        conn.execute(
            """INSERT INTO device_status (device_id, machine_id, boot_id, online, last_seen_at)
               VALUES (?, ?, ?, 0, ?)
               ON CONFLICT(device_id) DO UPDATE SET
                   last_seen_at = max(device_status.last_seen_at, excluded.last_seen_at),
                   machine_id = COALESCE(excluded.machine_id, device_status.machine_id),
                   boot_id = COALESCE(excluded.boot_id, device_status.boot_id)""",
            (device_id, machine_id, boot_id, seen),
        )

    def _insert_message(
        self,
        conn,
        *,
        device_id: str,
        received_at: datetime,
        status: str,
        boot_id: str | None = None,
        seq: int | None = None,
        machine_id: str | None = None,
        sampled_at: str | None = None,
        buffered: bool = False,
        time_synced: bool = True,
        error: str | None = None,
        reading_id: int | None = None,
        latency_ms: float | None = None,
        payload_bytes: int | None = None,
        ignore_conflict: bool = False,
    ) -> None:
        verb = "INSERT OR IGNORE" if ignore_conflict else "INSERT"
        conn.execute(
            f"""{verb} INTO telemetry_messages
                   (device_id, boot_id, seq, machine_id, sampled_at, received_at, buffered,
                    time_synced, status, error, reading_id, latency_ms, payload_bytes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                device_id, boot_id, seq, machine_id, sampled_at, protocol.format_utc(received_at),
                int(bool(buffered)), int(bool(time_synced)), status,
                # Bounded: a pathological payload must not bloat the audit table.
                error[:1000] if error else None,
                reading_id, latency_ms, payload_bytes,
            ),
        )
