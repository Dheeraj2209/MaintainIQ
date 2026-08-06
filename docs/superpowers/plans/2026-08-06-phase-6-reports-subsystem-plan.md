# Phase 6 — Reports Subsystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate, persist (with audit trail), retrieve, and download the three approved report types — `machine_prognostic`, `model_performance`, `fleet_summary` — from real persisted rows only, in `markdown` or `json`.

**Architecture:** A pure read-only generator layer (`src/reports/generators.py`) builds a machine-readable `summary` dict per report type and renders it to `content` (markdown/json); a thin service (`src/reports/service.py`) builds the summary once, renders, and persists one `reports` row so `summary_json` always matches `content`; FastAPI routes expose generate/list/get/download with per-report-type RBAC.

**Tech Stack:** Python 3 · FastAPI · SQLite (raw SQL) · pytest + TestClient. stdlib `json`/`datetime` only — no new deps.

## Global Constraints

Every task implicitly includes these (copied verbatim from the roadmap Global Constraints; spec is the design authority).

- **Canonical dataset is XJTU-SY.** No NASA/IMS assumptions in schema, model, or UI.
- **Drop synthetic temperature entirely.** No `temperature_c`/`temperature_severity` anywhere.
- **RUL is in minutes.** `rul_minutes`/`predicted_rul_minutes` is the unit of record (hours may be derived for display only — not used in this phase).
- **No new heavyweight deps.** stdlib + existing FastAPI only.
- **RBAC preserved:** admin/supervisor/operator.
- **TDD, DRY, YAGNI, frequent commits.** Failing test first, minimal code, green, commit. One deliverable per task.
- **Feature extraction has exactly one home** (`src.ingestion.xjtu_sy.extract_snapshot_features`) — not touched this phase.
- **Code-review gate between phases** (user mandate): review this phase's diff before Phase 7.
- **db.py schema is frozen this phase** — do NOT modify `src/storage/db.py`. The `reports` table already exists (committed in Phase 1).
- **Percentile / rate math is single-sourced.** Reuse `src.observability.telemetry._percentile`; do not re-implement percentiles.

---

## Spec-vs-Roadmap Resolutions (authority: the design spec)

The roadmap header states: "Design authority: the design spec … if the two conflict, the spec wins." Three roadmap-prose items are corrected here against `docs/superpowers/specs/2026-08-06-xjtu-sy-ml-integration-design.md` §4.6 + §7:

1. **Report type values** = `machine_prognostic`, `model_performance`, `fleet_summary` (spec §4.6/§7). The roadmap's `per_machine` is the spec's `machine_prognostic`.
2. **Formats** = `markdown` | `json` only (spec §4.6 `format` column; §7 endpoints set `text/markdown` or `application/json`). No CSV.
3. **RBAC is per-report-type** (spec §7 verbatim): "operators can generate/read `machine_prognostic`; `model_performance` and `fleet_summary` are admin/supervisor." The router is mounted under `Depends(get_current_user)` (any authed); the per-type gate runs inside each handler against the caller's role.

---

## Committed-Code Facts (verified against HEAD — do not re-derive from prose)

- **`reports` table** (`src/storage/db.py:122-133`), columns exactly:
  `id` (PK autoincrement), `report_type` TEXT NOT NULL, `scope` TEXT **NOT NULL**, `format` TEXT NOT NULL, `period_start` TEXT, `period_end` TEXT, `generated_at` TEXT NOT NULL DEFAULT `(datetime('now'))`, `generated_by` INTEGER, `content` TEXT NOT NULL, `summary_json` TEXT. There is **no CHECK constraint** on `report_type`/`format` — validation is in application code. `scope` is NOT NULL, so every generate call must pass a scope (machine_id for `machine_prognostic`; `'fleet'` for the other two).
- **`predictions`** columns used (`db.py:70-91`): `machine_id`, `timestamp`, `health_state`, `confidence`, `source`, `probable_cause`, `predicted_rul_minutes`, `rul_estimate_kind`, `failure_within_horizon_probability`, `prediction_interval_low`, `prediction_interval_high`, `model_version`, `out_of_distribution`, `id`. (`id` is monotonic autoincrement → `MAX(id)` per machine = latest row, no timestamp-tie ambiguity.)
- **`alerts`** columns used (`db.py:135-149`): `machine_id`, `opened_at`, `severity`, `health_state`, `probable_cause`, `message`, `status` (`'open'`/`'resolved'`).
- **`maintenance_records`** (`db.py:152-161`): `machine_id`, `performed_at`, `type` CHECK IN (`'preventive'`,`'corrective'`).
- **`model_inference_log`** (`db.py:94-108`): `machine_id`, `timestamp`, `model_version`, `latency_ms`, `out_of_distribution`, `warming_up`, `status` (`'ok'`/`'error'`).
- **`model_registry`** (`db.py:112-120`): `model_version`, `artifact_path`, `algorithm`, `trained_at`, `metrics_json`, `deployed_at`, `is_active`.
- **`machines`** (`db.py:37-46`): `machine_id` PK.
- **Percentile helper**: `src.observability.telemetry._percentile(sorted_values: list[float], pct: float) -> float | None` — nearest-rank; `None` if empty (Phase 5, committed). Reuse it.
- **App mount** (`src/api/app.py:18-49`): routers are imported as a tuple and mounted in a loop with `dependencies=[Depends(get_current_user)]`, before the SPA catch-all `@app.get("/{full_path:path}")`. Add `reports` to BOTH the import tuple and the loop.
- **DB dependency**: `from src.api.deps import get_db` yields a per-request `sqlite3.Connection` with `row_factory = sqlite3.Row` (`src/api/deps.py`).
- **Auth deps** (`src/auth/deps.py`): `get_current_user(request, conn) -> dict` returns the user row (`id`, `email`, `name`, `role`, ...); `require_role(*roles)` 403s. For per-type RBAC we inject `get_current_user` and branch on `user["role"]` inside the handler.
- **Schemas** (`src/api/schemas.py`): Pydantic `BaseModel` + `from typing import Literal, Optional`; existing request models like `ReplayStartRequest` (line 179) are the pattern.
- **Test fixtures** (`tests/conftest.py`): `conn` (seeded shared DB), `db_path`, `client` (admin), `auth_client(role)` factory, `anon_client`. **`tests/api/conftest.py` DELETEs all `predictions`** for tests under `tests/api/`, so API report tests must seed their own `predictions`/`model_inference_log` rows via raw `sqlite3` on `db_path` (the `machines`/`alerts`/`users` seed rows remain).

