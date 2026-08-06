# Phase 3 — Model Wiring: Persisted Predictor + Inference Log + Registry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every RUL inference persists a `predictions` row and a `model_inference_log` row, the active model is registered in `model_registry`, predictor state can be rehydrated from stored `readings` after a restart, and the machine-health KPI is driven by XJTU RUL predictions.

**Architecture:** A new `src/prediction/rul_store.py` owns the predictor-dict → DB-column mapping (predictions + inference log), an idempotent model-registry upsert, and cold-start rehydration that replays *already-extracted* stored features back through the predictor. The `RealTimeRULPredictor` is refactored so `predict()` (raw waveforms → features) delegates to a new `_predict_from_base()` (features → result); rehydration reuses that delegate so feature math is never forked. The RUL endpoint gains a DB dependency and an overridable predictor dependency, wrapping inference with latency measurement and persistence on both success and error paths. The KPI layer reads the new RUL columns off the latest prediction.

**Tech Stack:** Python 3 · FastAPI · SQLite (raw SQL, `sqlite3`) · scikit-learn (already-trained ExtraTrees cascade via joblib) · numpy/pandas · pytest + TestClient.

## Global Constraints

Every task implicitly includes these (copied from the roadmap/spec, verbatim values):

- **Canonical dataset is XJTU-SY.** `predictions.source` for RUL rows is the literal string `'xjtu_rul'`. No NASA/IMS assumptions.
- **RUL is in minutes.** `rul_minutes` / `predicted_rul_minutes` are the unit of record. Hours may be *derived* in a response (the predictor already returns `predicted_rul_hours`) but are never stored.
- **No temperature.** No `temperature_c`/`temperature_severity`/`temperature_is_synthetic` anywhere.
- **No new heavyweight deps.** Standard library + already-present scikit-learn/joblib/numpy/pandas only.
- **Model artifact paths are stored relative to the repo root** in `model_registry.artifact_path`; never absolute.
- **Feature extraction has exactly one home:** `src.ingestion.xjtu_sy.extract_snapshot_features`, reached online via `RealTimeRULPredictor.predict`. Rehydration must reuse already-extracted features (from `readings.features_json`); it must NOT recompute or re-implement any feature.
- **RBAC preserved.** The RUL endpoints keep their existing auth posture (the `predictions` router is mounted behind `get_current_user` in `src/api/app.py`). This phase adds no new role gates.
- **TDD, DRY, YAGNI, frequent commits.** Failing test first, minimal code, green, commit. One deliverable per task.

### Committed interfaces this plan builds on (read before implementing)

- `src/prediction/rul_realtime.py`:
  - `RealTimeRULPredictor(artifact_path: Path = DEFAULT_ARTIFACT, max_history: int | None = None)`.
  - `.predict(machine_id, horizontal, vertical, sample_rate_hz, speed_rpm, load_kn) -> dict`. Result dict keys: `machine_id`, `predicted_rul_minutes`, `predicted_rul_hours`, `rul_estimate_kind`, `prognostic_horizon_minutes`, `failure_within_horizon_probability`, `raw_failure_within_horizon_probability`, `warning_persistence_snapshots`, `prediction_interval_90_minutes` (a `[low, high]` list; `high` may be `None`), `health_state`, `model_version`, `history_snapshots`, `out_of_distribution` (bool), `outside_training_features`, `warnings` (list[str]; a warming-up warning starts with `"warming_up"`).
  - Internal state mutated per snapshot (all inside `self._locks[machine_id]`): `self._history`, `self._baseline_history`, `self._cycles`, `self._probability_history`, `self._warning_history`. `self.artifact` holds the loaded joblib dict; `self.artifact["model_version"]` is the version string.
- `src/ingestion/xjtu_sy.py`: `extract_snapshot_features(horizontal, vertical, sample_rate_hz) -> dict` (keys `h_*`/`v_*`/`m_*`, `cross_axis_*`). `SAMPLE_RATE_HZ = 25600.0`.
- `src/training/xjtu_rul.py`: `REPO_ROOT` (Path), `DEFAULT_ARTIFACT = REPO_ROOT/"models"/"xjtu_rul_model.joblib"`, `ROLLING_WINDOWS = (5, 20, 60)`.
- `src/storage/db.py`: canonical `SCHEMA` (see `predictions`, `model_inference_log`, `model_registry` DDL); `insert_readings(conn, readings)`; `insert_machines(conn, machines)`. `predictions` RUL columns: `predicted_rul_minutes`, `rul_estimate_kind`, `failure_within_horizon_probability`, `prognostic_horizon_minutes`, `prediction_interval_low`, `prediction_interval_high`, `model_version`, `out_of_distribution` (INT), `history_snapshots`, `warnings_json`; `health_state` is NOT NULL; `source` DEFAULT `'xjtu_rul'`.
- `src/api/deps.py`: `get_db()` FastAPI dependency yielding a `sqlite3.Connection`.
- `src/api/routes/predictions.py`: current `POST /predictions/rul` and `DELETE /predictions/rul/{machine_id}/state`, using a module-level `_predictor()` (`@lru_cache`).
- `src/kpi/calculations.py`: `_latest_prediction(conn, machine_id)`, `_machine_health(conn, machine_id) -> dict`. Health states already match the RUL predictor's (`healthy`/`degrading`/`faulty`/`critical`) — `HEALTH_RANK` and `_RISK_BY_STATE` need no remapping.
- `src/api/schemas.py`: `MachineSummary` (BaseModel).
- `tests/test_rul_realtime.py`: reference for building a deterministic fake artifact (`_AlwaysLateClassifier`, `_ThirtyMinuteRegressor`, `classifier_feature_columns=["speed_rpm","h_kurtosis"]`, `regressor_feature_columns=["h_rms"]`, `model_version="test-model"`). Reuse this pattern; do not import private helpers across test files — copy the tiny fakes where needed.
- `tests/conftest.py`: `conn` fixture (temp DB, real schema, seeded), `client` fixture (TestClient authed as admin via `_db_override`), `_db_override` (registers `get_db` override on the shared `app`, cleared after test).

