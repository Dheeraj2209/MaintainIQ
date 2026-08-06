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

Note: replay does not register the model (model_registry is populated by the
/predictions/rul endpoint); log_inference only needs the model_version string,
which the predictor result already carries. Registration is a model-lifecycle
concern, not part of the predict+persist path replay reuses.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from src.prediction import rul_store

_WORKLIST_SQL = """
    SELECT id, cycle, speed_rpm, load_kn, sample_rate_hz, features_json
    FROM readings
    WHERE machine_id = ? AND features_json IS NOT NULL AND features_json != '{}'
    ORDER BY cycle ASC
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReplayService:
    def __init__(self, *, predictor_provider, connection_factory,
                 base_interval_seconds: float = 1.0):
        self._predictor_provider = predictor_provider
        self._connection_factory = connection_factory
        self._base_interval_seconds = base_interval_seconds
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
            rul_store.persist_prediction(conn, result, reading_id=row["id"])
            rul_store.log_inference(
                conn, machine_id=machine_id,
                model_version=result["model_version"],
                latency_ms=latency_ms, result=result,
            )
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
