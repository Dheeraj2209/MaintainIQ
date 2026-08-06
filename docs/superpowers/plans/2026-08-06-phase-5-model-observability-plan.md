# Phase 5 — Model Observability (Heartbeat + Telemetry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the RUL model's heartbeat and self-telemetry as two authenticated, read-only endpoints computed from the already-persisted `model_inference_log` + `model_registry` tables.

**Architecture:** Two pure, connection-taking functions in a new `src/observability/` package — `compute_health` (heartbeat) and `compute_telemetry` (rolling aggregates) — do all the work in SQL where possible; percentiles are computed in Python from a single ordered latency query (SQLite has no `PERCENTILE_CONT`). A thin new router `src/api/routes/model.py` reads the configurable window (env for heartbeat, query param for telemetry), passes an explicit `now` for determinism, and returns the function output. No new model, no new dependency, `db.py` untouched.

**Tech Stack:** Python 3 · FastAPI · SQLite (raw SQL, no ORM) · pytest + TestClient. Standard library only (`datetime`, `math`, `os`).

## Global Constraints

Every task implicitly includes these. Copied verbatim from the roadmap's Global Constraints section (the ones that bind Phase 5).

- **Canonical dataset is XJTU-SY.** No NASA/IMS assumptions in schema, model, or UI.
- **Drop synthetic temperature entirely.** No `temperature_c`, `temperature_is_synthetic`, or temperature severity anywhere.
- **RUL is in minutes.** `rul_minutes` is the unit of record (hours may still be *derived* in a response for display).
- **No new heavyweight deps.** scikit-learn/joblib/numpy/pandas/scipy only for ML; no PyTorch/TF. No new frontend chart lib beyond Recharts.
- **Model artifact paths are stored relative** to the repo root in `model_registry.artifact_path`; never absolute.
- **RBAC preserved:** admin/supervisor/operator. Report generation and ingestion control are supervisor+; read endpoints follow existing auth rules. (Phase 5's two endpoints are read-only observability → any authenticated user, mounted under `Depends(get_current_user)` like the other API routers.)
- **TDD, DRY, YAGNI, frequent commits.** Failing test first, minimal code, green, commit. One deliverable per task.
- **Code-review gate between phases (user mandate, verbatim):** "before integrating any two modules use the code review plugin or any relevant plugins then review then integrate them."

**Phase-5-specific review focus (roadmap):** aggregates computed in SQL where possible; percentiles correct; heartbeat window configurable; no PII in telemetry.

**Acceptance (roadmap):** with N logged inferences, telemetry counts/percentiles match a hand-computed fixture; health flips to stale after the window with no new inference.

## Committed-code facts this plan is authored against (HEAD = d7745a8)

- **`model_inference_log`** columns (`src/storage/db.py:94-108`): `id, machine_id, timestamp, model_version, latency_ms, failure_probability, predicted_rul_minutes, out_of_distribution (INT 0/1), warming_up (INT 0/1), warnings_count, status ('ok'|'error'), error_message, created_at`. Rows are written by `src/prediction/rul_store.py:log_inference` — success rows have `status='ok'`; failure rows have `status='error'`, `out_of_distribution=0`, `warming_up=0`. Every row (ok or error) has a non-NULL `latency_ms`. `timestamp` is `datetime.now(timezone.utc).isoformat()` (e.g. `2026-08-06T12:00:00.123456+00:00`), so string comparison against another `+00:00` ISO string orders correctly.
- **`model_registry`** columns (`src/storage/db.py:112-120`): `model_version (PK), artifact_path, algorithm, trained_at, metrics_json, deployed_at, is_active (INT 0/1)`. `register_active_model` sets exactly one row `is_active=1`.
- **Route/mount pattern:** routers live in `src/api/routes/<name>.py` with `router = APIRouter(prefix="/<name>", tags=[...])`; `src/api/app.py:47-48` mounts a tuple of modules with `prefix="/api", dependencies=[Depends(get_current_user)]`. `get_db` (`src/api/deps.py`) yields one `sqlite3.Connection` (row_factory = Row) per request.
- **Test fixtures (`tests/conftest.py`):** `conn` (Row-factory connection to a temp DB), `client` (TestClient pre-authed as admin), `auth_client(role)` factory, `anon_client` (no cookie). The temp DB has empty `model_inference_log` and empty `model_registry` — observability tests insert their own rows.

---

### Task 1: Observability compute functions (`model_health` + `telemetry`)

**Files:**
- Create: `src/observability/__init__.py` (empty)
- Create: `src/observability/model_health.py`
- Create: `src/observability/telemetry.py`
- Create: `tests/observability/__init__.py` (empty)
- Test: `tests/observability/test_model_health.py`

**Interfaces:**
- Consumes: a `sqlite3.Connection` (Row factory) over the real schema; `model_inference_log`, `model_registry`.
- Produces (relied on by Task 2):
  - `src.observability.model_health.compute_health(conn, *, now: datetime, stale_after_seconds: float) -> dict` returning keys `status` (`"healthy"|"stale"`), `model_version` (str|None), `last_inference_at` (str|None), `seconds_since_last_inference` (float|None), `active` (bool). Also exports `DEFAULT_STALE_AFTER_SECONDS = 900.0`.
  - `src.observability.telemetry.compute_telemetry(conn, *, now: datetime, window_minutes: float) -> dict` returning keys `window_minutes` (float), `inference_count` (int), `error_rate` (float), `latency_p50_ms` (float|None), `latency_p95_ms` (float|None), `ood_rate` (float), `warming_up_rate` (float). Also exports `DEFAULT_WINDOW_MINUTES = 60.0` and `_percentile(sorted_values: list[float], pct: float) -> float | None`.

**Design decisions (locked so the reviewer and Task 2 see the same contract):**
- **Percentile method = nearest-rank on an ascending-sorted list.** For `n` values and percentile `p` (0–100): `rank = ceil(p/100 * n)`, `index = min(max(rank, 1), n) - 1`, return `sorted_values[index]`. `n == 0` → `None`. This is deterministic and hand-verifiable (no interpolation), which the acceptance fixture depends on.
- **Denominators:** `error_rate = errors / total`; `ood_rate` and `warming_up_rate` are over **successful** rows only (`sum(...) / ok_count`) because error rows force those flags to 0 and would dilute the signal. All three rates are `0.0` when their denominator is 0.
- **Latency percentiles are over ALL rows in the window** (ok and error) — every row records the time spent up to its outcome.
- **Windowing:** `cutoff = (now - timedelta(minutes=window_minutes)).isoformat()`; SQL filters `WHERE timestamp >= :cutoff`. Health's "last successful inference" filters `status = 'ok'` and is NOT window-bounded (we always report the true last heartbeat and let `status` decide freshness).
- `now` is injected (never `datetime.now()` inside the functions) so tests are deterministic.

- [ ] **Step 1: Create the empty package markers**

Create `src/observability/__init__.py` with a one-line docstring:

```python
"""Model observability: heartbeat + rolling inference telemetry (Phase 5)."""
```

Create `tests/observability/__init__.py` as an empty file (no content).

- [ ] **Step 2: Write the failing tests**

Create `tests/observability/test_model_health.py`:

```python
"""Unit tests for the observability compute functions (Phase 5 Task 1)."""
from datetime import datetime, timedelta, timezone

from src.observability import model_health, telemetry

NOW = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _log(conn, *, status="ok", latency_ms=10.0, ood=0, warming=0,
         minutes_ago=5.0, model_version="v1"):
    conn.execute(
        """INSERT INTO model_inference_log
               (machine_id, timestamp, model_version, latency_ms, failure_probability,
                predicted_rul_minutes, out_of_distribution, warming_up, warnings_count,
                status, error_message)
           VALUES ('m1', :ts, :mv, :lat, NULL, NULL, :ood, :warming, 0, :status, :err)""",
        {
            "ts": _iso(NOW - timedelta(minutes=minutes_ago)),
            "mv": model_version,
            "lat": latency_ms,
            "ood": ood,
            "warming": warming,
            "status": status,
            "err": None if status == "ok" else "boom",
        },
    )
    conn.commit()


def _register(conn, model_version="v1"):
    conn.execute(
        """INSERT INTO model_registry (model_version, artifact_path, is_active)
           VALUES (?, 'models/xjtu_rul.joblib', 1)""",
        (model_version,),
    )
    conn.commit()


# ---- telemetry ----

def _seed_telemetry_fixture(conn):
    # 5 rows inside a 60-min window + 1 old row outside it.
    _log(conn, status="ok", latency_ms=10.0, ood=0, warming=0, minutes_ago=10)
    _log(conn, status="ok", latency_ms=20.0, ood=1, warming=0, minutes_ago=11)
    _log(conn, status="ok", latency_ms=30.0, ood=0, warming=1, minutes_ago=12)
    _log(conn, status="ok", latency_ms=40.0, ood=1, warming=1, minutes_ago=13)
    _log(conn, status="error", latency_ms=50.0, minutes_ago=14)
    _log(conn, status="ok", latency_ms=9999.0, ood=1, warming=1, minutes_ago=120)  # outside window


def test_telemetry_counts_and_rates_match_hand_computed(conn):
    _seed_telemetry_fixture(conn)
    t = telemetry.compute_telemetry(conn, now=NOW, window_minutes=60.0)
    assert t["window_minutes"] == 60.0
    assert t["inference_count"] == 5           # the old row is excluded
    assert t["error_rate"] == 0.2              # 1 error / 5
    assert t["ood_rate"] == 0.5                # 2 ood / 4 ok
    assert t["warming_up_rate"] == 0.5         # 2 warming / 4 ok


def test_telemetry_percentiles_nearest_rank(conn):
    _seed_telemetry_fixture(conn)
    t = telemetry.compute_telemetry(conn, now=NOW, window_minutes=60.0)
    # sorted latencies in window: [10, 20, 30, 40, 50]
    # p50: ceil(0.50*5)=3 -> index 2 -> 30 ; p95: ceil(0.95*5)=5 -> index 4 -> 50
    assert t["latency_p50_ms"] == 30.0
    assert t["latency_p95_ms"] == 50.0


def test_telemetry_empty_window_is_zeroed(conn):
    t = telemetry.compute_telemetry(conn, now=NOW, window_minutes=60.0)
    assert t["inference_count"] == 0
    assert t["error_rate"] == 0.0
    assert t["ood_rate"] == 0.0
    assert t["warming_up_rate"] == 0.0
    assert t["latency_p50_ms"] is None
    assert t["latency_p95_ms"] is None


def test_percentile_helper_edges():
    assert telemetry._percentile([], 50) is None
    assert telemetry._percentile([42.0], 50) == 42.0
    assert telemetry._percentile([42.0], 95) == 42.0
    assert telemetry._percentile([10.0, 20.0], 50) == 10.0   # ceil(1.0)=1 -> idx 0
    assert telemetry._percentile([10.0, 20.0], 95) == 20.0   # ceil(1.9)=2 -> idx 1


# ---- health ----

def test_health_healthy_within_window(conn):
    _register(conn, "v1")
    _log(conn, status="ok", minutes_ago=5.0, model_version="v1")  # 300s ago
    h = model_health.compute_health(conn, now=NOW, stale_after_seconds=900.0)
    assert h["status"] == "healthy"
    assert h["model_version"] == "v1"
    assert h["active"] is True
    assert h["last_inference_at"] == _iso(NOW - timedelta(minutes=5.0))
    assert h["seconds_since_last_inference"] == 300.0


def test_health_stale_when_last_inference_older_than_window(conn):
    _register(conn, "v1")
    _log(conn, status="ok", minutes_ago=5.0)  # 300s ago
    h = model_health.compute_health(conn, now=NOW, stale_after_seconds=120.0)
    assert h["status"] == "stale"              # 300 > 120
    assert h["seconds_since_last_inference"] == 300.0


def test_health_ignores_error_rows_for_heartbeat(conn):
    _register(conn, "v1")
    _log(conn, status="error", minutes_ago=1.0)   # recent, but a failure
    h = model_health.compute_health(conn, now=NOW, stale_after_seconds=900.0)
    assert h["last_inference_at"] is None          # no successful inference
    assert h["seconds_since_last_inference"] is None
    assert h["status"] == "stale"


def test_health_no_registry_no_inferences(conn):
    h = model_health.compute_health(conn, now=NOW, stale_after_seconds=900.0)
    assert h["status"] == "stale"
    assert h["model_version"] is None
    assert h["active"] is False
    assert h["last_inference_at"] is None
    assert h["seconds_since_last_inference"] is None
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/observability/test_model_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.observability.model_health'` (and `.telemetry`).

- [ ] **Step 4: Implement `src/observability/telemetry.py`**

```python
"""Rolling telemetry aggregates over model_inference_log.

Counts and rates are computed in SQL; latency percentiles are computed in
Python (SQLite has no PERCENTILE_CONT) from a single ordered query using the
nearest-rank method. All aggregates are windowed by `now - window_minutes`.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

DEFAULT_WINDOW_MINUTES = 60.0

_AGG_SQL = """
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
        SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
        SUM(CASE WHEN status = 'ok' THEN out_of_distribution ELSE 0 END) AS ood_sum,
        SUM(CASE WHEN status = 'ok' THEN warming_up ELSE 0 END) AS warming_sum
    FROM model_inference_log
    WHERE timestamp >= :cutoff
"""

_LATENCY_SQL = """
    SELECT latency_ms FROM model_inference_log
    WHERE timestamp >= :cutoff
    ORDER BY latency_ms ASC
"""


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile over an ascending-sorted list. None if empty."""
    n = len(sorted_values)
    if n == 0:
        return None
    rank = math.ceil(pct / 100.0 * n)
    index = min(max(rank, 1), n) - 1
    return sorted_values[index]


def compute_telemetry(conn, *, now: datetime, window_minutes: float) -> dict:
    cutoff = (now - timedelta(minutes=window_minutes)).isoformat()
    agg = conn.execute(_AGG_SQL, {"cutoff": cutoff}).fetchone()
    total = agg["total"] or 0
    errors = agg["errors"] or 0
    ok_count = agg["ok_count"] or 0
    ood_sum = agg["ood_sum"] or 0
    warming_sum = agg["warming_sum"] or 0

    latencies = [r["latency_ms"] for r in conn.execute(_LATENCY_SQL, {"cutoff": cutoff}).fetchall()]

    return {
        "window_minutes": window_minutes,
        "inference_count": total,
        "error_rate": (errors / total) if total else 0.0,
        "latency_p50_ms": _percentile(latencies, 50.0),
        "latency_p95_ms": _percentile(latencies, 95.0),
        "ood_rate": (ood_sum / ok_count) if ok_count else 0.0,
        "warming_up_rate": (warming_sum / ok_count) if ok_count else 0.0,
    }
```

- [ ] **Step 5: Implement `src/observability/model_health.py`**

```python
"""Model heartbeat: is a successful inference recent enough to call the model live?

Reports the true last successful inference and the active registered model; the
`status` field ('healthy'/'stale') is decided against a configurable window so a
caller can tune sensitivity without changing what is measured.
"""
from __future__ import annotations

from datetime import datetime

DEFAULT_STALE_AFTER_SECONDS = 900.0

_LAST_OK_SQL = """
    SELECT MAX(timestamp) AS last_ts FROM model_inference_log WHERE status = 'ok'
"""

_ACTIVE_MODEL_SQL = """
    SELECT model_version FROM model_registry WHERE is_active = 1 LIMIT 1
"""


def compute_health(conn, *, now: datetime, stale_after_seconds: float) -> dict:
    last_row = conn.execute(_LAST_OK_SQL).fetchone()
    last_ts = last_row["last_ts"] if last_row else None

    active_row = conn.execute(_ACTIVE_MODEL_SQL).fetchone()
    model_version = active_row["model_version"] if active_row else None

    seconds_since = None
    if last_ts is not None:
        seconds_since = (now - datetime.fromisoformat(last_ts)).total_seconds()

    healthy = seconds_since is not None and seconds_since <= stale_after_seconds

    return {
        "status": "healthy" if healthy else "stale",
        "model_version": model_version,
        "last_inference_at": last_ts,
        "seconds_since_last_inference": seconds_since,
        "active": active_row is not None,
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/observability/test_model_health.py -v`
Expected: PASS (10 tests).

- [ ] **Step 7: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green (Phase 5 Task 1 adds only a new package + tests; nothing existing changes). Suite grows by 10 tests from the 144 baseline.

- [ ] **Step 8: Commit**

```bash
git add src/observability/__init__.py src/observability/model_health.py src/observability/telemetry.py tests/observability/__init__.py tests/observability/test_model_health.py
git commit -m "feat: model observability compute fns (heartbeat + rolling telemetry)"
```

---

### Task 2: Observability endpoints (`GET /api/model/health`, `GET /api/model/telemetry`)

**Files:**
- Create: `src/api/routes/model.py`
- Modify: `src/api/app.py:18-29` (add `model` to the routes import tuple) and `src/api/app.py:47` (add `model` to the mount loop)
- Test: `tests/api/test_model_endpoints.py`

**Interfaces:**
- Consumes: `compute_health` / `compute_telemetry` (Task 1); `get_db` (`src/api/deps.py`); router mounted under `Depends(get_current_user)` (any authenticated user).
- Produces:
  - `GET /api/model/health` → the `compute_health` dict. Heartbeat window from env `MAINTAINIQ_MODEL_HEARTBEAT_SECONDS` (float; default `DEFAULT_STALE_AFTER_SECONDS`).
  - `GET /api/model/telemetry?window_minutes=<float>` → the `compute_telemetry` dict. Default `DEFAULT_WINDOW_MINUTES`; `window_minutes` validated `gt=0`.

**Design decisions:**
- `now` is `datetime.now(timezone.utc)` read in the route and passed into the pure functions.
- Env var is read per-request inside the handler (not at import) so tests can set/unset it via `monkeypatch` without reimporting. Parse defensively: a malformed value falls back to the default.
- No PII: both responses contain only model version, timestamps, counts, and rates — no machine ids, no user data. (Enforced by the function contracts in Task 1; the reviewer verifies.)

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_model_endpoints.py`:

```python
"""API tests for the Phase 5 observability endpoints."""
import sqlite3
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc)