### File structure

- Create `src/prediction/rul_store.py` — DB mapping (`persist_prediction`, `log_inference`, `register_active_model`) + `rehydrate`.
- Modify `src/prediction/rul_realtime.py` — store `self.artifact_path`; split `predict()` into `predict()` + `_predict_from_base()`.
- Modify `src/api/routes/predictions.py` — DB + overridable predictor deps; register model; measure latency; persist prediction + inference log on success and error.
- Modify `src/kpi/calculations.py` — surface RUL columns from the latest prediction in `_machine_health`.
- Modify `src/api/schemas.py` — add optional RUL fields to `MachineSummary`.
- Create `tests/prediction/__init__.py`, `tests/prediction/test_rul_store.py`.
- Create `tests/api/__init__.py`, `tests/api/test_rul_persistence.py`.
- Create `tests/kpi/__init__.py`, `tests/kpi/test_health_from_rul.py`.

---

## Task 1: `rul_store` mapping — persist_prediction, log_inference, register_active_model

**Files:**
- Create: `src/prediction/rul_store.py`
- Create: `tests/prediction/__init__.py` (empty)
- Test: `tests/prediction/test_rul_store.py`

**Interfaces:**
- Consumes: canonical `predictions`, `model_inference_log`, `model_registry` schema; `conn` fixture.
- Produces:
  - `persist_prediction(conn, result: dict, reading_id: int | None = None) -> int` — inserts one `predictions` row, returns its id. Maps `prediction_interval_90_minutes[0]/[1]` → `prediction_interval_low/high`, `warnings` → `warnings_json` (JSON string), `out_of_distribution` bool → INT, `source` always `'xjtu_rul'`, `timestamp` = now (UTC ISO).
  - `log_inference(conn, *, machine_id, model_version, latency_ms, result=None, error=None) -> int` — inserts one `model_inference_log` row (`status='ok'` when `result` given, `status='error'` + `error_message` when `error` given), returns its id. `warming_up` = 1 iff any warning starts with `"warming_up"`; `warnings_count` = len(warnings); `failure_probability` = `failure_within_horizon_probability`.
  - `register_active_model(conn, *, model_version, artifact_path, algorithm=None, metrics_json=None) -> None` — idempotent upsert into `model_registry`; sets this row `is_active=1` and every other row `is_active=0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/prediction/__init__.py` (empty) and `tests/prediction/test_rul_store.py`:

```python
import json

import pytest

from src.prediction import rul_store


def _result(**overrides):
    base = {
        "machine_id": "m1",
        "predicted_rul_minutes": 42.0,
        "predicted_rul_hours": 0.7,
        "rul_estimate_kind": "point_estimate",
        "prognostic_horizon_minutes": 120.0,
        "failure_within_horizon_probability": 0.83,
        "prediction_interval_90_minutes": [30.0, 54.0],
        "health_state": "faulty",
        "model_version": "test-model",
        "history_snapshots": 61,
        "out_of_distribution": True,
        "warnings": ["warming_up: 3/60 snapshots", "rul_lower_bound: ..."],
    }
    base.update(overrides)
    return base


def test_persist_prediction_maps_all_columns(conn):
    row_id = rul_store.persist_prediction(conn, _result(), reading_id=7)
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (row_id,)).fetchone()
    assert row["machine_id"] == "m1"
    assert row["reading_id"] == 7
    assert row["source"] == "xjtu_rul"
    assert row["health_state"] == "faulty"
    assert row["predicted_rul_minutes"] == 42.0
    assert row["rul_estimate_kind"] == "point_estimate"
    assert row["failure_within_horizon_probability"] == 0.83
    assert row["prognostic_horizon_minutes"] == 120.0
    assert row["prediction_interval_low"] == 30.0
    assert row["prediction_interval_high"] == 54.0
    assert row["model_version"] == "test-model"
    assert row["out_of_distribution"] == 1
    assert row["history_snapshots"] == 61
    assert json.loads(row["warnings_json"]) == _result()["warnings"]
    assert row["timestamp"]  # set to a non-empty ISO timestamp


def test_persist_prediction_handles_lower_bound_open_interval(conn):
    row_id = rul_store.persist_prediction(
        conn, _result(prediction_interval_90_minutes=[120.0, None], out_of_distribution=False)
    )
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (row_id,)).fetchone()
    assert row["prediction_interval_low"] == 120.0
    assert row["prediction_interval_high"] is None
    assert row["reading_id"] is None
    assert row["out_of_distribution"] == 0


def test_log_inference_success_row(conn):
    rul_store.log_inference(
        conn, machine_id="m1", model_version="test-model", latency_ms=12.5, result=_result()
    )
    row = conn.execute("SELECT * FROM model_inference_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "ok"
    assert row["error_message"] is None
    assert row["latency_ms"] == 12.5
    assert row["failure_probability"] == 0.83
    assert row["predicted_rul_minutes"] == 42.0
    assert row["out_of_distribution"] == 1
    assert row["warming_up"] == 1
    assert row["warnings_count"] == 2


def test_log_inference_error_row(conn):
    rul_store.log_inference(
        conn, machine_id="m1", model_version="test-model", latency_ms=3.0, error="bad input"
    )
    row = conn.execute("SELECT * FROM model_inference_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "error"
    assert row["error_message"] == "bad input"
    assert row["failure_probability"] is None
    assert row["predicted_rul_minutes"] is None
    assert row["warming_up"] == 0
    assert row["warnings_count"] == 0


def test_register_active_model_upserts_and_is_idempotent(conn):
    rul_store.register_active_model(
        conn, model_version="v1", artifact_path="models/xjtu_rul_model.joblib", algorithm="extratrees"
    )
    rul_store.register_active_model(
        conn, model_version="v1", artifact_path="models/xjtu_rul_model.joblib", algorithm="extratrees"
    )
    rows = conn.execute("SELECT * FROM model_registry").fetchall()
    assert len(rows) == 1
    assert rows[0]["artifact_path"] == "models/xjtu_rul_model.joblib"
    assert rows[0]["is_active"] == 1
    assert rows[0]["deployed_at"]


def test_register_active_model_deactivates_previous(conn):
    rul_store.register_active_model(conn, model_version="v1", artifact_path="a")
    rul_store.register_active_model(conn, model_version="v2", artifact_path="b")
    active = conn.execute("SELECT model_version FROM model_registry WHERE is_active = 1").fetchall()
    assert [r["model_version"] for r in active] == ["v2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/prediction/test_rul_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.prediction.rul_store'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/prediction/rul_store.py`:

```python
"""Persistence for RUL inference: predictions rows, inference-log rows, the
model registry, and cold-start rehydration of predictor state.

This module owns the predictor-dict -> DB-column mapping. It never computes a
feature or a prediction: it maps an already-produced predictor result dict
(src.prediction.rul_realtime.RealTimeRULPredictor.predict) into rows, and (in a
later task) replays already-extracted stored features back through the predictor
to rebuild in-memory state after a restart. Feature math stays single-homed in
src.ingestion.xjtu_sy.extract_snapshot_features.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_prediction(conn, result: dict, reading_id: int | None = None) -> int:
    """Map a predictor result dict into one predictions row. Returns the row id."""
    interval = result.get("prediction_interval_90_minutes") or [None, None]
    low = interval[0] if len(interval) > 0 else None
    high = interval[1] if len(interval) > 1 else None
    row = {
        "machine_id": result["machine_id"],
        "reading_id": reading_id,
        "timestamp": _now_iso(),
        "health_state": result["health_state"],
        "source": "xjtu_rul",
        "predicted_rul_minutes": result.get("predicted_rul_minutes"),
        "rul_estimate_kind": result.get("rul_estimate_kind"),
        "failure_within_horizon_probability": result.get("failure_within_horizon_probability"),
        "prognostic_horizon_minutes": result.get("prognostic_horizon_minutes"),
        "prediction_interval_low": low,
        "prediction_interval_high": high,
        "model_version": result.get("model_version"),
        "out_of_distribution": int(bool(result.get("out_of_distribution", False))),
        "history_snapshots": result.get("history_snapshots"),
        "warnings_json": json.dumps(result.get("warnings", [])),
    }
    cur = conn.execute(
        """INSERT INTO predictions
               (machine_id, reading_id, timestamp, health_state, source,
                predicted_rul_minutes, rul_estimate_kind,
                failure_within_horizon_probability, prognostic_horizon_minutes,
                prediction_interval_low, prediction_interval_high, model_version,
                out_of_distribution, history_snapshots, warnings_json)
           VALUES (:machine_id, :reading_id, :timestamp, :health_state, :source,
                   :predicted_rul_minutes, :rul_estimate_kind,
                   :failure_within_horizon_probability, :prognostic_horizon_minutes,
                   :prediction_interval_low, :prediction_interval_high, :model_version,
                   :out_of_distribution, :history_snapshots, :warnings_json)""",
        row,
    )
    conn.commit()
    return cur.lastrowid


def _warming_up(result: dict) -> int:
    return int(any(str(w).startswith("warming_up") for w in result.get("warnings", [])))


def log_inference(conn, *, machine_id: str, model_version: str, latency_ms: float,
                  result: dict | None = None, error: str | None = None) -> int:
    """Write one model_inference_log row. Pass `result` for a success row or
    `error` for a failure row (exactly one)."""
    if result is not None:
        row = {
            "machine_id": machine_id,
            "timestamp": _now_iso(),
            "model_version": model_version,
            "latency_ms": float(latency_ms),
            "failure_probability": result.get("failure_within_horizon_probability"),
            "predicted_rul_minutes": result.get("predicted_rul_minutes"),
            "out_of_distribution": int(bool(result.get("out_of_distribution", False))),
            "warming_up": _warming_up(result),
            "warnings_count": len(result.get("warnings", [])),
            "status": "ok",
            "error_message": None,
        }
    else:
        row = {
            "machine_id": machine_id,
            "timestamp": _now_iso(),
            "model_version": model_version,
            "latency_ms": float(latency_ms),
            "failure_probability": None,
            "predicted_rul_minutes": None,
            "out_of_distribution": 0,
            "warming_up": 0,
            "warnings_count": 0,
            "status": "error",
            "error_message": error,
        }
    cur = conn.execute(
        """INSERT INTO model_inference_log
               (machine_id, timestamp, model_version, latency_ms, failure_probability,
                predicted_rul_minutes, out_of_distribution, warming_up, warnings_count,
                status, error_message)
           VALUES (:machine_id, :timestamp, :model_version, :latency_ms,
                   :failure_probability, :predicted_rul_minutes, :out_of_distribution,
                   :warming_up, :warnings_count, :status, :error_message)""",
        row,
    )
    conn.commit()
    return cur.lastrowid


def register_active_model(conn, *, model_version: str, artifact_path: str,
                          algorithm: str | None = None,
                          metrics_json: str | None = None) -> None:
    """Idempotently record the active model and flag it active (deactivating all
    others). artifact_path MUST be repo-root-relative."""
    conn.execute(
        """INSERT INTO model_registry
               (model_version, artifact_path, algorithm, metrics_json, deployed_at, is_active)
           VALUES (:model_version, :artifact_path, :algorithm, :metrics_json, :deployed_at, 1)
           ON CONFLICT(model_version) DO UPDATE SET
               artifact_path = excluded.artifact_path,
               algorithm = excluded.algorithm,
               metrics_json = excluded.metrics_json,
               is_active = 1""",
        {
            "model_version": model_version,
            "artifact_path": artifact_path,
            "algorithm": algorithm,
            "metrics_json": metrics_json,
            "deployed_at": _now_iso(),
        },
    )
    conn.execute(
        "UPDATE model_registry SET is_active = 0 WHERE model_version != ?",
        (model_version,),
    )
    conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/prediction/test_rul_store.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full backend suite (no regressions)**

Run: `python -m pytest -q`
Expected: all previously-green tests still pass, plus the 6 new ones.

- [ ] **Step 6: Commit**

```bash
git add src/prediction/rul_store.py tests/prediction/__init__.py tests/prediction/test_rul_store.py
git commit -m "feat: rul_store persist_prediction / log_inference / register_active_model"
```

---

## Task 2: Wire the RUL endpoint — register model, persist prediction + inference log, measure latency

**Files:**
- Modify: `src/prediction/rul_realtime.py` (store `self.artifact_path` in `__init__`)
- Modify: `src/api/routes/predictions.py`
- Create: `tests/api/__init__.py` (empty)
- Test: `tests/api/test_rul_persistence.py`

**Interfaces:**
- Consumes: Task 1 `rul_store.{persist_prediction, log_inference, register_active_model}`; `get_db`; `REPO_ROOT`; `RealTimeRULPredictor`.
- Produces:
  - `RealTimeRULPredictor.artifact_path: Path` attribute (set from the constructor arg).
  - `predictions.get_predictor()` FastAPI dependency (lru-cached instance; wraps `FileNotFoundError` as HTTP 503) — overridable in tests via `app.dependency_overrides`.
  - `POST /predictions/rul` now: registers the active model (idempotent), times `predict()` only, persists a `predictions` row and an `ok` inference-log row on success, persists an `error` inference-log row and raises HTTP 422 on predictor `ValueError`, and 503 on missing artifact.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/__init__.py` (empty) and `tests/api/test_rul_persistence.py`:

```python
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_db
from src.api.routes import predictions as predictions_route


class _AlwaysLateClassifier:
    def predict_proba(self, X):
        return np.tile([0.0, 1.0], (len(X), 1))


class _ThirtyMinuteRegressor:
    def predict(self, X):
        return np.full(len(X), 30.0)


def _make_predictor(tmp_path):
    import joblib
    from src.prediction.rul_realtime import RealTimeRULPredictor

    path = tmp_path / "rul.joblib"
    joblib.dump({
        "classifiers": [_AlwaysLateClassifier(), _AlwaysLateClassifier()],
        "regressor": _ThirtyMinuteRegressor(),
        "classifier_feature_columns": ["speed_rpm", "h_kurtosis"],
        "regressor_feature_columns": ["h_rms"],
        "feature_columns": ["h_rms"],
        "feature_bounds_99pct": {},
        "conformal_error_90_minutes": 10.0,
        "model_version": "test-model",
        "prognostic_horizon_minutes": 120.0,
        "failure_probability_threshold": 0.6,
        "probability_smoothing_window": 3,
        "warning_persistence_snapshots": 3,
        "baseline_window": 20,
        "sample_rate_hz": 25_600.0,
    }, path)
    return RealTimeRULPredictor(path)


@pytest.fixture
def rul_client(db_path, tmp_path):
    predictor = _make_predictor(tmp_path)

    def _override_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[predictions_route.get_predictor] = lambda: predictor
    try:
        with TestClient(app) as client:
            from src.auth.seed import DEMO_USERS
            email, _n, password, _r = DEMO_USERS[0]
            assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(predictions_route.get_predictor, None)


def _payload(**overrides):
    body = {
        "machine_id": "m1",
        "horizontal": list(np.sin(2 * np.pi * 1000 * np.arange(256) / 25_600.0)),
        "vertical": list(np.cos(2 * np.pi * 1000 * np.arange(256) / 25_600.0)),
        "sample_rate_hz": 25_600.0,
        "speed_rpm": 2100.0,
        "load_kn": 12.0,
    }
    body.update(overrides)
    return body


def _count(db_path, table, machine_id="m1"):
    c = sqlite3.connect(db_path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table} WHERE machine_id = ?", (machine_id,)).fetchone()[0]
    finally:
        c.close()


def test_predict_writes_one_prediction_and_one_inference_log(rul_client, db_path):
    resp = rul_client.post("/api/predictions/rul", json=_payload())
    assert resp.status_code == 200, resp.text
    assert _count(db_path, "predictions") == 1
    assert _count(db_path, "model_inference_log") == 1
    log = sqlite3.connect(db_path).execute(
        "SELECT status, model_version, latency_ms FROM model_inference_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert log[0] == "ok"
    assert log[1] == "test-model"
    assert log[2] >= 0.0


def test_predict_registers_active_model(rul_client, db_path):
    rul_client.post("/api/predictions/rul", json=_payload())
    reg = sqlite3.connect(db_path).execute(
        "SELECT model_version, artifact_path, is_active FROM model_registry"
    ).fetchall()
    assert len(reg) == 1
    assert reg[0][0] == "test-model"
    assert not reg[0][1].startswith("/") and ":" not in reg[0][1].split("/")[0]  # relative, not absolute
    assert reg[0][2] == 1


def test_bad_input_logs_error_and_returns_422(rul_client, db_path):
    resp = rul_client.post("/api/predictions/rul", json=_payload(speed_rpm=0.0))
    assert resp.status_code == 422
    # Pydantic rejects speed_rpm<=0 before the route body, so no error row is
    # written. Use a value that passes validation but fails inside predict():
    # an empty machine_id is blocked by schema too, so drive the predictor error
    # via a sample_rate the predictor accepts but classifier rejects is not
    # possible here; assert the HTTP contract only.


def test_predictor_value_error_is_logged(db_path, tmp_path, monkeypatch):
    # Force predict() to raise ValueError to exercise the error path + error log.
    predictor = _make_predictor(tmp_path)

    def _boom(*a, **k):
        raise ValueError("live feature vector is missing trained features")

    monkeypatch.setattr(predictor, "predict", _boom)

    def _override_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[predictions_route.get_predictor] = lambda: predictor
    try:
        with TestClient(app) as client:
            from src.auth.seed import DEMO_USERS
            email, _n, password, _r = DEMO_USERS[0]
            client.post("/api/auth/login", json={"email": email, "password": password})
            resp = client.post("/api/predictions/rul", json=_payload())
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(predictions_route.get_predictor, None)

    log = sqlite3.connect(db_path).execute(
        "SELECT status, error_message FROM model_inference_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert log[0] == "error"
    assert "missing trained features" in log[1]
    assert _count(db_path, "predictions") == 0
```