---

## File Structure

- `src/reports/__init__.py` — package marker (one-line docstring).
- `src/reports/generators.py` — Task 1. Pure read-only summary builders (`machine_prognostic`, `model_performance`, `fleet_summary`), a `build_summary` dispatcher, and renderers (`render`/`render_json`/`render_markdown` + `_md_cell`). Exports `REPORT_TYPES`, `FORMATS`.
- `src/reports/service.py` — Task 2. `generate` (build→render→persist→return), `get_report`, `list_reports`.
- `src/api/routes/reports.py` — Task 2. `POST /reports`, `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/download` with per-type RBAC.
- `src/api/schemas.py` — Task 2. Add `ReportCreateRequest`.
- `src/api/app.py` — Task 2. Mount `reports` router.
- `tests/reports/__init__.py` (empty), `tests/reports/test_generators.py` — Task 1.
- `tests/api/test_reports.py` — Task 2.

---

## Task 1: Report generators + renderers (read-only)

**Files:**
- Create: `src/reports/__init__.py`
- Create: `src/reports/generators.py`
- Create: `tests/reports/__init__.py` (empty)
- Test: `tests/reports/test_generators.py`

**Interfaces:**
- Consumes: a `sqlite3.Connection` (Row factory) over the committed schema; `src.observability.telemetry._percentile`.
- Produces (relied on by Task 2):
  - `REPORT_TYPES = ("machine_prognostic", "model_performance", "fleet_summary")`, `FORMATS = ("markdown", "json")`.
  - `build_summary(conn, *, report_type, scope, period_start=None, period_end=None) -> dict` — dispatches; raises `ValueError` on unknown type.
  - `machine_prognostic(conn, *, scope, period_start=None, period_end=None) -> dict`
  - `model_performance(conn, *, scope="fleet", period_start=None, period_end=None) -> dict`
  - `fleet_summary(conn, *, scope="fleet", period_start=None, period_end=None) -> dict`
  - `render(fmt, summary) -> str`, `render_json(summary) -> str`, `render_markdown(summary) -> str`, `_md_cell(value) -> str`. `render`/`render_markdown` raise `ValueError` on unknown format/type.

**Design decisions (locked so Task 2 and the reviewer share the contract):**
- **Real data only.** Every field comes from a committed table. The one derived field is `recommended_maintenance.by_timestamp = latest_prediction.timestamp + predicted_rul_minutes` (documented; `None` when there is no prediction or no RUL).
- **Latest = `MAX(id)`.** `predictions.id` is monotonic; latest-per-machine and current-prognosis both key on `MAX(id)` to avoid timestamp ties.
- **Period filtering** compares ISO-8601 UTC strings with `>=`/`<=` (consistent with Phase 5). `predictions`/`model_inference_log` filter on `timestamp`; `alerts` on `opened_at`; `maintenance_records` on `performed_at`. `None` bound → unbounded on that side.
- **Percentiles reuse `telemetry._percentile`** (no re-implementation).
- **`summary` is the single source of truth**; `render_json` is `json.dumps(summary, indent=2)`, so a JSON report's content round-trips to the exact summary Task 2 also stores as `summary_json`.
- **Markdown cell escaping:** `_md_cell` stringifies, escapes `\` then `|` (→ `\|`), and collapses `\r`/`\n` to a space, so free-text (`message`, `probable_cause`) can never break a table.
- **OOD is surfaced as a bool** (`bool(out_of_distribution)`).

- [ ] **Step 1: Create the package markers**

Create `src/reports/__init__.py`:

```python
"""Report generation subsystem: generators, renderers, service (Phase 6)."""
```

Create `tests/reports/__init__.py` as an empty file (no content).

- [ ] **Step 2: Write the failing tests**

Create `tests/reports/test_generators.py`:

```python
"""Unit tests for report generators + renderers (Phase 6 Task 1)."""
import json
import sqlite3

import pytest

from src.reports import generators
from src.storage.db import init_schema


@pytest.fixture
def rconn():
    """A fully-controlled in-memory DB on the real schema (no shared seed)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _machine(conn, machine_id="m1"):
    conn.execute(
        "INSERT INTO machines (machine_id, dataset, is_documented_failure) "
        "VALUES (?, 'xjtu_sy', 1)",
        (machine_id,),
    )
    conn.commit()


def _prediction(conn, *, machine_id="m1", timestamp, health_state="degrading",
                rul=None, low=None, high=None, prob=None, ood=0, cause=None,
                model_version="v1", confidence=None):
    conn.execute(
        """INSERT INTO predictions
               (machine_id, timestamp, health_state, confidence, source, probable_cause,
                created_at, predicted_rul_minutes, rul_estimate_kind,
                failure_within_horizon_probability, prediction_interval_low,
                prediction_interval_high, model_version, out_of_distribution)
           VALUES (?, ?, ?, ?, 'xjtu_rul', ?, ?, ?, 'point_estimate', ?, ?, ?, ?, ?)""",
        (machine_id, timestamp, health_state, confidence, cause, timestamp, rul,
         prob, low, high, model_version, ood),
    )
    conn.commit()


def _alert(conn, *, machine_id="m1", opened_at, severity="high", health_state="critical",
           status="open", cause="bearing_wear", message="boom"):
    conn.execute(
        """INSERT INTO alerts
               (machine_id, opened_at, severity, health_state, probable_cause, message,
                status, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'ml', ?)""",
        (machine_id, opened_at, severity, health_state, cause, message, status, opened_at),
    )
    conn.commit()


def _inference(conn, *, machine_id="m1", timestamp, latency_ms, status="ok",
               ood=0, warming=0, model_version="v1"):
    conn.execute(
        """INSERT INTO model_inference_log
               (machine_id, timestamp, model_version, latency_ms, out_of_distribution,
                warming_up, warnings_count, status)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
        (machine_id, timestamp, model_version, latency_ms, ood, warming, status),
    )
    conn.commit()