def _log(db_path, *, status="ok", latency_ms=10.0, ood=0, warming=0,
         seconds_ago=30.0, model_version="v1"):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO model_inference_log
                   (machine_id, timestamp, model_version, latency_ms, failure_probability,
                    predicted_rul_minutes, out_of_distribution, warming_up, warnings_count,
                    status, error_message)
               VALUES ('m1', ?, ?, ?, NULL, NULL, ?, ?, 0, ?, ?)""",
            (
                (_now() - timedelta(seconds=seconds_ago)).isoformat(),
                model_version, latency_ms, ood, warming, status,
                None if status == "ok" else "boom",
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO model_registry (model_version, artifact_path, is_active)
               VALUES (?, 'models/xjtu_rul.joblib', 1)""",
            (model_version,),
        )
        conn.commit()
    finally:
        conn.close()


def test_health_requires_auth(anon_client):
    resp = anon_client.get("/api/model/health")
    assert resp.status_code == 401


def test_telemetry_requires_auth(anon_client):
    resp = anon_client.get("/api/model/telemetry")
    assert resp.status_code == 401


def test_health_healthy_after_recent_inference(client, db_path):
    _log(db_path, status="ok", seconds_ago=30.0, model_version="v1")
    resp = client.get("/api/model/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["model_version"] == "v1"
    assert body["active"] is True
    assert body["last_inference_at"] is not None
    assert body["seconds_since_last_inference"] >= 0.0


def test_health_stale_with_tight_window_env(client, db_path, monkeypatch):
    _log(db_path, status="ok", seconds_ago=300.0, model_version="v1")
    monkeypatch.setenv("MAINTAINIQ_MODEL_HEARTBEAT_SECONDS", "60")
    resp = client.get("/api/model/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "stale"   # 300s ago > 60s window


def test_telemetry_counts_and_rates(client, db_path):
    _log(db_path, status="ok", latency_ms=10.0, ood=0, warming=0, seconds_ago=10)
    _log(db_path, status="ok", latency_ms=20.0, ood=1, warming=0, seconds_ago=11)
    _log(db_path, status="ok", latency_ms=30.0, ood=0, warming=1, seconds_ago=12)
    _log(db_path, status="ok", latency_ms=40.0, ood=1, warming=1, seconds_ago=13)
    _log(db_path, status="error", latency_ms=50.0, seconds_ago=14)
    resp = client.get("/api/model/telemetry?window_minutes=60")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inference_count"] == 5
    assert body["error_rate"] == 0.2
    assert body["ood_rate"] == 0.5
    assert body["warming_up_rate"] == 0.5
    assert body["latency_p50_ms"] == 30.0
    assert body["latency_p95_ms"] == 50.0


def test_telemetry_rejects_nonpositive_window(client, db_path):
    resp = client.get("/api/model/telemetry?window_minutes=0")
    assert resp.status_code == 422
```

> `client` and the raw `sqlite3` writes here both target the same `db_path` temp DB (the `client` fixture's `get_db` override points at `db_path`), so a row inserted before the request is visible to the endpoint.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/api/test_model_endpoints.py -v`
Expected: FAIL — 404 on `/api/model/health` and `/api/model/telemetry` (router not mounted).

- [ ] **Step 3: Implement `src/api/routes/model.py`**

```python
"""Model observability endpoints: heartbeat + rolling telemetry (Phase 5).

Read-only. Mounted under Depends(get_current_user) in app.py, so any
authenticated user may read them (no PII in either response).
"""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_db
from src.observability import model_health, telemetry

router = APIRouter(prefix="/model", tags=["model"])

_HEARTBEAT_ENV = "MAINTAINIQ_MODEL_HEARTBEAT_SECONDS"


def _stale_after_seconds() -> float:
    raw = os.environ.get(_HEARTBEAT_ENV)
    if raw is None:
        return model_health.DEFAULT_STALE_AFTER_SECONDS
    try:
        return float(raw)
    except ValueError:
        return model_health.DEFAULT_STALE_AFTER_SECONDS


@router.get("/health")
def model_health_endpoint(db=Depends(get_db)):
    return model_health.compute_health(
        db,
        now=datetime.now(timezone.utc),
        stale_after_seconds=_stale_after_seconds(),
    )


@router.get("/telemetry")
def model_telemetry_endpoint(
    db=Depends(get_db),
    window_minutes: float = Query(default=telemetry.DEFAULT_WINDOW_MINUTES, gt=0),
):
    return telemetry.compute_telemetry(
        db,
        now=datetime.now(timezone.utc),
        window_minutes=window_minutes,
    )
```

- [ ] **Step 4: Mount the router in `src/api/app.py`**

Add `model` to the routes import tuple (alphabetical, after `maintenance`):

```python
from src.api.routes import (
    alerts,
    auth,
    demo,
    ingestion,
    kpis,
    machines,
    maintenance,
    model,
    notifications,
    predictions,
    users,
)
```

Add `model` to the authenticated mount loop (currently `src/api/app.py:47`):

```python
for module in (machines, alerts, maintenance, kpis, predictions, ingestion, model):
    app.include_router(module.router, prefix="/api", dependencies=[Depends(get_current_user)])
```

- [ ] **Step 5: Run the endpoint tests to verify they pass**

Run: `python -m pytest tests/api/test_model_endpoints.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green. Suite grows by 7 tests over the Task 1 total.

- [ ] **Step 7: Commit**

```bash
git add src/api/routes/model.py src/api/app.py tests/api/test_model_endpoints.py
git commit -m "feat: expose GET /api/model/health and /api/model/telemetry"
```

---

## Phase 5 Review Gate (Phase 5 → 7)

Run the whole-phase `code-review` on the Phase 5 diff (opus, most capable model). Focus (from the roadmap):
- Aggregates computed in SQL where possible; percentiles correct (nearest-rank, hand-verifiable).
- Heartbeat window configurable (env var), read per-request; malformed value falls back to default.
- No PII in either telemetry or health response (only model version, timestamps, counts, rates).
- Rates use the documented denominators (error over total; ood/warming over successful rows); division-by-zero guarded.
- Endpoints read-only, mounted under `get_current_user`; `db.py` untouched; no new dependency.

## Phase 5 Acceptance

- With N logged inferences, `/api/model/telemetry` counts and percentiles match the hand-computed fixture (Task 1 + Task 2 tests).
- `/api/model/health` reports `healthy` after a recent successful inference and flips to `stale` once the window elapses with no new successful inference (Task 1 + Task 2 tests).
- `python -m pytest -q` green.
```