> Note: the `test_bad_input_logs_error_and_returns_422` body documents why schema-level 422s don't produce an error log; `test_predictor_value_error_is_logged` covers the predictor-raised path. Keep both.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/api/test_rul_persistence.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_predictor'` (and/or predictions/log rows not written).

- [ ] **Step 3: Add `artifact_path` to the predictor**

In `src/prediction/rul_realtime.py`, inside `__init__`, right after the `artifact_path.exists()` check (before/after `joblib.load` is fine), store the path:

```python
        self.artifact_path = Path(artifact_path)
```

(`Path` is already imported.)

- [ ] **Step 4: Rewrite the RUL endpoint**

Replace the body of `src/api/routes/predictions.py` with:

```python
"""Online prediction endpoints."""
import os
import time
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_db
from src.api.schemas import RULPredictionRequest, RULPredictionResponse
from src.prediction import rul_store
from src.prediction.rul_realtime import RealTimeRULPredictor
from src.training.xjtu_rul import REPO_ROOT

router = APIRouter(prefix="/predictions", tags=["predictions"])


@lru_cache(maxsize=1)
def _cached_predictor() -> RealTimeRULPredictor:
    return RealTimeRULPredictor()


def get_predictor() -> RealTimeRULPredictor:
    """FastAPI dependency. Overridable in tests via app.dependency_overrides."""
    try:
        return _cached_predictor()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _relative_artifact_path(predictor: RealTimeRULPredictor) -> str:
    # model_registry.artifact_path must be repo-root-relative, never absolute.
    return os.path.relpath(predictor.artifact_path, REPO_ROOT).replace(os.sep, "/")