# ---- machine_prognostic ----

def test_machine_prognostic_current_is_latest(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="degrading",
                rul=200.0, low=150.0, high=260.0, prob=0.4, cause="bearing_wear", confidence=0.7)
    _prediction(rconn, timestamp="2030-01-01T01:00:00+00:00", health_state="critical",
                rul=60.0, low=40.0, high=90.0, prob=0.9, cause="bearing_wear", confidence=0.95)
    s = generators.machine_prognostic(rconn, scope="m1")
    assert s["report_type"] == "machine_prognostic"
    assert s["scope"] == "m1"
    assert s["prediction_count"] == 2
    assert s["current"]["health_state"] == "critical"
    assert s["current"]["predicted_rul_minutes"] == 60.0
    assert s["current"]["prediction_interval_low"] == 40.0
    assert s["current"]["prediction_interval_high"] == 90.0
    assert s["current"]["out_of_distribution"] is False
    assert [p["health_state"] for p in s["health_state_trajectory"]] == ["degrading", "critical"]
    assert s["recommended_maintenance"]["within_minutes"] == 60.0
    assert s["recommended_maintenance"]["by_timestamp"] == "2030-01-01T02:00:00+00:00"


def test_machine_prognostic_no_predictions(rconn):
    _machine(rconn, "m9")
    s = generators.machine_prognostic(rconn, scope="m9")
    assert s["prediction_count"] == 0
    assert s["current"] is None
    assert s["recommended_maintenance"] == {"within_minutes": None, "by_timestamp": None}
    assert s["health_state_trajectory"] == []
    assert s["recent_alerts"] == []


