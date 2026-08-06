# Phase 4 — Streaming Replay Service + Ingestion Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay already-ingested `readings` snapshots back through the live predict+persist path at a controllable rate, controlled by `POST /api/ingestion/replay/start|stop` and `GET /api/ingestion/replay/status`.

**Architecture:** A `ReplayService` owns per-machine in-memory worklists (readings ordered by cycle) and drives each stored snapshot's already-extracted feature vector through `predictor._predict_from_base` + `rul_store.persist_prediction` + `rul_store.log_inference` — the exact functions a live `/predictions/rul` call uses, so no prediction row is ever hand-written. A background daemon thread per machine paces replay via a stop `Event`; a synchronous `replay_once` core makes the loop deterministically unit-testable. REST endpoints wrap the service; start/stop are supervisor+, status is any authenticated role.

**Tech Stack:** Python 3, FastAPI, `threading` (Thread + Event), raw `sqlite3`, pytest + TestClient.

## Global Constraints

Copied verbatim from the master roadmap (`docs/superpowers/plans/2026-08-06-xjtu-sy-ml-integration-roadmap.md`). Every task's requirements implicitly include this section.

- Canonical dataset is **XJTU-SY**; no NASA/IMS references in schema, model, or new code.
- RUL is expressed in **minutes** (never cycles-as-RUL, never hours as the stored unit).
- Feature math has a **single home**: `src.ingestion.xjtu_sy.extract_snapshot_features`. Replay **reuses stored `readings.features_json`** and never recomputes a feature (mirrors `rul_store.rehydrate`).
- **No new heavyweight dependencies.** `threading` and `sqlite3` are stdlib; nothing new is added.
- **`src/storage/db.py` MUST NOT be modified** (canonical schema is frozen for this phase).
- Model artifact paths in the registry are **repo-root-relative** (unchanged; replay does not register models — see Task 1 note).
- **RBAC roles:** admin / supervisor / operator. **Ingestion control (start/stop) is supervisor+**; status is any authenticated role. Enforced with `src.auth.deps.require_role`.
- TDD / DRY / YAGNI; frequent commits.
- A code-review gate runs on the whole Phase 4 diff before advancing to Phase 5.

**Phase-4-specific binding constraints:**

- Each replayed snapshot goes through the **SAME predict+persist path as a live call** (`predictor._predict_from_base` → `rul_store.persist_prediction` → `rul_store.log_inference`). **No shortcut writes** (no direct `INSERT INTO predictions`).
- Thread/task lifecycle must be **clean**: `stop` halts the loop and joins the thread; no leaked loops survive a test. Each worker thread owns its **own** SQLite connection (connections are not shared across threads).
- WebSocket broadcast of replayed snapshots is **deferred** (roadmap: "if wired"). Phase 4 does not broadcast; the frontend (Phase 7) reads replay progress via `GET .../status` and the predictions already persisted. This is an explicit YAGNI decision — do not add `manager.broadcast` calls in this phase.

**Out of scope (deferred, documented):** The Phase 3 review's top follow-up — making the online `/predictions/rul` path persist a `readings` row with a real `features_json` so a live-only-driven machine is rehydratable — is **not** part of Phase 4. Replay operates on **batch-backfilled** readings, which already carry real feature vectors and are rehydratable; closing the `/rul`-write gap requires changing the predictor's return contract (to surface `base`) and is orthogonal to the replay feature. It stays deferred to a later hardening pass.

---

## File Structure

**Task 1 — replay engine (no HTTP):**
- Create: `src/ingestion/replay_service.py` — `ReplayService`: per-machine worklist + cursor + status, `replay_once` (synchronous core), `start`/`stop`/`stop_all` (thread lifecycle), `status`.
- Test: `tests/ingestion/test_replay_service.py` — unit tests with a fake predictor and the temp DB.

