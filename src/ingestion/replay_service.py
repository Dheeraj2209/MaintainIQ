"""Background replay of stored readings through the live predict+persist path.

Phase 4 streaming ingestion. A ReplayService drives already-ingested readings
(readings.features_json, extracted once by
src.ingestion.xjtu_sy.extract_snapshot_features) back through the SAME predictor
+ persistence path a live /predictions/rul call uses — it never recomputes a
feature and never writes prediction rows by hand. Each replayed snapshot
produces one predictions row and one model_inference_log row via
src.prediction.rul_store, indistinguishable from a live prediction.

State (per-machine worklist cursor + status) is in memory, mirroring the
academic predictor's in-memory design. Each worker thread owns its own SQLite
connection (SQLite connections are not shared across threads).

Each replayed prediction is then fanned out through src.prediction.pipeline —
alert state machine, email paging, realtime broadcast — so a model prediction
has the same operational consequences as any other. Without that step the
model would run invisibly: prediction rows accumulate while no alert is ever
raised and nobody is told a machine is failing.

Replay and live MQTT ingest (src/telemetry/ingest.py) must never drive the
same machine at once. Both feed the one RealTimeRULPredictor, whose rolling
window, baseline and cycle counter are keyed by machine_id alone, so
interleaving old replayed snapshots with new live ones would corrupt the live
RUL and health state; and both run the alert state machine, so a "healthy"
replayed prediction would resolve an alert a live one just opened (and page
again when the next live one reopens it). Hence two guards: live readings
(dataset 'live_mqtt') are never part of a replay worklist — they already have
their prediction — and start() refuses a machine that has received live
telemetry recently.

Note: replay does not register the model (model_registry is populated by the
/predictions/rul endpoint); log_inference only needs the model_version string,
which the predictor result already carries. Registration is a model-lifecycle
concern, not part of the predict+persist path replay reuses.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from src.prediction import pipeline, rul_store

logger = logging.getLogger(__name__)

# Alerts raised from replayed predictions carry the same `source` as the
# prediction rows themselves (rul_store.PREDICTION_SOURCE), so an alert can be
# traced back to the model run that raised it.
REPLAY_SOURCE = rul_store.PREDICTION_SOURCE

# readings.dataset written by live MQTT ingest (src/telemetry/ingest.py
# LIVE_DATASET; not imported, to keep replay free of the paho-side package).
LIVE_DATASET = "live_mqtt"

# A machine that got a live reading within this long is treated as "being fed
# live" and cannot be replayed. Generous on purpose: a device in a network
# outage is still live, it is just buffering at the edge.
LIVE_GUARD = timedelta(minutes=10)

_WORKLIST_SQL = """
    SELECT id, cycle, speed_rpm, load_kn, sample_rate_hz, features_json
    FROM readings
    WHERE machine_id = ? AND features_json IS NOT NULL AND features_json != '{}'
      AND dataset != 'live_mqtt'
    ORDER BY cycle ASC