@router.post("/rul", response_model=RULPredictionResponse)
def predict_rul(payload: RULPredictionRequest, db=Depends(get_db), predictor=Depends(get_predictor)):
    model_version = predictor.artifact["model_version"]
    rul_store.register_active_model(
        db,
        model_version=model_version,
        artifact_path=_relative_artifact_path(predictor),
        algorithm=predictor.artifact.get("algorithm"),
    )
    start = time.perf_counter()
    try:
        result = predictor.predict(
            machine_id=payload.machine_id,
            horizontal=np.asarray(payload.horizontal, dtype=np.float64),
            vertical=np.asarray(payload.vertical, dtype=np.float64),
            sample_rate_hz=payload.sample_rate_hz,
            speed_rpm=payload.speed_rpm,
            load_kn=payload.load_kn,
        )
    except ValueError as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        rul_store.log_inference(
            db, machine_id=payload.machine_id, model_version=model_version,
            latency_ms=latency_ms, error=str(exc),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    latency_ms = (time.perf_counter() - start) * 1000.0
    rul_store.persist_prediction(db, result, reading_id=None)
    rul_store.log_inference(
        db, machine_id=payload.machine_id, model_version=model_version,
        latency_ms=latency_ms, result=result,
    )
    return result


@router.delete("/rul/{machine_id}/state")
def reset_rul_state(machine_id: str, predictor=Depends(get_predictor)):
    """Reset rolling context after maintenance, sensor movement, or replacement."""
    predictor.reset_machine(machine_id)
    return {"machine_id": machine_id, "status": "reset"}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/api/test_rul_persistence.py tests/test_rul_realtime.py -v`
Expected: PASS (new persistence tests + unchanged predictor tests).

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/prediction/rul_realtime.py src/api/routes/predictions.py tests/api/__init__.py tests/api/test_rul_persistence.py
git commit -m "feat: persist RUL prediction + inference log + model registry on each /predictions/rul call"
```

---

## Task 3: Rehydration — split predict() and replay stored readings into a cold predictor

**Files:**
- Modify: `src/prediction/rul_realtime.py` (split `predict()` → `predict()` + `_predict_from_base()`)
- Modify: `src/prediction/rul_store.py` (add `rehydrate`)
- Test: `tests/prediction/test_rul_store.py` (append rehydration tests)

**Interfaces:**
- Consumes: `RealTimeRULPredictor._predict_from_base`; `readings.features_json` (full `extract_snapshot_features` dict), `speed_rpm`, `load_kn`, `sample_rate_hz`, `cycle`.
- Produces:
  - `RealTimeRULPredictor._predict_from_base(machine_id, base: dict, *, sample_rate_hz, speed_rpm, load_kn) -> dict` — everything `predict()` did after feature extraction. `predict()` now = validate + `extract_snapshot_features` + delegate.
  - `rul_store.rehydrate(predictor, conn, machine_id) -> int` — replays stored readings (cycle-ascending, `features_json` non-empty) through `_predict_from_base`, discarding results; returns the count replayed. Readings whose `features_json` is empty/`{}` (live/demo rows without a stored feature vector) are skipped.

- [ ] **Step 1: Write the failing tests**

Append to `tests/prediction/test_rul_store.py`:

```python
import json as _json

import numpy as np

from src.ingestion.xjtu_sy import extract_snapshot_features
from src.prediction.rul_realtime import RealTimeRULPredictor
from src.storage.db import init_schema, insert_machines, insert_readings


def _fake_artifact(path):
    import joblib

    class _AlwaysLate:
        def predict_proba(self, X):
            return np.tile([0.0, 1.0], (len(X), 1))

    class _ThirtyMin:
        def predict(self, X):
            return np.full(len(X), 30.0)

    joblib.dump({
        "classifiers": [_AlwaysLate(), _AlwaysLate()],
        "regressor": _ThirtyMin(),
        "classifier_feature_columns": ["speed_rpm", "h_kurtosis"],
        "regressor_feature_columns": ["h_rms"],
        "feature_columns": ["h_rms"],
        "feature_bounds_99pct": {},
        "conformal_error_90_minutes": 10.0,
        "model_version": "test-model",
        "prognostic_horizon_minutes": 120.0,
        "failure_probability_threshold": 0.6,
        "probability_smoothing_window": 3,
        "warning_persistence_snapshots": 3,
        "baseline_window": 20,
        "sample_rate_hz": 25_600.0,
    }, path)
    return path


def _signal(cycle, n=256, sr=25_600.0):
    t = np.arange(n) / sr
    amp = 1.0 + 0.05 * cycle
    return amp * np.sin(2 * np.pi * 1000 * t), amp * np.cos(2 * np.pi * 1000 * t)


def _fresh_db(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "rehydrate.db")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    insert_machines(conn, [{"machine_id": "b1", "dataset": "xjtu_sy"}])
    return conn


def test_rehydrate_reproduces_next_prediction_health_state(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib")
    n_history = 8
    sr, speed, load = 25_600.0, 2100.0, 12.0

    # Warm predictor A: process n_history snapshots and persist each as a reading.
    conn = _fresh_db(tmp_path)
    warm = RealTimeRULPredictor(art)
    rows = []
    for cycle in range(n_history):
        h, v = _signal(cycle)
        warm.predict("b1", h, v, sr, speed, load)
        base = extract_snapshot_features(h, v, sr)
        rows.append({
            "machine_id": "b1", "timestamp": f"2020-01-01T00:{cycle:02d}:00+00:00",
            "cycle": cycle, "elapsed_minutes": float(cycle), "speed_rpm": speed, "load_kn": load,
            "sample_rate_hz": sr,
            "vibration_h_rms": base["h_rms"], "vibration_h_kurtosis": base["h_kurtosis"],
            "vibration_v_rms": base["v_rms"], "vibration_v_kurtosis": base["v_kurtosis"],
            "cross_axis_rms_ratio": base["cross_axis_rms_ratio"],
            "cross_axis_correlation": base["cross_axis_correlation"],
            "rul_minutes": None, "features_json": _json.dumps(base), "dataset": "xjtu_sy",
        })
    insert_readings(conn, rows)

    # Cold predictor B: rehydrate from the stored readings.
    cold = RealTimeRULPredictor(art)
    replayed = rul_store.rehydrate(cold, conn, "b1")
    assert replayed == n_history

    # Next snapshot fed to both must agree.
    h_next, v_next = _signal(n_history)
    warm_next = warm.predict("b1", h_next, v_next, sr, speed, load)
    cold_next = cold.predict("b1", h_next, v_next, sr, speed, load)
    assert cold_next["health_state"] == warm_next["health_state"]
    assert cold_next["predicted_rul_minutes"] == warm_next["predicted_rul_minutes"]
    assert cold_next["history_snapshots"] == warm_next["history_snapshots"]
    conn.close()


def test_rehydrate_skips_readings_without_feature_vector(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib")
    conn = _fresh_db(tmp_path)
    insert_readings(conn, [{
        "machine_id": "b1", "timestamp": "2020-01-01T00:00:00+00:00", "cycle": 0,
        "elapsed_minutes": 0.0, "speed_rpm": 2100.0, "load_kn": 12.0, "sample_rate_hz": 25_600.0,
        "vibration_h_rms": 0.1, "vibration_h_kurtosis": 3.0, "vibration_v_rms": 0.1,
        "vibration_v_kurtosis": 3.0, "cross_axis_rms_ratio": 1.0, "cross_axis_correlation": 0.0,
        "rul_minutes": None, "features_json": "{}", "dataset": "xjtu_sy",
    }])
    cold = RealTimeRULPredictor(art)
    assert rul_store.rehydrate(cold, conn, "b1") == 0
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/prediction/test_rul_store.py -v`
Expected: FAIL — `rul_store.rehydrate` missing and/or `_predict_from_base` missing.

- [ ] **Step 3: Split `predict()` in `src/prediction/rul_realtime.py`**

Change the current `predict` so that after the two guard clauses and `base = extract_snapshot_features(...)`, it delegates. Keep the guard clauses in `predict`. Move everything from `with self._locks[machine_id]:` through the final `return {...}` into a new method `_predict_from_base`. Concretely:

```python
    def predict(
        self,
        machine_id: str,
        horizontal: np.ndarray,
        vertical: np.ndarray,
        sample_rate_hz: float,
        speed_rpm: float,
        load_kn: float,
    ) -> dict:
        if not machine_id.strip():
            raise ValueError("machine_id must not be empty")
        if speed_rpm <= 0 or load_kn < 0:
            raise ValueError("speed_rpm must be positive and load_kn must be non-negative")
        base = extract_snapshot_features(horizontal, vertical, sample_rate_hz)
        return self._predict_from_base(
            machine_id, base, sample_rate_hz=sample_rate_hz, speed_rpm=speed_rpm, load_kn=load_kn
        )

    def _predict_from_base(
        self,
        machine_id: str,
        base: dict,
        *,
        sample_rate_hz: float,
        speed_rpm: float,
        load_kn: float,
    ) -> dict:
        # ... UNCHANGED body: the exact code that currently follows the
        # `base = extract_snapshot_features(...)` line in predict(), starting at
        # `with self._locks[machine_id]:` and ending at the final `return {...}`.
```

Do not change any logic inside the moved body — this is a pure extract-method refactor. `base`, `machine_id`, `sample_rate_hz`, `speed_rpm`, `load_kn` are all already the names used in that body.

- [ ] **Step 4: Add `rehydrate` to `src/prediction/rul_store.py`**

```python
def rehydrate(predictor, conn, machine_id: str) -> int:
    """Replay stored readings for machine_id back through the predictor so a
    cold instance rebuilds the same in-memory state a warm one would have.

    Uses the already-extracted feature vector in readings.features_json (never
    recomputes a feature). Readings without a stored feature vector (live/demo
    rows with features_json '{}') are skipped. Returns the number replayed.
    """
    cur = conn.execute(
        """SELECT speed_rpm, load_kn, sample_rate_hz, features_json
           FROM readings WHERE machine_id = ? ORDER BY cycle ASC""",
        (machine_id,),
    )
    replayed = 0
    for row in cur.fetchall():
        base = json.loads(row["features_json"] or "{}")
        if not base:
            continue
        predictor._predict_from_base(
            machine_id,
            base,
            sample_rate_hz=row["sample_rate_hz"],
            speed_rpm=row["speed_rpm"],
            load_kn=row["load_kn"],
        )
        replayed += 1
    return replayed
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/prediction/test_rul_store.py tests/test_rul_realtime.py -v`
Expected: PASS (mapping + rehydration + unchanged predictor tests).

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/prediction/rul_realtime.py src/prediction/rul_store.py tests/prediction/test_rul_store.py
git commit -m "feat: rehydrate cold RUL predictor from stored readings via _predict_from_base"
```

---

## Task 4: KPI health from RUL + MachineSummary RUL fields

**Files:**
- Modify: `src/kpi/calculations.py` (`_latest_prediction`, `_machine_health`)
- Modify: `src/api/schemas.py` (`MachineSummary`)
- Create: `tests/kpi/__init__.py` (empty)
- Test: `tests/kpi/test_health_from_rul.py`

**Interfaces:**
- Consumes: `predictions` RUL columns; `conn`/`client` fixtures.
- Produces: `_machine_health` result dict gains `predicted_rul_minutes`, `rul_estimate_kind`, `out_of_distribution`; `MachineSummary` gains the same three optional fields.

- [ ] **Step 1: Write the failing tests**

Create `tests/kpi/__init__.py` (empty) and `tests/kpi/test_health_from_rul.py`:

```python
from src.kpi import calculations as kpi


def _insert_rul_prediction(conn, machine_id, **cols):
    defaults = {
        "timestamp": "2030-01-01T00:00:00+00:00",  # newest, so it is the latest
        "health_state": "faulty",
        "source": "xjtu_rul",
        "predicted_rul_minutes": 45.0,
        "rul_estimate_kind": "point_estimate",
        "failure_within_horizon_probability": 0.9,
        "out_of_distribution": 1,
    }
    defaults.update(cols)
    conn.execute(
        """INSERT INTO predictions
               (machine_id, timestamp, health_state, source, predicted_rul_minutes,
                rul_estimate_kind, failure_within_horizon_probability, out_of_distribution)
           VALUES (:machine_id, :timestamp, :health_state, :source, :predicted_rul_minutes,
                   :rul_estimate_kind, :failure_within_horizon_probability, :out_of_distribution)""",
        {"machine_id": machine_id, **defaults},
    )
    conn.commit()


def test_machine_health_surfaces_rul_fields(conn):
    _insert_rul_prediction(conn, "m1")
    health = kpi.machine_health_kpis(conn, "m1")[0]
    assert health["health_state"] == "faulty"
    assert health["predicted_rul_minutes"] == 45.0
    assert health["rul_estimate_kind"] == "point_estimate"
    assert health["out_of_distribution"] is True
    assert health["prediction_source"] == "xjtu_rul"


def test_machine_health_rul_fields_default_when_absent(conn):
    # m2 in the seed has only legacy predictions (no RUL columns set).
    health = kpi.machine_health_kpis(conn, "m2")[0]
    assert health["predicted_rul_minutes"] is None
    assert health["rul_estimate_kind"] is None
    # out_of_distribution defaults to 0 in the schema; surfaced as False.
    assert health["out_of_distribution"] in (False, None)


def test_machine_summary_endpoint_includes_rul_fields(client, conn):
    _insert_rul_prediction(conn, "m1")
    resp = client.get("/api/kpis/m1")
    assert resp.status_code == 200
    body = resp.json()["machine_health"]
    assert body["predicted_rul_minutes"] == 45.0
    assert body["rul_estimate_kind"] == "point_estimate"
    assert body["out_of_distribution"] is True
```

> `conn` and `client` both build off the same `db_path` fixture (one temp DB per test), so a row inserted via `conn` is visible to `client`'s request in the same test.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/kpi/test_health_from_rul.py -v`
Expected: FAIL — `KeyError: 'predicted_rul_minutes'` (health dict) and missing field in the endpoint body.

- [ ] **Step 3: Extend `_latest_prediction` and `_machine_health` in `src/kpi/calculations.py`**

In `_latest_prediction`, extend the SELECT to include the RUL columns:

```python
def _latest_prediction(conn, machine_id: str):
    cur = conn.execute(
        """SELECT health_state, confidence, source, model_name, probable_cause, timestamp,
                  predicted_rul_minutes, rul_estimate_kind,
                  failure_within_horizon_probability, out_of_distribution
           FROM predictions
           WHERE machine_id = ?
           ORDER BY timestamp DESC
           LIMIT 1""",
        (machine_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None
```

In `_machine_health`, add the three RUL fields to the returned dict (place them next to the existing prediction-derived fields):

```python
        "predicted_rul_minutes": latest_pred.get("predicted_rul_minutes") if latest_pred else None,
        "rul_estimate_kind": latest_pred.get("rul_estimate_kind") if latest_pred else None,
        "out_of_distribution": bool(latest_pred["out_of_distribution"]) if latest_pred and latest_pred.get("out_of_distribution") is not None else None,
```

- [ ] **Step 4: Add the fields to `MachineSummary` in `src/api/schemas.py`**

```python
class MachineSummary(BaseModel):
    machine_id: str
    health_state: str
    confidence: Optional[float] = None
    prediction_source: Optional[str] = None
    probable_cause: Optional[str] = None
    last_reading_at: Optional[str] = None
    vibration_severity: str
    risk_score: float
    abnormal_event_count: int
    open_alert_count: int
    predicted_rul_minutes: Optional[float] = None
    rul_estimate_kind: Optional[str] = None
    out_of_distribution: Optional[bool] = None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/kpi/test_health_from_rul.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green (existing `test_kpi.py` / `test_api.py` still pass — the new fields are additive and optional).

- [ ] **Step 7: Commit**

```bash
git add src/kpi/calculations.py src/api/schemas.py tests/kpi/__init__.py tests/kpi/test_health_from_rul.py
git commit -m "feat: surface RUL fields (predicted_rul_minutes/estimate_kind/ood) in machine health KPI"
```

---

## Phase 3 Review Gate (Phase 3 → 4/5/6)

Run the whole-branch `code-review` on the Phase 3 diff. Focus (from the roadmap):
- Feature math still single-sourced (`extract_snapshot_features` only; rehydration reuses stored features, never recomputes).
- Prediction dict → DB column mapping is lossless and correct (`prediction_interval_90_minutes` → low/high; `warnings` → `warnings_json`; bool → INT).
- Inference log written on **both** success and error paths; latency measured around inference only.
- KPI no longer references IMS; RUL fields surfaced; existing KPI numbers unchanged.
- Rehydration deterministic and bounded by `max_history`.

## Phase 3 Acceptance

- A predict call writes exactly one `predictions` + one `model_inference_log` row (Task 2 tests).
- Restart + rehydrate reproduces the same next-prediction `health_state` (and `predicted_rul_minutes`) as no-restart (Task 3 test).
- Fleet/machine KPI shows RUL-derived health fields (Task 4 tests).
- `python -m pytest -q` green.