**Task 2 — ingestion control endpoints:**
- Create: `src/api/routes/ingestion.py` — `router` (prefix `/ingestion`) with `start`/`stop`/`status`, plus `get_replay_service` dependency (overridable in tests).
- Modify: `src/api/schemas.py` — add `ReplayStartRequest`, `ReplayStopRequest`.
- Modify: `src/api/app.py` — import `ingestion` and add it to the `get_current_user`-gated router loop.
- Test: `tests/api/test_ingestion_control.py` — RBAC + end-to-end replay via TestClient. (`tests/api/conftest.py` already clears `predictions`, so replay-written row counts are clean.)

`tests/ingestion/__init__.py` and `tests/api/__init__.py` already exist — do not recreate them.

---

## Task 1: ReplayService (replay engine)

**Files:**
- Create: `src/ingestion/replay_service.py`
- Test: `tests/ingestion/test_replay_service.py`

**Interfaces:**
- Consumes: `src.prediction.rul_store.persist_prediction(conn, result, reading_id)` and `rul_store.log_inference(conn, *, machine_id, model_version, latency_ms, result)` (committed). A predictor object exposing `_predict_from_base(machine_id, base, *, sample_rate_hz, speed_rpm, load_kn) -> dict` (the committed `RealTimeRULPredictor` method; tests inject a fake with the same signature). The `db_path` fixture (temp DB) from `tests/conftest.py`.
- Produces:
  - `ReplayService(*, predictor_provider, connection_factory, base_interval_seconds=1.0)` — `predictor_provider: () -> predictor`, `connection_factory: () -> sqlite3.Connection`.
  - `replay_once(machine_id: str) -> bool` — process the next queued snapshot; `True` if one was processed, `False` if exhausted.
  - `start(machine_id: str, speed_multiplier: float = 1.0) -> None` — raises `ValueError` on non-positive multiplier or a machine with no replayable readings.
  - `stop(machine_id: str) -> bool`, `stop_all() -> None`.
  - `status() -> dict[str, dict]` — `{machine_id: {"cycle": int|None, "running": bool, "last_ts": str|None, "replayed": int}}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/test_replay_service.py`:

```python
import sqlite3
import time

import pytest

from src.ingestion.replay_service import ReplayService


class _FakePredictor:
    """Stand-in for RealTimeRULPredictor. Records calls and returns a result
    dict shaped exactly like _predict_from_base's output, so the real
    rul_store.persist_prediction / log_inference mapping runs unchanged."""

    def __init__(self):
        self.calls = []

    def _predict_from_base(self, machine_id, base, *, sample_rate_hz, speed_rpm, load_kn):
        self.calls.append((machine_id, dict(base), sample_rate_hz, speed_rpm, load_kn))
        return {
            "machine_id": machine_id,
            "predicted_rul_minutes": 42.0,
            "predicted_rul_hours": 0.7,
            "rul_estimate_kind": "point_estimate",
            "prognostic_horizon_minutes": 720.0,
            "failure_within_horizon_probability": 0.9,
            "raw_failure_within_horizon_probability": 0.9,
            "warning_persistence_snapshots": 1,
            "prediction_interval_90_minutes": [30.0, 54.0],
            "health_state": "faulty",
            "model_version": "test-model-v1",
            "history_snapshots": len(self.calls),
            "out_of_distribution": False,
            "outside_training_features": [],
            "warnings": [],
        }


def _row_conn(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _service(db_path, predictor, **kwargs):
    return ReplayService(
        predictor_provider=lambda: predictor,
        connection_factory=lambda: _row_conn(db_path),
        **kwargs,
    )


def test_replay_once_routes_through_predict_and_persist(db_path):
    pred = _FakePredictor()
    svc = _service(db_path, pred)
    # seed m1 has two readings, each with a non-empty features_json.
    assert svc.replay_once("m1") is True
    assert len(pred.calls) == 1
    # feature vector came from readings.features_json (never recomputed).
    _mid, base, sr, speed, load = pred.calls[0]
    assert base == {"vibration_h_high_band_energy_ratio": 0.1}
    assert sr == 25600.0 and speed == 2100.0 and load == 12.0

    conn = _row_conn(db_path)
    try:
        pred_rows = conn.execute(
            "SELECT source, model_version, predicted_rul_minutes, reading_id "
            "FROM predictions WHERE machine_id='m1' AND source='xjtu_rul'"
        ).fetchall()
        log_rows = conn.execute(
            "SELECT status FROM model_inference_log WHERE machine_id='m1'"
        ).fetchall()
    finally:
        conn.close()
    assert len(pred_rows) == 1
    assert pred_rows[0]["source"] == "xjtu_rul"          # same source a live /rul write uses
    assert pred_rows[0]["model_version"] == "test-model-v1"
    assert pred_rows[0]["predicted_rul_minutes"] == 42.0
    assert pred_rows[0]["reading_id"] is not None         # linked to the stored reading
    assert len(log_rows) == 1 and log_rows[0]["status"] == "ok"


def test_replay_once_exhausts_after_all_snapshots(db_path):
    pred = _FakePredictor()
    svc = _service(db_path, pred)
    assert svc.replay_once("m1") is True   # cycle 0
    assert svc.replay_once("m1") is True   # cycle 1
    assert svc.replay_once("m1") is False  # exhausted (seed has 2 readings)
    assert len(pred.calls) == 2
    st = svc.status()["m1"]
    assert st["replayed"] == 2
    assert st["cycle"] == 1                # last processed reading's cycle


def test_start_sets_running_then_stop_halts(db_path):
    pred = _FakePredictor()
    # long interval: the loop parks in stop_event.wait() and cannot finish on its own.
    svc = _service(db_path, pred, base_interval_seconds=10.0)
    svc.start("m1")
    assert svc.status()["m1"]["running"] is True
    svc.stop("m1")
    assert svc.status()["m1"]["running"] is False
    assert svc._threads.get("m1") is None   # thread cleaned up — no leaked loop


def test_start_replays_all_then_marks_not_running(db_path):
    pred = _FakePredictor()
    svc = _service(db_path, pred, base_interval_seconds=0.001)
    svc.start("m1")
    deadline = time.time() + 5.0
    while svc.status()["m1"]["running"] and time.time() < deadline:
        time.sleep(0.02)
    svc.stop_all()
    st = svc.status()["m1"]
    assert st["running"] is False
    assert st["replayed"] == 2
    conn = _row_conn(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM predictions "
            "WHERE machine_id='m1' AND source='xjtu_rul'"
        ).fetchone()["c"]
    finally:
        conn.close()
    assert n == 2


def test_start_unknown_machine_raises(db_path):
    svc = _service(db_path, _FakePredictor())
    with pytest.raises(ValueError):
        svc.start("does-not-exist")


def test_start_rejects_nonpositive_speed(db_path):
    svc = _service(db_path, _FakePredictor())
    with pytest.raises(ValueError):
        svc.start("m1", speed_multiplier=0)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ingestion/test_replay_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.ingestion.replay_service'`.

- [ ] **Step 3: Implement `src/ingestion/replay_service.py`**

```python
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
        return {"cycle": None, "running": False, "last_ts": None, "replayed": 0}

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/ingestion/test_replay_service.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green (129 prior + 6 new = 135). No existing test regresses (Task 1 adds a new module only).

- [ ] **Step 6: Commit**

```bash
git add src/ingestion/replay_service.py tests/ingestion/test_replay_service.py
git commit -m "feat: ReplayService drives stored readings through the live predict+persist path"
```

---

## Task 2: Ingestion control endpoints

**Files:**
- Create: `src/api/routes/ingestion.py`
- Modify: `src/api/schemas.py` (append `ReplayStartRequest`, `ReplayStopRequest`)
- Modify: `src/api/app.py` (import + mount `ingestion` under `get_current_user`)
- Test: `tests/api/test_ingestion_control.py`

**Interfaces:**
- Consumes: `ReplayService` (Task 1); `src.auth.deps.require_role`; `src.storage.db.get_connection`; the `auth_client`/`anon_client`/`db_path` fixtures; `tests/api/conftest.py` (clears `predictions`).
- Produces:
  - `get_replay_service() -> ReplayService` (FastAPI dependency; overridable via `app.dependency_overrides`).
  - `POST /api/ingestion/replay/start` (supervisor+) — body `{"machine_id": str, "speed_multiplier": float=1.0}` → `{"machine_id", "status": "started", "speed_multiplier"}`; `404` for a machine with no replayable readings.
  - `POST /api/ingestion/replay/stop` (supervisor+) — body `{"machine_id": str}` → `{"machine_id", "status": "stopped"}`.
  - `GET /api/ingestion/replay/status` (any authenticated role) → the `ReplayService.status()` dict.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_ingestion_control.py`:

```python
import sqlite3
import time

import pytest

from src.ingestion.replay_service import ReplayService


class _FakePredictor:
    def __init__(self):
        self.calls = []

    def _predict_from_base(self, machine_id, base, *, sample_rate_hz, speed_rpm, load_kn):
        self.calls.append(machine_id)
        return {
            "machine_id": machine_id,
            "predicted_rul_minutes": 42.0,
            "predicted_rul_hours": 0.7,
            "rul_estimate_kind": "point_estimate",
            "prognostic_horizon_minutes": 720.0,
            "failure_within_horizon_probability": 0.9,
            "raw_failure_within_horizon_probability": 0.9,
            "warning_persistence_snapshots": 1,
            "prediction_interval_90_minutes": [30.0, 54.0],
            "health_state": "faulty",
            "model_version": "test-model-v1",
            "history_snapshots": len(self.calls),
            "out_of_distribution": False,
            "outside_training_features": [],
            "warnings": [],
        }


def _row_conn(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def replay_svc(db_path):
    """Override get_replay_service with a service bound to the temp DB and a
    fake predictor; always stop threads on teardown (no leaked loops)."""
    from src.api.app import app
    from src.api.routes.ingestion import get_replay_service

    pred = _FakePredictor()
    svc = ReplayService(
        predictor_provider=lambda: pred,
        connection_factory=lambda: _row_conn(db_path),
        base_interval_seconds=0.001,
    )
    app.dependency_overrides[get_replay_service] = lambda: svc
    yield svc
    svc.stop_all()
    app.dependency_overrides.pop(get_replay_service, None)


def test_operator_cannot_start(auth_client, replay_svc):
    client = auth_client("operator")
    resp = client.post("/api/ingestion/replay/start", json={"machine_id": "m1"})
    assert resp.status_code == 403


def test_operator_cannot_stop(auth_client, replay_svc):
    client = auth_client("operator")
    resp = client.post("/api/ingestion/replay/stop", json={"machine_id": "m1"})
    assert resp.status_code == 403


def test_supervisor_can_start_and_stop(auth_client, replay_svc):
    client = auth_client("supervisor")
    resp = client.post("/api/ingestion/replay/start", json={"machine_id": "m1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "started"
    resp = client.post("/api/ingestion/replay/stop", json={"machine_id": "m1"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


def test_status_visible_to_operator(auth_client, replay_svc):
    client = auth_client("operator")
    resp = client.get("/api/ingestion/replay/status")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_status_requires_auth(anon_client, replay_svc):
    resp = anon_client.get("/api/ingestion/replay/status")
    assert resp.status_code == 401


def test_start_unknown_machine_returns_404(auth_client, replay_svc):
    client = auth_client("supervisor")
    resp = client.post("/api/ingestion/replay/start", json={"machine_id": "ghost"})
    assert resp.status_code == 404


def test_replay_produces_predictions_and_status_advances(auth_client, replay_svc, db_path):
    supervisor = auth_client("supervisor")
    resp = supervisor.post(
        "/api/ingestion/replay/start",
        json={"machine_id": "m1", "speed_multiplier": 1.0},
    )
    assert resp.status_code == 200, resp.text

    # Poll status until the background replay drains the two seed readings.
    deadline = time.time() + 5.0
    status = {}
    while time.time() < deadline:
        status = supervisor.get("/api/ingestion/replay/status").json()
        m1 = status.get("m1", {})
        if m1.get("running") is False and m1.get("replayed") == 2:
            break
        time.sleep(0.02)

    supervisor.post("/api/ingestion/replay/stop", json={"machine_id": "m1"})
    assert status["m1"]["replayed"] == 2
    assert status["m1"]["cycle"] == 1

    # Replayed snapshots wrote predictions rows via the same persist path a live
    # /predictions/rul call uses (source 'xjtu_rul'), linked to the stored reading.
    conn = _row_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT source, model_version, reading_id FROM predictions "
            "WHERE machine_id='m1' AND source='xjtu_rul'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert all(r["source"] == "xjtu_rul" and r["reading_id"] is not None for r in rows)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/api/test_ingestion_control.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_replay_service'` (module `src.api.routes.ingestion` does not exist yet).