"""

_LAST_LIVE_SQL = "SELECT MAX(timestamp) FROM readings WHERE machine_id = ? AND dataset = ?"


class LiveMachineError(ValueError):
    """Raised by start() for a machine currently fed by live MQTT telemetry."""


def _parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_fan_out(conn, result: dict, features: dict, *, prediction_id=None, reading_id=None) -> dict:
    """Production fan-out: alerts + email + realtime, off the same connection
    the prediction was written on. The ids link an opened alert to the
    replayed reading and its prediction row."""
    return pipeline.handle_prediction(conn, result, source=REPLAY_SOURCE, features=features,
                                      prediction_id=prediction_id, reading_id=reading_id)


class ReplayService:
    def __init__(self, *, predictor_provider, connection_factory,
                 base_interval_seconds: float = 1.0, on_prediction=None):
        self._predictor_provider = predictor_provider
        self._connection_factory = connection_factory
        self._base_interval_seconds = base_interval_seconds
        # Injectable so tests can observe the fan-out without an SMTP server
        # or an event loop; production uses the default below.
        self._on_prediction = on_prediction or _default_fan_out
        self._worklists: dict[str, list[dict]] = {}
        self._cursors: dict[str, int] = {}
        self._state: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def _load_worklist(self, machine_id: str) -> list[dict]:
        conn = self._connection_factory()
        try:
            cur = conn.execute(_WORKLIST_SQL, (machine_id,))
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def _last_live_reading(self, machine_id: str):
        conn = self._connection_factory()
        try:
            row = conn.execute(_LAST_LIVE_SQL, (machine_id, LIVE_DATASET)).fetchone()
        finally:
            conn.close()
        return _parse_iso(row[0]) if row is not None else None

    def _fresh_state(self) -> dict:
        return {"cycle": None, "running": False, "last_ts": None, "replayed": 0, "error": None}

    def replay_once(self, machine_id: str) -> bool:
        """Process the next queued snapshot through the predict+persist path.
        Returns True if a snapshot was processed, False if exhausted."""
        with self._lock:
            if machine_id not in self._worklists:
                self._worklists[machine_id] = self._load_worklist(machine_id)
                self._cursors[machine_id] = 0
                self._state.setdefault(machine_id, self._fresh_state())
            worklist = self._worklists[machine_id]
            cursor = self._cursors[machine_id]
            if cursor >= len(worklist):
                return False
            row = worklist[cursor]
            self._cursors[machine_id] = cursor + 1

        base = json.loads(row["features_json"] or "{}")
        predictor = self._predictor_provider()
        start = time.perf_counter()
        result = predictor._predict_from_base(
            machine_id, base,
            sample_rate_hz=row["sample_rate_hz"],
            speed_rpm=row["speed_rpm"],
            load_kn=row["load_kn"],
        )
        latency_ms = (time.perf_counter() - start) * 1000.0

        conn = self._connection_factory()
        try:
            prediction_id = rul_store.persist_prediction(conn, result, reading_id=row["id"])
            rul_store.log_inference(
                conn, machine_id=machine_id,
                model_version=result["model_version"],
                latency_ms=latency_ms, result=result,
            )
            # The prediction row above is the durable record; alerting, email
            # and the realtime feed are consequences of it. A failure in any
            # of them is logged and dropped rather than allowed to stall the
            # replay loop or cost us the prediction we just made.
            try:
                self._on_prediction(conn, result, base,
                                    prediction_id=prediction_id, reading_id=row["id"])
            except Exception:
                logger.exception("Alert fan-out failed for %s", machine_id)
        finally:
            conn.close()

        with self._lock:
            st = self._state[machine_id]
            st["cycle"] = row["cycle"]
            st["last_ts"] = _now_iso()
            st["replayed"] = st.get("replayed", 0) + 1
        return True

    def start(self, machine_id: str, speed_multiplier: float = 1.0) -> None:
        if speed_multiplier <= 0:
            raise ValueError("speed_multiplier must be positive")
        with self._lock:
            existing = self._threads.get(machine_id)
            if existing is not None and existing.is_alive():
                return  # already running; idempotent
            last_live = self._last_live_reading(machine_id)
            if last_live is not None and datetime.now(timezone.utc) - last_live < LIVE_GUARD:
                raise LiveMachineError(
                    f"machine {machine_id} is receiving live telemetry (last reading "
                    f"{last_live.isoformat()}); replay would corrupt its live predictions and alerts"
                )
            # Fresh worklist each start so a re-start replays from the beginning.
            worklist = self._load_worklist(machine_id)
            if not worklist:
                raise ValueError(f"no replayable readings for machine {machine_id}")
            self._worklists[machine_id] = worklist
            self._cursors[machine_id] = 0
            state = self._fresh_state()
            state["running"] = True
            self._state[machine_id] = state
            stop_event = threading.Event()
            self._stops[machine_id] = stop_event
            interval = self._base_interval_seconds / speed_multiplier
            thread = threading.Thread(
                target=self._run, args=(machine_id, interval, stop_event),
                name=f"replay-{machine_id}", daemon=True,
            )
            self._threads[machine_id] = thread
            thread.start()

    def _run(self, machine_id: str, interval: float, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                if not self.replay_once(machine_id):
                    break
                if stop_event.wait(interval):
                    break
        except Exception as exc:  # worker-thread failure must be observable, not silent
            with self._lock:
                if machine_id in self._state:
                    self._state[machine_id]["error"] = repr(exc)
        finally:
            with self._lock:
                if machine_id in self._state:
                    self._state[machine_id]["running"] = False

    def stop(self, machine_id: str) -> bool:
        with self._lock:
            stop_event = self._stops.get(machine_id)
            thread = self._threads.get(machine_id)
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=5.0)
        with self._lock:
            if machine_id in self._state:
                self._state[machine_id]["running"] = False
            self._threads.pop(machine_id, None)
            self._stops.pop(machine_id, None)
        return thread is not None

    def stop_all(self) -> None:
        with self._lock:
            machine_ids = list(self._threads.keys())
        for machine_id in machine_ids:
            self.stop(machine_id)

    def status(self) -> dict:
        with self._lock:
            return {mid: dict(st) for mid, st in self._state.items()}