def test_machine_prognostic_period_filters(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2029-12-01T00:00:00+00:00", health_state="healthy", rul=500.0)
    _prediction(rconn, timestamp="2030-01-15T00:00:00+00:00", health_state="critical", rul=30.0)
    s = generators.machine_prognostic(
        rconn, scope="m1",
        period_start="2030-01-01T00:00:00+00:00",
        period_end="2030-01-31T00:00:00+00:00",
    )
    assert s["prediction_count"] == 1
    assert s["current"]["health_state"] == "critical"


def test_machine_prognostic_alerts_included_and_escaped(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="critical", rul=30.0)
    _alert(rconn, opened_at="2030-01-01T00:05:00+00:00", message="pipe | in message")
    s = generators.machine_prognostic(rconn, scope="m1")
    assert len(s["recent_alerts"]) == 1
    assert s["recent_alerts"][0]["message"] == "pipe | in message"
    md = generators.render_markdown(s)
    assert "pipe \\| in message" in md


# ---- model_performance ----

def test_model_performance_counts_and_percentiles(rconn):
    for lat, st, ood, warm in [
        (10.0, "ok", 0, 0), (20.0, "ok", 1, 0), (30.0, "ok", 0, 1),
        (40.0, "ok", 1, 1), (50.0, "error", 0, 0),
    ]:
        _inference(rconn, timestamp="2030-01-01T00:00:00+00:00",
                   latency_ms=lat, status=st, ood=ood, warming=warm)
    s = generators.model_performance(rconn, scope="fleet")
    assert s["inference_count"] == 5
    assert s["error_count"] == 1
    assert s["error_rate"] == 0.2
    assert s["latency_p50_ms"] == 30.0
    assert s["latency_p95_ms"] == 50.0
    assert s["ood_rate"] == 0.5
    assert s["warming_up_rate"] == 0.5
    assert s["active_model"] is None


def test_model_performance_active_model_metrics(rconn):
    rconn.execute(
        """INSERT INTO model_registry
               (model_version, artifact_path, algorithm, trained_at, metrics_json,
                deployed_at, is_active)
           VALUES ('v1', 'models/x.joblib', 'ExtraTrees', '2030-01-01', ?, '2030-01-02', 1)""",
        (json.dumps({"rmse": 12.5}),),
    )
    rconn.commit()
    s = generators.model_performance(rconn, scope="fleet")
    assert s["inference_count"] == 0
    assert s["active_model"]["model_version"] == "v1"
    assert s["active_model"]["algorithm"] == "ExtraTrees"
    assert s["active_model"]["metrics"] == {"rmse": 12.5}


# ---- fleet_summary ----

def test_fleet_summary_distribution_and_at_risk(rconn):
    _machine(rconn, "m1")
    _machine(rconn, "m2")
    _machine(rconn, "m3")  # no prediction
    _prediction(rconn, machine_id="m1", timestamp="2030-01-01T00:00:00+00:00",
                health_state="critical", rul=30.0)
    _prediction(rconn, machine_id="m2", timestamp="2030-01-01T00:00:00+00:00",
                health_state="healthy", rul=500.0)
    _alert(rconn, machine_id="m1", opened_at="2030-01-01T00:05:00+00:00", status="open")
    _alert(rconn, machine_id="m1", opened_at="2030-01-01T00:06:00+00:00", status="resolved")
    rconn.execute(
        "INSERT INTO maintenance_records (machine_id, performed_at, created_at, type) "
        "VALUES ('m1', '2030-01-01T01:00:00+00:00', '2030-01-01T01:00:00+00:00', 'preventive')"
    )
    rconn.commit()
    s = generators.fleet_summary(rconn, scope="fleet")
    assert s["machine_count"] == 3
    assert s["health_distribution"] == {"critical": 1, "healthy": 1}
    assert s["top_at_risk"][0]["machine_id"] == "m1"
    assert s["top_at_risk"][0]["predicted_rul_minutes"] == 30.0
    assert s["alert_counts"] == {"total": 2, "open": 1, "resolved": 1}
    assert s["maintenance_counts"] == {"total": 1, "preventive": 1, "corrective": 0}


def test_fleet_summary_latest_prediction_per_machine(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, machine_id="m1", timestamp="2030-01-01T00:00:00+00:00",
                health_state="healthy", rul=500.0)
    _prediction(rconn, machine_id="m1", timestamp="2030-01-01T02:00:00+00:00",
                health_state="critical", rul=25.0)
    s = generators.fleet_summary(rconn, scope="fleet")
    assert s["health_distribution"] == {"critical": 1}
    assert s["top_at_risk"][0]["predicted_rul_minutes"] == 25.0


# ---- renderers ----

def test_render_json_roundtrips(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="critical", rul=30.0)
    s = generators.machine_prognostic(rconn, scope="m1")
    assert json.loads(generators.render_json(s)) == s


def test_render_markdown_headings_per_type(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="critical", rul=30.0)
    s = generators.machine_prognostic(rconn, scope="m1")
    md = generators.render_markdown(s)
    assert md.startswith("# Machine Prognostic Report")
    assert "## Health-State Trajectory" in md


def test_md_cell_escapes_pipes_and_newlines():
    assert generators._md_cell("a|b") == "a\\|b"
    assert generators._md_cell("a\nb") == "a b"
    assert generators._md_cell(None) == ""


def test_render_rejects_unknown_format(rconn):
    _machine(rconn, "m1")
    s = generators.machine_prognostic(rconn, scope="m1")
    with pytest.raises(ValueError):
        generators.render("pdf", s)
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/reports/test_generators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.reports.generators'`.

- [ ] **Step 4: Implement `src/reports/generators.py`**

```python
"""Report content generators + renderers (Phase 6).

Each builder reads ONLY real persisted rows and returns a machine-readable
`summary` dict. Renderers turn a summary into the stored `content` string. The
service (service.py) builds the summary once and derives both `content` and the
persisted `summary_json` from it, so the two always match.

Latency percentiles reuse src.observability.telemetry._percentile — no math is
re-implemented here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.observability.telemetry import _percentile

REPORT_TYPES = ("machine_prognostic", "model_performance", "fleet_summary")
FORMATS = ("markdown", "json")

_TRAJECTORY_LIMIT = 10
_RECENT_ALERTS_LIMIT = 10
_TOP_AT_RISK_LIMIT = 5


def _period_clause(column: str, period_start, period_end) -> tuple[str, dict]:
    """Build an ' AND <col> >= :period_start AND <col> <= :period_end' fragment.
    `column` is a fixed literal supplied by this module (never user input)."""
    clauses = []
    params: dict = {}
    if period_start is not None:
        clauses.append(f"{column} >= :period_start")
        params["period_start"] = period_start
    if period_end is not None:
        clauses.append(f"{column} <= :period_end")
        params["period_end"] = period_end
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _add_minutes(iso_ts: str, minutes: float) -> str:
    return (datetime.fromisoformat(iso_ts) + timedelta(minutes=minutes)).isoformat()


def machine_prognostic(conn, *, scope, period_start=None, period_end=None) -> dict:
    where, params = _period_clause("timestamp", period_start, period_end)
    params["machine_id"] = scope

    latest = conn.execute(
        f"""SELECT timestamp, health_state, predicted_rul_minutes, rul_estimate_kind,
                   prediction_interval_low, prediction_interval_high,
                   failure_within_horizon_probability, out_of_distribution,
                   probable_cause, model_version, confidence
            FROM predictions
            WHERE machine_id = :machine_id{where}
            ORDER BY id DESC LIMIT 1""",
        params,
    ).fetchone()

    count_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM predictions WHERE machine_id = :machine_id{where}",
        params,
    ).fetchone()

    trajectory_rows = conn.execute(
        f"""SELECT timestamp, health_state FROM predictions
            WHERE machine_id = :machine_id{where}
            ORDER BY id DESC LIMIT {_TRAJECTORY_LIMIT}""",
        params,
    ).fetchall()

    alert_where, alert_params = _period_clause("opened_at", period_start, period_end)
    alert_params["machine_id"] = scope
    alert_rows = conn.execute(
        f"""SELECT opened_at, severity, health_state, status, probable_cause, message
            FROM alerts
            WHERE machine_id = :machine_id{alert_where}
            ORDER BY opened_at DESC LIMIT {_RECENT_ALERTS_LIMIT}""",
        alert_params,
    ).fetchall()

    current = None
    recommended = {"within_minutes": None, "by_timestamp": None}
    if latest is not None:
        current = {
            "timestamp": latest["timestamp"],
            "health_state": latest["health_state"],
            "predicted_rul_minutes": latest["predicted_rul_minutes"],
            "rul_estimate_kind": latest["rul_estimate_kind"],
            "prediction_interval_low": latest["prediction_interval_low"],
            "prediction_interval_high": latest["prediction_interval_high"],
            "failure_within_horizon_probability": latest["failure_within_horizon_probability"],
            "out_of_distribution": bool(latest["out_of_distribution"]),
            "probable_cause": latest["probable_cause"],
            "model_version": latest["model_version"],
            "confidence": latest["confidence"],
        }
        rul = latest["predicted_rul_minutes"]
        if rul is not None:
            recommended = {
                "within_minutes": rul,
                "by_timestamp": _add_minutes(latest["timestamp"], rul),
            }

    return {
        "report_type": "machine_prognostic",
        "scope": scope,
        "period": {"start": period_start, "end": period_end},
        "prediction_count": count_row["n"],
        "current": current,
        "health_state_trajectory": [
            {"timestamp": r["timestamp"], "health_state": r["health_state"]}
            for r in reversed(trajectory_rows)
        ],
        "recent_alerts": [
            {
                "opened_at": r["opened_at"],
                "severity": r["severity"],
                "health_state": r["health_state"],
                "status": r["status"],
                "probable_cause": r["probable_cause"],
                "message": r["message"],
            }
            for r in alert_rows
        ],
        "recommended_maintenance": recommended,
    }


def model_performance(conn, *, scope="fleet", period_start=None, period_end=None) -> dict:
    where, params = _period_clause("timestamp", period_start, period_end)

    agg = conn.execute(
        f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                SUM(CASE WHEN status = 'ok' THEN out_of_distribution ELSE 0 END) AS ood_sum,
                SUM(CASE WHEN status = 'ok' THEN warming_up ELSE 0 END) AS warming_sum
            FROM model_inference_log
            WHERE 1 = 1{where}""",
        params,
    ).fetchone()
    total = agg["total"] or 0
    errors = agg["errors"] or 0
    ok_count = agg["ok_count"] or 0
    ood_sum = agg["ood_sum"] or 0
    warming_sum = agg["warming_sum"] or 0

    latencies = [
        r["latency_ms"]
        for r in conn.execute(
            f"""SELECT latency_ms FROM model_inference_log
                WHERE 1 = 1{where} ORDER BY latency_ms ASC""",
            params,
        ).fetchall()
    ]

    active = conn.execute(
        """SELECT model_version, algorithm, trained_at, metrics_json
           FROM model_registry WHERE is_active = 1
           ORDER BY deployed_at DESC LIMIT 1"""
    ).fetchone()
    active_model = None
    if active is not None:
        metrics = None
        if active["metrics_json"]:
            try:
                metrics = json.loads(active["metrics_json"])
            except (ValueError, TypeError):
                metrics = None
        active_model = {
            "model_version": active["model_version"],
            "algorithm": active["algorithm"],
            "trained_at": active["trained_at"],
            "metrics": metrics,
        }

    return {
        "report_type": "model_performance",
        "scope": "fleet",
        "period": {"start": period_start, "end": period_end},
        "inference_count": total,
        "error_count": errors,
        "error_rate": (errors / total) if total else 0.0,
        "latency_p50_ms": _percentile(latencies, 50.0),
        "latency_p95_ms": _percentile(latencies, 95.0),
        "ood_rate": (ood_sum / ok_count) if ok_count else 0.0,
        "warming_up_rate": (warming_sum / ok_count) if ok_count else 0.0,
        "active_model": active_model,
    }


def fleet_summary(conn, *, scope="fleet", period_start=None, period_end=None) -> dict:
    pred_where, pred_params = _period_clause("timestamp", period_start, period_end)

    machine_count = conn.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"]

    latest_rows = conn.execute(
        f"""SELECT p.machine_id, p.health_state, p.predicted_rul_minutes
            FROM predictions p
            JOIN (SELECT machine_id, MAX(id) AS max_id FROM predictions
                  WHERE 1 = 1{pred_where} GROUP BY machine_id) l
              ON p.id = l.max_id
            ORDER BY p.machine_id""",
        pred_params,
    ).fetchall()

    health_distribution: dict = {}
    at_risk = []
    for r in latest_rows:
        state = r["health_state"]
        health_distribution[state] = health_distribution.get(state, 0) + 1
        at_risk.append({
            "machine_id": r["machine_id"],
            "predicted_rul_minutes": r["predicted_rul_minutes"],
            "health_state": r["health_state"],
        })

    # Lowest predicted RUL = most at risk; machines with no RUL sort last.
    at_risk.sort(key=lambda x: (
        x["predicted_rul_minutes"] is None,
        x["predicted_rul_minutes"] if x["predicted_rul_minutes"] is not None else 0.0,
    ))
    top_at_risk = at_risk[:_TOP_AT_RISK_LIMIT]

    alert_where, alert_params = _period_clause("opened_at", period_start, period_end)
    alert_agg = conn.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved_count
            FROM alerts WHERE 1 = 1{alert_where}""",
        alert_params,
    ).fetchone()

    maint_where, maint_params = _period_clause("performed_at", period_start, period_end)
    maint_agg = conn.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN type = 'preventive' THEN 1 ELSE 0 END) AS preventive,
                   SUM(CASE WHEN type = 'corrective' THEN 1 ELSE 0 END) AS corrective
            FROM maintenance_records WHERE 1 = 1{maint_where}""",
        maint_params,
    ).fetchone()

    return {
        "report_type": "fleet_summary",
        "scope": "fleet",
        "period": {"start": period_start, "end": period_end},
        "machine_count": machine_count,
        "health_distribution": health_distribution,
        "top_at_risk": top_at_risk,
        "alert_counts": {
            "total": alert_agg["total"] or 0,
            "open": alert_agg["open_count"] or 0,
            "resolved": alert_agg["resolved_count"] or 0,
        },
        "maintenance_counts": {
            "total": maint_agg["total"] or 0,
            "preventive": maint_agg["preventive"] or 0,
            "corrective": maint_agg["corrective"] or 0,
        },
    }


_BUILDERS = {
    "machine_prognostic": machine_prognostic,
    "model_performance": model_performance,
    "fleet_summary": fleet_summary,
}


def build_summary(conn, *, report_type, scope, period_start=None, period_end=None) -> dict:
    if report_type not in _BUILDERS:
        raise ValueError(f"unknown report_type: {report_type}")
    return _BUILDERS[report_type](
        conn, scope=scope, period_start=period_start, period_end=period_end
    )


# ---- rendering ----

def _md_cell(value) -> str:
    """Escape a value for a Markdown table cell: pipes/newlines break tables."""
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def render(fmt: str, summary: dict) -> str:
    if fmt == "json":
        return render_json(summary)
    if fmt == "markdown":
        return render_markdown(summary)
    raise ValueError(f"unknown format: {fmt}")


def render_json(summary: dict) -> str:
    return json.dumps(summary, indent=2)


def render_markdown(summary: dict) -> str:
    rt = summary.get("report_type")
    if rt == "machine_prognostic":
        return _md_machine_prognostic(summary)
    if rt == "model_performance":
        return _md_model_performance(summary)
    if rt == "fleet_summary":
        return _md_fleet_summary(summary)
    raise ValueError(f"unknown report_type: {rt}")


def _md_machine_prognostic(s: dict) -> str:
    p = s["period"]
    lines = [
        f"# Machine Prognostic Report — {_md_cell(s['scope'])}",
        "",
        f"- Period: {_md_cell(p['start'])} → {_md_cell(p['end'])}",
        f"- Predictions in scope: {s['prediction_count']}",
        "",
        "## Current Prognosis",
        "",
    ]
    cur = s["current"]
    if cur is None:
        lines.append("_No predictions available for this machine in the selected period._")
    else:
        rec = s["recommended_maintenance"]
        lines += [
            f"- Health state: {_md_cell(cur['health_state'])}",
            f"- Predicted RUL (minutes): {_md_cell(cur['predicted_rul_minutes'])}",
            f"- 90% interval (minutes): {_md_cell(cur['prediction_interval_low'])} "
            f"– {_md_cell(cur['prediction_interval_high'])}",
            f"- Failure-within-horizon probability: "
            f"{_md_cell(cur['failure_within_horizon_probability'])}",
            f"- Confidence: {_md_cell(cur['confidence'])}",
            f"- Out of distribution: {_md_cell(cur['out_of_distribution'])}",
            f"- Probable cause: {_md_cell(cur['probable_cause'])}",
            f"- Model version: {_md_cell(cur['model_version'])}",
            f"- Recommended maintenance by: {_md_cell(rec['by_timestamp'])}",
        ]
    lines += [
        "",
        "## Health-State Trajectory",
        "",
        "| Timestamp | Health state |",
        "| --- | --- |",
    ]
    for pt in s["health_state_trajectory"]:
        lines.append(f"| {_md_cell(pt['timestamp'])} | {_md_cell(pt['health_state'])} |")
    lines += [
        "",
        "## Recent Alerts",
        "",
        "| Opened | Severity | Health | Status | Cause | Message |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for a in s["recent_alerts"]:
        lines.append(
            f"| {_md_cell(a['opened_at'])} | {_md_cell(a['severity'])} "
            f"| {_md_cell(a['health_state'])} | {_md_cell(a['status'])} "
            f"| {_md_cell(a['probable_cause'])} | {_md_cell(a['message'])} |"
        )
    return "\n".join(lines) + "\n"


def _md_model_performance(s: dict) -> str:
    p = s["period"]
    lines = [
        "# Model Performance Report",
        "",
        f"- Period: {_md_cell(p['start'])} → {_md_cell(p['end'])}",
        "",
        "## Inference Telemetry",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Inference count | {s['inference_count']} |",
        f"| Error count | {s['error_count']} |",
        f"| Error rate | {s['error_rate']} |",
        f"| Latency p50 (ms) | {_md_cell(s['latency_p50_ms'])} |",
        f"| Latency p95 (ms) | {_md_cell(s['latency_p95_ms'])} |",
        f"| OOD rate | {s['ood_rate']} |",
        f"| Warming-up rate | {s['warming_up_rate']} |",
        "",
        "## Active Model",
        "",
    ]
    am = s["active_model"]
    if am is None:
        lines.append("_No active model registered._")
    else:
        metrics = am["metrics"]
        lines += [
            f"- Version: {_md_cell(am['model_version'])}",
            f"- Algorithm: {_md_cell(am['algorithm'])}",
            f"- Trained at: {_md_cell(am['trained_at'])}",
            f"- Metrics: {_md_cell(json.dumps(metrics) if metrics is not None else None)}",
        ]
    return "\n".join(lines) + "\n"


def _md_fleet_summary(s: dict) -> str:
    p = s["period"]
    lines = [
        "# Fleet Summary Report",
        "",
        f"- Period: {_md_cell(p['start'])} → {_md_cell(p['end'])}",
        f"- Machines: {s['machine_count']}",
        "",
        "## Health Distribution",
        "",
        "| Health state | Machines |",
        "| --- | --- |",
    ]
    for state, n in s["health_distribution"].items():
        lines.append(f"| {_md_cell(state)} | {n} |")
    lines += [
        "",
        "## Top At-Risk Machines",
        "",
        "| Machine | Predicted RUL (min) | Health state |",
        "| --- | --- | --- |",
    ]
    for m in s["top_at_risk"]:
        lines.append(
            f"| {_md_cell(m['machine_id'])} | {_md_cell(m['predicted_rul_minutes'])} "
            f"| {_md_cell(m['health_state'])} |"
        )
    ac = s["alert_counts"]
    mc = s["maintenance_counts"]
    lines += [
        "",
        "## Activity In Period",
        "",
        "| Category | Count |",
        "| --- | --- |",
        f"| Alerts total | {ac['total']} |",
        f"| Alerts open | {ac['open']} |",
        f"| Alerts resolved | {ac['resolved']} |",
        f"| Maintenance total | {mc['total']} |",
        f"| Maintenance preventive | {mc['preventive']} |",
        f"| Maintenance corrective | {mc['corrective']} |",
    ]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/reports/test_generators.py -v`
Expected: PASS (12 tests).

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green. Suite grows by 12 from the 158 baseline (→ 170).

- [ ] **Step 7: Commit**

```bash
git add src/reports/__init__.py src/reports/generators.py tests/reports/__init__.py tests/reports/test_generators.py
git commit -m "feat: report generators + markdown/json renderers (read-only)"
```

---

## Task 2: Report service + API endpoints (persist, RBAC, download)

**Files:**
- Create: `src/reports/service.py`
- Create: `src/api/routes/reports.py`
- Modify: `src/api/schemas.py` — add `ReportCreateRequest`
- Modify: `src/api/app.py` — mount the `reports` router
- Test: `tests/api/test_reports.py`

**Interfaces:**
- Consumes: Task 1 `generators` (`REPORT_TYPES`, `FORMATS`, `build_summary`, `render`); `src.api.deps.get_db`; `src.auth.deps.get_current_user`.
- Produces:
  - `service.generate(conn, *, report_type, scope, format, period_start=None, period_end=None, generated_by=None) -> dict` — validates type+format (raises `ValueError`), builds summary once, renders `content`, persists one `reports` row (with `summary_json = json.dumps(summary)`), returns the stored row via `get_report`.
  - `service.get_report(conn, report_id) -> dict | None` — full row incl `content`.
  - `service.list_reports(conn, *, report_types=None, scope=None, limit=100) -> list[dict]` — headline rows (no `content`), newest first.
  - Endpoints under `/api/reports`: `POST ""`, `GET ""`, `GET "/{report_id}"`, `GET "/{report_id}/download"`.

**Design decisions:**
- **Per-type RBAC** (spec §7): a handler helper `_authorize_type(user, report_type)` raises 403 when `report_type in {model_performance, fleet_summary}` and the caller is not admin/supervisor. Applied to POST (on the requested type), GET-by-id and download (on the stored type). `GET ""` (list) filters an operator's results to `machine_prognostic` only.
- **machine_prognostic requires an existing machine:** POST returns 404 if `scope` is not in `machines` (only for that type; `fleet` types skip the check).
- **Response shape:** endpoints return the stored row with `summary_json` parsed into a `summary` object and the raw `summary_json` key removed (`_present`). List rows carry `summary` but no `content`.
- **Download:** `Response(content, media_type=..., headers={Content-Disposition: attachment; filename="report-{id}.{ext}"})`; `markdown`→`text/markdown`/`.md`, `json`→`application/json`/`.json`.
- **Pydantic validation** (`ReportCreateRequest`) rejects unknown `report_type`/`format` with 422 before the handler runs.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_reports.py`:

```python
"""API tests for the reports subsystem (Phase 6 Task 2).

tests/api/conftest.py clears the shared `predictions` seed, so these tests seed
their own prediction rows on db_path. machines/alerts/users seed rows remain.
"""
import json
import sqlite3


def _seed_prediction(db_path, *, machine_id="m1",
                     timestamp="2030-01-01T00:00:00+00:00",
                     health_state="critical", rul=30.0):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO predictions
                   (machine_id, timestamp, health_state, source, created_at,
                    predicted_rul_minutes, rul_estimate_kind, prediction_interval_low,
                    prediction_interval_high, model_version, out_of_distribution)
               VALUES (?, ?, ?, 'xjtu_rul', ?, ?, 'point_estimate', ?, ?, 'v1', 0)""",
            (machine_id, timestamp, health_state, timestamp, rul, rul * 0.7, rul * 1.3),
        )
        conn.commit()
    finally:
        conn.close()


def test_create_requires_auth(anon_client):
    resp = anon_client.post(
        "/api/reports", json={"report_type": "machine_prognostic", "scope": "m1"}
    )
    assert resp.status_code == 401


def test_list_requires_auth(anon_client):
    assert anon_client.get("/api/reports").status_code == 401


def test_operator_can_generate_machine_prognostic(auth_client, db_path):
    _seed_prediction(db_path)
    client = auth_client("operator")
    resp = client.post(
        "/api/reports",
        json={"report_type": "machine_prognostic", "scope": "m1", "format": "json"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report_type"] == "machine_prognostic"
    assert body["summary"]["current"]["predicted_rul_minutes"] == 30.0
    assert "summary_json" not in body


def test_operator_cannot_generate_fleet_summary(auth_client):
    client = auth_client("operator")
    resp = client.post(
        "/api/reports", json={"report_type": "fleet_summary", "scope": "fleet"}
    )
    assert resp.status_code == 403


def test_supervisor_can_generate_fleet_summary(auth_client):
    client = auth_client("supervisor")
    resp = client.post(
        "/api/reports",
        json={"report_type": "fleet_summary", "scope": "fleet", "format": "markdown"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["report_type"] == "fleet_summary"


def test_create_unknown_machine_404(auth_client):
    client = auth_client("supervisor")
    resp = client.post(
        "/api/reports", json={"report_type": "machine_prognostic", "scope": "ghost"}
    )
    assert resp.status_code == 404


def test_create_rejects_bad_type(auth_client):
    client = auth_client("supervisor")
    resp = client.post(
        "/api/reports", json={"report_type": "nonsense", "scope": "fleet"}
    )
    assert resp.status_code == 422


def test_get_by_id_returns_stored_report(auth_client, db_path):
    _seed_prediction(db_path)
    client = auth_client("supervisor")
    created = client.post(
        "/api/reports", json={"report_type": "machine_prognostic", "scope": "m1"}
    ).json()
    rid = created["id"]
    resp = client.get(f"/api/reports/{rid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == rid
    assert resp.json()["content"]


def test_get_missing_report_404(auth_client):
    client = auth_client("supervisor")
    assert client.get("/api/reports/99999").status_code == 404


def test_download_sets_content_type_and_disposition(auth_client, db_path):
    _seed_prediction(db_path)
    client = auth_client("supervisor")
    created = client.post(
        "/api/reports",
        json={"report_type": "machine_prognostic", "scope": "m1", "format": "markdown"},
    ).json()
    rid = created["id"]
    resp = client.get(f"/api/reports/{rid}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert f'filename="report-{rid}.md"' in resp.headers["content-disposition"]
    assert resp.text.startswith("# Machine Prognostic Report")


def test_download_json_report(auth_client, db_path):
    _seed_prediction(db_path)
    client = auth_client("supervisor")
    created = client.post(
        "/api/reports",
        json={"report_type": "machine_prognostic", "scope": "m1", "format": "json"},
    ).json()
    rid = created["id"]
    resp = client.get(f"/api/reports/{rid}/download")
    assert resp.headers["content-type"].startswith("application/json")
    assert json.loads(resp.text)["report_type"] == "machine_prognostic"


def test_operator_cannot_read_elevated_report(auth_client):
    sup = auth_client("supervisor")
    created = sup.post(
        "/api/reports", json={"report_type": "fleet_summary", "scope": "fleet"}
    ).json()
    rid = created["id"]
    op = auth_client("operator")
    assert op.get(f"/api/reports/{rid}").status_code == 403
    assert op.get(f"/api/reports/{rid}/download").status_code == 403


def test_list_filters_by_role(auth_client, db_path):
    _seed_prediction(db_path)
    sup = auth_client("supervisor")
    sup.post("/api/reports", json={"report_type": "machine_prognostic", "scope": "m1"})
    sup.post("/api/reports", json={"report_type": "fleet_summary", "scope": "fleet"})
    assert len(sup.get("/api/reports").json()) == 2
    op = auth_client("operator")
    rows = op.get("/api/reports").json()
    assert len(rows) == 1
    assert all(r["report_type"] == "machine_prognostic" for r in rows)
    assert all("content" not in r for r in rows)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/api/test_reports.py -v`
Expected: FAIL — the `/api/reports` routes don't exist yet (unmatched routes fall through to the SPA catch-all when `frontend/dist` exists, or 404 otherwise; either way the JSON/status assertions fail). `ReportCreateRequest`/router import will also not exist.

- [ ] **Step 3: Implement `src/reports/service.py`**

```python
"""Report generation service (Phase 6): build summary, render, persist, fetch."""
from __future__ import annotations

import json

from src.reports import generators


def generate(conn, *, report_type, scope, format, period_start=None,
             period_end=None, generated_by=None) -> dict:
    if report_type not in generators.REPORT_TYPES:
        raise ValueError(f"unknown report_type: {report_type}")
    if format not in generators.FORMATS:
        raise ValueError(f"unknown format: {format}")

    summary = generators.build_summary(
        conn, report_type=report_type, scope=scope,
        period_start=period_start, period_end=period_end,
    )
    content = generators.render(format, summary)
    summary_json = json.dumps(summary)

    cur = conn.execute(
        """INSERT INTO reports
               (report_type, scope, format, period_start, period_end,
                generated_by, content, summary_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (report_type, scope, format, period_start, period_end,
         generated_by, content, summary_json),
    )
    conn.commit()
    return get_report(conn, cur.lastrowid)


def get_report(conn, report_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row is not None else None


def list_reports(conn, *, report_types=None, scope=None, limit=100) -> list[dict]:
    clauses = []
    params: list = []
    if report_types:
        placeholders = ",".join("?" for _ in report_types)
        clauses.append(f"report_type IN ({placeholders})")
        params.extend(report_types)
    if scope is not None:
        clauses.append("scope = ?")
        params.append(scope)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""SELECT id, report_type, scope, format, period_start, period_end,
                   generated_at, generated_by, summary_json
            FROM reports{where}
            ORDER BY id DESC LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Add `ReportCreateRequest` to `src/api/schemas.py`**

Append after the existing request models (e.g. after `ReplayStopRequest`):

```python
class ReportCreateRequest(BaseModel):
    report_type: Literal["machine_prognostic", "model_performance", "fleet_summary"]
    scope: str
    format: Literal["markdown", "json"] = "json"
    period_start: Optional[str] = None
    period_end: Optional[str] = None
```

(`Literal` and `Optional` are already imported at `src/api/schemas.py:8`.)

- [ ] **Step 5: Implement `src/api/routes/reports.py`**

```python
"""Report generation + retrieval endpoints (Phase 6).

Per-type RBAC (design spec §7): operators may generate/read machine_prognostic;
model_performance and fleet_summary require admin/supervisor. The router is
mounted under Depends(get_current_user) in app.py, so every endpoint already
requires a session; the per-type check runs inside each handler.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.api.deps import get_db
from src.api.schemas import ReportCreateRequest
from src.auth.deps import get_current_user
from src.reports import service

router = APIRouter(prefix="/reports", tags=["reports"])

_ELEVATED_TYPES = ("model_performance", "fleet_summary")
_MEDIA_TYPES = {"markdown": "text/markdown", "json": "application/json"}
_EXTENSIONS = {"markdown": "md", "json": "json"}


def _authorize_type(user: dict, report_type: str) -> None:
    if report_type in _ELEVATED_TYPES and user["role"] not in ("admin", "supervisor"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Insufficient permissions for this report type"
        )


def _present(row: dict) -> dict:
    """Parse summary_json into a `summary` object; drop the raw JSON string."""
    out = dict(row)
    raw = out.pop("summary_json", None)
    out["summary"] = json.loads(raw) if raw else None
    return out


@router.post("")
def create_report(
    payload: ReportCreateRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    _authorize_type(user, payload.report_type)
    if payload.report_type == "machine_prognostic":
        exists = db.execute(
            "SELECT 1 FROM machines WHERE machine_id = ?", (payload.scope,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown machine: {payload.scope}")
    report = service.generate(
        db,
        report_type=payload.report_type,
        scope=payload.scope,
        format=payload.format,
        period_start=payload.period_start,
        period_end=payload.period_end,
        generated_by=user["id"],
    )
    return _present(report)


@router.get("")
def list_reports_endpoint(
    db=Depends(get_db),
    user=Depends(get_current_user),
    scope: str | None = Query(default=None),
):
    report_types = ["machine_prognostic"] if user["role"] == "operator" else None
    rows = service.list_reports(db, report_types=report_types, scope=scope)
    return [_present(r) for r in rows]


@router.get("/{report_id}")
def get_report_endpoint(
    report_id: int,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    report = service.get_report(db, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    _authorize_type(user, report["report_type"])
    return _present(report)


@router.get("/{report_id}/download")
def download_report_endpoint(
    report_id: int,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    report = service.get_report(db, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    _authorize_type(user, report["report_type"])
    fmt = report["format"]
    media_type = _MEDIA_TYPES.get(fmt, "text/plain")
    ext = _EXTENSIONS.get(fmt, "txt")
    return Response(
        content=report["content"],
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="report-{report_id}.{ext}"'},
    )
```

- [ ] **Step 6: Mount the router in `src/api/app.py`**

Add `reports` to the routes import tuple (after `predictions`):

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
    reports,
    users,
)
```

Add `reports` to the authenticated mount loop:

```python
for module in (machines, alerts, maintenance, kpis, predictions, ingestion, model, reports):
    app.include_router(module.router, prefix="/api", dependencies=[Depends(get_current_user)])
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/api/test_reports.py -v`
Expected: PASS (13 tests).

- [ ] **Step 8: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green. Suite grows by 13 from the 170 Task-1 baseline (→ 183).

- [ ] **Step 9: Commit**

```bash
git add src/reports/service.py src/api/routes/reports.py src/api/schemas.py src/api/app.py tests/api/test_reports.py
git commit -m "feat: reports API (generate/list/get/download) with per-type RBAC"
```

---

## Phase 6 Review Gate (→ Phase 7)

Run `code-review` on the whole-phase diff. Focus (from the roadmap):
- **Generators read only real persisted data** — no synthesized/placeholder values (the sole derived field is `recommended_maintenance.by_timestamp`, documented).
- **CSV/markdown escaping** — `_md_cell` neutralizes `|`/newlines/backslashes so free-text (`message`, `probable_cause`) cannot break tables; no CSV in scope.
- **Large-range queries bounded** — trajectory/alerts/top-at-risk use `LIMIT`; `list_reports` has a `limit`.
- **`summary_json` matches `content`** — both derived from one `summary` object in `service.generate`.
- **RBAC per spec §7** — operators generate/read only `machine_prognostic`; elevated types require admin/supervisor; anon → 401.
- **No new deps; db.py untouched; no temperature/NASA-IMS; RUL in minutes.**

## Acceptance

- Each report type generates from seeded data with a stable `summary_json`.
- `download` returns the correct content-type and body; re-fetch by id returns the stored report.
- `pytest` green; suite at 183 (158 + 12 + 13).