- [ ] **Step 3: Add request schemas to `src/api/schemas.py`**

Append after the `RULPredictionResponse` class (end of file). `BaseModel` and `Field` are already imported at the top of the module:

```python
class ReplayStartRequest(BaseModel):
    machine_id: str = Field(min_length=1, max_length=128)
    speed_multiplier: float = Field(default=1.0, gt=0)


class ReplayStopRequest(BaseModel):
    machine_id: str = Field(min_length=1, max_length=128)
```

- [ ] **Step 4: Create `src/api/routes/ingestion.py`**

```python
"""Streaming replay ingestion-control endpoints (Phase 4).

Drives stored readings back through the live predict+persist path via
src.ingestion.replay_service.ReplayService. start/stop are supervisor+
(ingestion control); status is any authenticated role (router mounted under
get_current_user in src/api/app.py). The service is a process singleton,
resolved via get_replay_service so tests can override it.
"""
from fastapi import APIRouter, Depends, HTTPException

from src.api.schemas import ReplayStartRequest, ReplayStopRequest
from src.auth.deps import require_role
from src.ingestion.replay_service import ReplayService
from src.storage.db import get_connection

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_service: ReplayService | None = None


def _default_predictor():
    # Imported lazily so importing this route module does not build the predictor
    # cache; the FileNotFoundError (no trained model) then surfaces only when a
    # replay actually runs, not at app import.
    from src.api.routes.predictions import _cached_predictor

    return _cached_predictor()


def get_replay_service() -> ReplayService:
    """FastAPI dependency. Overridable in tests via app.dependency_overrides."""
    global _service
    if _service is None:
        _service = ReplayService(
            predictor_provider=_default_predictor,
            connection_factory=get_connection,
        )
    return _service


@router.post("/replay/start")
def start_replay(
    payload: ReplayStartRequest,
    _user=Depends(require_role("admin", "supervisor")),
    svc: ReplayService = Depends(get_replay_service),
):
    try:
        svc.start(payload.machine_id, payload.speed_multiplier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "machine_id": payload.machine_id,
        "status": "started",
        "speed_multiplier": payload.speed_multiplier,
    }


@router.post("/replay/stop")
def stop_replay(
    payload: ReplayStopRequest,
    _user=Depends(require_role("admin", "supervisor")),
    svc: ReplayService = Depends(get_replay_service),
):
    svc.stop(payload.machine_id)
    return {"machine_id": payload.machine_id, "status": "stopped"}


@router.get("/replay/status")
def replay_status(svc: ReplayService = Depends(get_replay_service)):
    return svc.status()
```

- [ ] **Step 5: Mount the router in `src/api/app.py`**

Add `ingestion` to the routes import (keep alphabetical) and to the `get_current_user`-gated loop.

Change the import block:

```python
from src.api.routes import (
    alerts,
    auth,
    demo,
    ingestion,
    kpis,
    machines,
    maintenance,
    notifications,
    predictions,
    users,
)
```

Change the mounting loop:

```python
for module in (machines, alerts, maintenance, kpis, predictions, ingestion):
    app.include_router(module.router, prefix="/api", dependencies=[Depends(get_current_user)])
```

- [ ] **Step 6: Run the ingestion-control tests to verify they pass**

Run: `python -m pytest tests/api/test_ingestion_control.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green (135 from Task 1 + 7 new = 142). No existing test regresses (endpoints are additive; the new router mounts alongside the existing ones).

- [ ] **Step 8: Commit**

```bash
git add src/api/routes/ingestion.py src/api/schemas.py src/api/app.py tests/api/test_ingestion_control.py
git commit -m "feat: ingestion replay control endpoints (start/stop supervisor+, status any role)"
```

---

## Phase 4 Review Gate (Phase 4 → 5)

Run the whole-branch `code-review` on the Phase 4 diff (`ea221c7..HEAD`) on the most capable model. Focus (from the roadmap):

- **Thread/task lifecycle:** `stop`/`stop_all` set the Event and `join` the thread; `_run` always clears `running` in a `finally`; no worker thread leaks past a test. Confirm the `stop_event.wait(interval)` pacing exits promptly on stop and does not busy-spin.
- **Predict+persist reuse / no shortcut writes:** replay routes every snapshot through `predictor._predict_from_base` → `rul_store.persist_prediction` → `rul_store.log_inference`; there is no direct `INSERT INTO predictions`. Feature vectors come from stored `readings.features_json` (single feature home; nothing recomputed).
- **WebSocket broadcast contract:** confirm Phase 4 intentionally does **not** broadcast (deferred per Global Constraints); if any broadcast were added it must match the existing `src.realtime.manager.manager.broadcast(event: dict)` contract.
- **RBAC:** start/stop reject `operator` (403); status allows any authenticated role and rejects anon (401).
- **Constraints:** `src/storage/db.py` untouched; no new deps; RUL in minutes; no IMS/NASA references introduced.

## Phase 4 Acceptance

- `start` → `status` shows the machine `running` with an advancing `cycle`; `stop` halts it (`running` False, thread joined). (Task 1 + Task 2 tests.)
- Replayed snapshots produce `predictions` rows indistinguishable from live: `source='xjtu_rul'`, `model_version` set, `predicted_rul_minutes` present, `reading_id` linked to the stored reading, each paired with one `ok` `model_inference_log` row. (Task 1 + Task 2 tests.)
- `python -m pytest -q` green.

---

## Self-Review

**1. Spec coverage.** Roadmap Phase 4 requires: `src/ingestion/replay_service.py` with `start`/`stop`/`status` (Task 1 ✅); endpoints `POST /api/ingestion/replay/start|stop`, `GET /api/ingestion/replay/status` (Task 2 ✅); tests `tests/ingestion/test_replay_service.py` + `tests/api/test_ingestion_control.py` (both ✅); each replayed snapshot through the same predict+persist path (Task 1 Step 3 ✅); RBAC start/stop supervisor+, status any role (Task 2 ✅). Acceptance criteria mapped above.

**2. Placeholder scan.** No TBD/TODO; every code step carries complete code; every command carries expected output.

**3. Type consistency.** `ReplayService(*, predictor_provider, connection_factory, base_interval_seconds=1.0)`, `replay_once(machine_id)->bool`, `start(machine_id, speed_multiplier=1.0)`, `stop(machine_id)->bool`, `stop_all()`, `status()->dict` are used identically in Task 1 tests, Task 2 route, and Task 2 fixture. The result-dict keys the fake predictor returns match exactly what `rul_store.persist_prediction`/`log_inference` read (`machine_id`, `health_state`, `predicted_rul_minutes`, `rul_estimate_kind`, `failure_within_horizon_probability`, `prognostic_horizon_minutes`, `prediction_interval_90_minutes`, `model_version`, `out_of_distribution`, `history_snapshots`, `warnings`). `get_replay_service` is the exact override key used by the Task 2 fixture.
