# Phase 1 — Canonical Schema & Migration Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy IMS-shaped SQLite schema with the XJTU-SY canonical model behind a versioned migration runner, adapt every runtime consumer, and archive the legacy IMS training island — leaving the full backend suite green with no `temperature_c`/`rul_hours`/`source_test`/`sensor_id` surviving outside `src/legacy/`.

**Architecture:** A new `src/storage/migrations.py` holds a numbered `(version, callable)` list recorded in a `schema_version` table. `db.init_schema` delegates to `run_migrations`. Migration 1 installs the canonical schema; on a legacy DB it **drops and recreates** the dataset tables (`machines`/`readings`/`predictions`) — they are regenerated from XJTU-SY via the Phase 2 backfill, not data-migrated — while preserving the app tables (`alerts`/`maintenance_records`/`users`/`notifications`) via guarded `ALTER`s. Runtime survivors (KPI, machine routes, schemas, demo/live path, root-cause) are adapted in place; the offline IMS training island moves to `src/legacy/`.

**Tech Stack:** Python 3, SQLite (raw SQL, `sqlite3` module, no ORM), FastAPI + Starlette `TestClient`, pytest, pandas (runtime survivors only), numpy.

## Global Constraints

- **Spec is authority.** On any conflict between this plan / the roadmap and `docs/superpowers/specs/2026-08-06-xjtu-sy-ml-integration-design.md`, the spec wins. (See "Schema reconciliation" below for the two conflicts already resolved this way.)
- **RUL unit is minutes.** Storage columns use `rul_minutes` / `predicted_rul_minutes`. Hours may still be *derived* in an API response for display (`RULPredictionResponse.predicted_rul_hours` stays) — but never stored.
- **No synthetic temperature.** XJTU-SY has no thermal channel; `temperature_c` / `temperature_is_synthetic` are dropped everywhere outside `src/legacy/`.
- **`dataset` column is the isolation seam** on `machines` and `readings`, default `'xjtu_sy'`.
- **Idempotency key** for readings is `UNIQUE(machine_id, cycle)`.
- **Legacy IMS code is archived, not deleted** — moved under `src/legacy/` (import-guarded, needed later for NASA validation). `research/` and `models/evaluation_report.json` are exempt and stay put.
- TDD (test first, watch it fail, minimal code, watch it pass, commit). DRY. YAGNI. Frequent commits.
- Environment gotchas: invoke pytest via the project's Python; the `&` background operator and `curl` are denied in this bash — do not use them.

## Schema reconciliation (spec-wins decisions, applied in this plan)

1. **`predictions` keeps `confidence`, `model_name`, `probable_cause`, `created_at`.** The roadmap's canonical DDL (roadmap lines 203-220) omits these four; spec §4.3 line 126 explicitly retains them ("Existing columns kept: id, reading_id, machine_id, timestamp, health_state, **confidence, source, model_name, probable_cause, created_at**"). Spec wins → canonical `predictions` = roadmap columns **plus** those four. This keeps `insert_predictions`, KPI `_latest_prediction`, `MachineSummary.confidence/probable_cause/prediction_source`, and the demo/live path working unchanged.
2. **Migration reshapes by drop-and-recreate, not the "copy dance."** The roadmap (lines 262-267) suggests a create-new / copy / drop-old / rename dance. But the canonical `readings` adds NOT NULL columns (`cycle`, `elapsed_minutes`, `speed_rpm`, `load_kn`, `sample_rate_hz`, `vibration_v_*`, `cross_axis_*`) that have **no source column** in the old IMS rows — copying is structurally impossible without fabricating data. Spec §4.8 mandates regeneration from XJTU-SY via backfill. Spec wins → migration 1 `DROP`s legacy `machines`/`readings`/`predictions` and recreates them canonical; app tables are preserved.

## Removed-token consumer register (regenerated from grep at plan time)

`grep -rnE "temperature_c|temperature_is_synthetic|temperature_severity|\brul_hours\b|source_test|sensor_id|vibration_h_high_band_energy_ratio"` over `src/` and `tests/`:

**Runtime survivors — adapt in place (Task 1):**
- `src/storage/db.py` — SCHEMA + `insert_*` helpers.
- `src/kpi/calculations.py` — `_latest_reading`, `_vibration_severity`, `_temperature_severity`, `_machine_health`.
- `src/api/schemas.py` — `MachineSummary.temperature_severity`.
- `src/api/routes/machines.py` — `_TREND_COLUMNS`.
- `src/prediction/live.py` — synthetic reading builder.
- `src/root_cause/rule_based.py` — `classify_probable_cause`.
- `tests/conftest.py`, `tests/test_kpi.py`, `tests/test_db.py` — fixtures/assertions.

**Legacy IMS island — archive to `src/legacy/` (Task 2):**
- `src/ingestion/ims_bearing.py`, `src/prediction/ml_model.py`, `src/prediction/router.py`, `src/prediction/rule_based.py`, `src/features/temperature.py`, `src/training/anomaly_detector.py`, `src/training/export.py`, `src/training/rul_regressor.py`, `src/training/stage_classifiers.py`, `src/training/run_pipeline.py`.
- Confirmed by grep: this island is imported by **nothing** runtime and **no** test — its only imports live in `run_pipeline.py`; the sole surviving `src.training` import is `rul_realtime.py` → `xjtu_rul` (XJTU, kept), and the sole test import is `test_xjtu_rul.py` → `xjtu_rul`.

---

## Task 1: Canonical schema cutover + migration runner

This is an **atomic cutover**: the SCHEMA change, the migration runner, every runtime consumer, and every fixture must land together — any partial application leaves the suite red (a reader SELECTing a dropped column errors). Steps are ordered so all edits happen before the final full-suite run and single commit.

**Files:**
- Create: `src/storage/migrations.py`
- Create: `tests/storage/__init__.py`, `tests/storage/test_migrations.py`
- Modify: `src/storage/db.py` (SCHEMA, `init_schema`, `insert_machines`, `insert_readings`, `insert_single_reading`, imports)
- Modify: `src/kpi/calculations.py`
- Modify: `src/api/schemas.py:23`
- Modify: `src/api/routes/machines.py:12-17`
- Modify: `src/root_cause/rule_based.py`
- Modify: `src/prediction/live.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_kpi.py:28-32`
- Modify: `tests/test_db.py:81-148`

**Interfaces:**
- Produces:
  - `src.storage.migrations.run_migrations(conn) -> int` — applies pending migrations in order, records each in `schema_version`, returns resulting version. Idempotent.
  - `src.storage.migrations.current_version(conn) -> int` — max applied version, `0` if none.
  - `db.init_schema(conn) -> None` — delegates to `run_migrations`.
  - `db.SCHEMA` — canonical DDL string (contract for all later phases).
  - `db.insert_machines(conn, machines: list[dict]) -> None` — canonical machine rows; keys `machine_id` (required), `bearing_id`, `operating_condition`, `speed_rpm`, `load_kn`, `dataset` (default `'xjtu_sy'`), `is_documented_failure` (default `0`). `INSERT OR IGNORE`.
  - `db.insert_readings(conn, readings: list[dict]) -> None` — canonical reading rows (see `_READING_COLUMNS`). `INSERT OR IGNORE` on `(machine_id, cycle)`.
  - `db.insert_single_reading(conn, reading: dict) -> int` — one canonical reading, returns new row id.
- Consumes: nothing (foundation).

---

- [ ] **Step 1: Write the failing migration tests**

Create `tests/storage/__init__.py` (empty file), then create `tests/storage/test_migrations.py`:

```python
"""Tests for the versioned migration runner (src/storage/migrations.py)."""
import sqlite3

from src.storage.migrations import current_version, run_migrations

# Old IMS-shaped DDL used to simulate a pre-canonical DB for the upgrade path.
_LEGACY_DDL = """
CREATE TABLE machines (
    machine_id TEXT PRIMARY KEY,
    source_test TEXT,
    bearing TEXT,
    is_documented_failure INTEGER
);
CREATE TABLE readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    sensor_id TEXT,
    vibration_h_rms REAL,
    temperature_c REAL,
    rul_hours REAL,
    UNIQUE(machine_id, timestamp)
);
CREATE TABLE predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reading_id INTEGER NOT NULL,
    machine_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    health_state TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    severity TEXT NOT NULL,
    health_state TEXT NOT NULL,
    probable_cause TEXT,
    message TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    performed_at TEXT NOT NULL,
    description TEXT,
    technician TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER,
    recipient_email TEXT NOT NULL,
    recipient_role TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    created_at TEXT NOT NULL
);
"""


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn, table) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_current_version_zero_on_empty_db():
    assert current_version(_mem()) == 0


def test_fresh_db_reaches_version_1():
    conn = _mem()
    assert run_migrations(conn) == 1
    assert current_version(conn) == 1


def test_run_migrations_is_idempotent():
    conn = _mem()
    run_migrations(conn)
    # Second run applies nothing and does not raise or duplicate rows.
    assert run_migrations(conn) == 1
    rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"]
    assert rows == 1


def test_fresh_db_machines_are_canonical():
    conn = _mem()
    run_migrations(conn)
    cols = _columns(conn, "machines")
    assert {"bearing_id", "operating_condition", "speed_rpm", "load_kn", "dataset"} <= cols
    assert "source_test" not in cols
    assert "bearing" not in cols


def test_fresh_db_readings_are_canonical():
    conn = _mem()
    run_migrations(conn)
    cols = _columns(conn, "readings")
    assert {"cycle", "elapsed_minutes", "vibration_v_rms", "cross_axis_rms_ratio",
            "rul_minutes", "features_json", "dataset"} <= cols
    assert "temperature_c" not in cols
    assert "rul_hours" not in cols
    assert "sensor_id" not in cols


def test_new_tables_exist():
    conn = _mem()
    run_migrations(conn)
    names = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"model_inference_log", "model_registry", "reports", "schema_version"} <= names


def test_legacy_upgrade_preserves_app_data_and_reaches_same_version():
    conn = _mem()
    conn.executescript(_LEGACY_DDL)
    conn.execute("INSERT INTO alerts (machine_id, opened_at, severity, health_state, "
                 "status, created_at) VALUES ('m1','2020-01-01T00:00:00','high',"
                 "'critical','open','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO users (email, name, hashed_password, role, created_at) "
                 "VALUES ('a@b.c','A','x','admin','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO maintenance_records (machine_id, performed_at, created_at) "
                 "VALUES ('m1','2020-01-01T00:00:00','2020-01-01T00:00:00')")
    conn.commit()

    version = run_migrations(conn)

    assert version == 1
    # App tables + their data survive.
    assert conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM maintenance_records").fetchone()["n"] == 1
    # Legacy alerts/maintenance gained the newer columns.
    assert {"acknowledged_at", "acknowledged_by"} <= _columns(conn, "alerts")
    assert {"alert_id", "type"} <= _columns(conn, "maintenance_records")
    # Dataset tables are now canonical.
    assert "source_test" not in _columns(conn, "machines")
    assert "bearing_id" in _columns(conn, "machines")
    assert "temperature_c" not in _columns(conn, "readings")
```

- [ ] **Step 2: Run the migration tests to verify they fail**

Run: `python -m pytest tests/storage/test_migrations.py -q`
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'src.storage.migrations'`.

- [ ] **Step 3: Replace `db.SCHEMA` with the canonical DDL**

In `src/storage/db.py`, replace the entire `SCHEMA = """ ... """` block (lines 32-126) with:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS machines (
    machine_id            TEXT PRIMARY KEY,
    bearing_id            TEXT,
    operating_condition   INTEGER,
    speed_rpm             REAL,
    load_kn               REAL,
    dataset               TEXT NOT NULL DEFAULT 'xjtu_sy',
    is_documented_failure INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS readings (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id             TEXT NOT NULL REFERENCES machines(machine_id),
    timestamp              TEXT NOT NULL,
    cycle                  INTEGER NOT NULL,
    elapsed_minutes        REAL NOT NULL,
    speed_rpm              REAL NOT NULL,
    load_kn                REAL NOT NULL,
    sample_rate_hz         REAL NOT NULL,
    vibration_h_rms        REAL NOT NULL,
    vibration_h_kurtosis   REAL NOT NULL,
    vibration_v_rms        REAL NOT NULL,
    vibration_v_kurtosis   REAL NOT NULL,
    cross_axis_rms_ratio   REAL NOT NULL,
    cross_axis_correlation REAL NOT NULL,
    rul_minutes            REAL,
    features_json          TEXT NOT NULL,
    dataset                TEXT NOT NULL DEFAULT 'xjtu_sy',
    UNIQUE(machine_id, cycle)
);
CREATE INDEX IF NOT EXISTS idx_readings_machine_ts ON readings(machine_id, timestamp);

CREATE TABLE IF NOT EXISTS predictions (
    id                                 INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id                         TEXT NOT NULL REFERENCES machines(machine_id),
    reading_id                         INTEGER REFERENCES readings(id),
    timestamp                          TEXT NOT NULL,
    health_state                       TEXT NOT NULL,
    confidence                         REAL,
    source                             TEXT NOT NULL DEFAULT 'xjtu_rul',
    model_name                         TEXT,
    probable_cause                     TEXT,
    created_at                         TEXT NOT NULL DEFAULT (datetime('now')),
    predicted_rul_minutes              REAL,
    rul_estimate_kind                  TEXT,
    failure_within_horizon_probability REAL,
    prognostic_horizon_minutes         REAL,
    prediction_interval_low            REAL,
    prediction_interval_high           REAL,
    model_version                      TEXT,
    out_of_distribution                INTEGER NOT NULL DEFAULT 0,
    history_snapshots                  INTEGER,
    warnings_json                      TEXT
);
CREATE INDEX IF NOT EXISTS idx_predictions_machine_ts ON predictions(machine_id, timestamp);

CREATE TABLE IF NOT EXISTS model_inference_log (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id             TEXT NOT NULL,
    timestamp              TEXT NOT NULL,
    model_version          TEXT NOT NULL,
    latency_ms             REAL NOT NULL,
    failure_probability    REAL,
    predicted_rul_minutes  REAL,
    out_of_distribution    INTEGER NOT NULL DEFAULT 0,
    warming_up             INTEGER NOT NULL DEFAULT 0,
    warnings_count         INTEGER NOT NULL DEFAULT 0,
    status                 TEXT NOT NULL DEFAULT 'ok',
    error_message          TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_inference_model_ts ON model_inference_log(model_version, timestamp);
CREATE INDEX IF NOT EXISTS idx_inference_machine_ts ON model_inference_log(machine_id, timestamp);

CREATE TABLE IF NOT EXISTS model_registry (
    model_version  TEXT PRIMARY KEY,
    artifact_path  TEXT NOT NULL,
    algorithm      TEXT,
    trained_at     TEXT,
    metrics_json   TEXT,
    deployed_at    TEXT,
    is_active      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type   TEXT NOT NULL,
    scope         TEXT,
    format        TEXT NOT NULL,
    period_start  TEXT,
    period_end    TEXT,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    generated_by  INTEGER,
    content       TEXT NOT NULL,
    summary_json  TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    severity TEXT NOT NULL,
    health_state TEXT NOT NULL,
    probable_cause TEXT,
    message TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    acknowledged_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_machine_status ON alerts(machine_id, status);

CREATE TABLE IF NOT EXISTS maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    performed_at TEXT NOT NULL,
    description TEXT,
    technician TEXT,
    created_at TEXT NOT NULL,
    alert_id INTEGER REFERENCES alerts(id),
    type TEXT CHECK(type IN ('preventive','corrective'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','supervisor','operator')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER REFERENCES alerts(id),
    recipient_email TEXT NOT NULL,
    recipient_role TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    created_at TEXT NOT NULL
);
"""
```

- [ ] **Step 4: Rewrite `db.init_schema` and the `insert_*` helpers; drop now-unused imports**

In `src/storage/db.py`:

Remove the module-level `import json`, `import pandas as pd`, and the `_FEATURE_PREFIX = (...)` line (they become unused). Keep `import os`, `import sqlite3`, `from pathlib import Path`.

Replace `init_schema` (lines 141-156) with:

```python
def init_schema(conn: sqlite3.Connection) -> None:
    # Delegates to the versioned migration runner (design §4.8). Import locally
    # to avoid a circular import: migrations.py imports SCHEMA from this module.
    from src.storage.migrations import run_migrations

    run_migrations(conn)
```

Replace `insert_machines`, `insert_readings`, `insert_single_reading` (lines 159-218) with:

```python
_READING_COLUMNS = (
    "machine_id", "timestamp", "cycle", "elapsed_minutes", "speed_rpm", "load_kn",
    "sample_rate_hz", "vibration_h_rms", "vibration_h_kurtosis", "vibration_v_rms",
    "vibration_v_kurtosis", "cross_axis_rms_ratio", "cross_axis_correlation",
    "rul_minutes", "features_json", "dataset",
)

_READING_INSERT_SQL = (
    f"INTO readings ({', '.join(_READING_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in _READING_COLUMNS)})"
)


def _normalize_reading(reading: dict) -> dict:
    """Fill a canonical reading dict: default dataset, and features_json='{}'
    when absent (the live/demo path stores no full feature vector)."""
    row = {c: reading.get(c) for c in _READING_COLUMNS}
    row["dataset"] = reading.get("dataset", "xjtu_sy")
    if row["features_json"] is None:
        row["features_json"] = "{}"
    return row


def insert_machines(conn: sqlite3.Connection, machines: list) -> None:
    """machines: list of dicts. Required key: machine_id. Optional: bearing_id,
    operating_condition, speed_rpm, load_kn, dataset (default 'xjtu_sy'),
    is_documented_failure (default 0)."""
    conn.executemany(
        """INSERT OR IGNORE INTO machines
           (machine_id, bearing_id, operating_condition, speed_rpm, load_kn,
            dataset, is_documented_failure)
           VALUES (:machine_id, :bearing_id, :operating_condition, :speed_rpm,
                   :load_kn, :dataset, :is_documented_failure)""",
        [
            {
                "machine_id": m["machine_id"],
                "bearing_id": m.get("bearing_id"),
                "operating_condition": m.get("operating_condition"),
                "speed_rpm": m.get("speed_rpm"),
                "load_kn": m.get("load_kn"),
                "dataset": m.get("dataset", "xjtu_sy"),
                "is_documented_failure": int(m.get("is_documented_failure", 0)),
            }
            for m in machines
        ],
    )
    conn.commit()


def insert_readings(conn: sqlite3.Connection, readings: list) -> None:
    """readings: list of canonical reading dicts (see _READING_COLUMNS).
    Idempotent on (machine_id, cycle)."""
    conn.executemany(
        "INSERT OR IGNORE " + _READING_INSERT_SQL,
        [_normalize_reading(r) for r in readings],
    )
    conn.commit()


def insert_single_reading(conn: sqlite3.Connection, reading: dict) -> int:
    """Single-row counterpart to insert_readings for the live/demo path
    (src/prediction/live.py). Returns the new row's id."""
    cur = conn.execute("INSERT " + _READING_INSERT_SQL, _normalize_reading(reading))
    conn.commit()
    return cur.lastrowid
```

Leave `get_connection`, `get_reading_id_map`, `insert_predictions`, `insert_alerts` unchanged (their columns all survive in the canonical schema).

- [ ] **Step 5: Create the migration runner**

Create `src/storage/migrations.py`:

```python
"""Versioned SQLite migration runner (design §4.8).

Replaces db.init_schema's try/except ALTER pattern with an explicit, ordered
list of (version, callable) steps recorded in a `schema_version` table.
run_migrations applies every step whose version exceeds the current version and
stamps each; a second call is a no-op.

Migration 1 establishes the XJTU-SY canonical baseline. On a legacy IMS DB the
dataset tables (machines/readings/predictions) are DROPPED and recreated in the
canonical shape rather than data-migrated: per design §4.8 the DB is regenerated
from XJTU-SY via the backfill, and the new NOT NULL readings columns have no
source in the old IMS rows, so an in-place column copy is impossible. The app
tables (alerts, maintenance_records, users, notifications) are preserved and
upgraded in place via guarded ALTERs.
"""
import sqlite3

from src.storage.db import SCHEMA


def current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0  # schema_version table does not exist yet
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


def _migration_001_canonical_baseline(conn: sqlite3.Connection) -> None:
    # Dataset tables are regenerated from XJTU-SY (design §4.8), never
    # data-migrated. Drop legacy IMS-shaped tables (child-first for FK sanity)
    # so the canonical CREATEs below install the new shape.
    for table in ("predictions", "readings", "machines"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    # Create every canonical table. IF NOT EXISTS preserves the app tables
    # (alerts/maintenance_records/users/notifications) and their data.
    conn.executescript(SCHEMA)
    # Upgrade pre-existing app tables that predate newer columns. On a fresh DB
    # SCHEMA already created these columns, so the ALTER raises and is ignored.
    for column in ("acknowledged_at TEXT", "acknowledged_by INTEGER"):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass
    for column in (
        "alert_id INTEGER REFERENCES alerts(id)",
        "type TEXT CHECK(type IN ('preventive','corrective'))",
    ):
        try:
            conn.execute(f"ALTER TABLE maintenance_records ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass


# Ordered list of (target_version, up_callable). Append new migrations here.
MIGRATIONS = [
    (1, _migration_001_canonical_baseline),
]


def run_migrations(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()
    version = current_version(conn)
    for target, step in MIGRATIONS:
        if target <= version:
            continue
        try:
            step(conn)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        version = target
    return version
```

- [ ] **Step 6: Run the migration tests to verify they pass**

Run: `python -m pytest tests/storage/test_migrations.py -q`
Expected: PASS (8 tests).

- [ ] **Step 7: Adapt `src/kpi/calculations.py` (remove temperature; read band ratio from features_json)**

In `src/kpi/calculations.py`:

Remove the `_HIGH_TEMP_C = 65.0` line (line 33).

Replace `_latest_reading` (lines 56-67) with:

```python
def _latest_reading(conn, machine_id: str):
    cur = conn.execute(
        """SELECT vibration_h_rms, vibration_h_kurtosis, features_json, timestamp
           FROM readings
           WHERE machine_id = ?
           ORDER BY timestamp DESC
           LIMIT 1""",
        (machine_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None
```

Replace `_vibration_severity` (lines 70-82) with (the high-band-energy ratio now lives inside the JSON feature vector, not a promoted column):

```python
def _vibration_severity(reading) -> str:
    """Coarse severity of the latest vibration reading: low/medium/high.
    Kurtosis is a promoted column; the high-band-energy ratio is read from the
    JSON feature vector (it is no longer a promoted column in the canonical
    schema)."""
    if not reading:
        return "unknown"
    kurt = reading.get("vibration_h_kurtosis")
    try:
        features = json.loads(reading.get("features_json") or "{}")
    except (TypeError, ValueError):
        features = {}
    band = features.get("vibration_h_high_band_energy_ratio")
    if kurt is None or band is None:
        return "unknown"
    if kurt >= _HIGH_KURTOSIS and band >= _HIGH_BAND_RATIO:
        return "high"
    if kurt >= _HIGH_KURTOSIS or band >= _HIGH_BAND_RATIO:
        return "medium"
    return "low"
```

Delete the entire `_temperature_severity` function (lines 85-95).

In `_machine_health` (lines 126-138), delete the line:

```python
        "temperature_severity": _temperature_severity(latest_reading),
```

(`import json` is already present at line 16 — no new import needed.)

- [ ] **Step 8: Adapt `src/api/schemas.py` and `src/api/routes/machines.py`**

In `src/api/schemas.py`, delete line 23:

```python
    temperature_severity: str
```

In `src/api/routes/machines.py`, replace `_TREND_COLUMNS` (lines 12-17) with the canonical, queryable columns:

```python
# Metrics exposable as trends map to real, queryable columns in `readings`.
_TREND_COLUMNS = {
    "vibration_h_rms": "vibration_h_rms",
    "vibration_h_kurtosis": "vibration_h_kurtosis",
    "vibration_v_rms": "vibration_v_rms",
    "vibration_v_kurtosis": "vibration_v_kurtosis",
    "cross_axis_rms_ratio": "cross_axis_rms_ratio",
    "cross_axis_correlation": "cross_axis_correlation",
    "rul_minutes": "rul_minutes",
}
```

- [ ] **Step 9: Adapt `src/root_cause/rule_based.py` (drop temperature + band-ratio branches)**

In `src/root_cause/rule_based.py`, replace the constants block (lines 15-25) with:

```python
BEARING_WEAR = "bearing_wear"
IMBALANCE = "imbalance"
SENSOR_DATA_QUALITY = "sensor_or_data_quality_issue"
UNKNOWN = "unknown"

# Threshold is heuristic, not learned — see module docstring.
HIGH_KURTOSIS = 5.0
```

Replace `classify_probable_cause` (lines 28-54) with (canonical vibration channels only; no thermal or high-band-energy promoted column exists):

```python
def classify_probable_cause(row: pd.Series) -> str:
    """Return a single probable root cause label for one machine-timestamp record.

    Only called for records already flagged degrading/faulty/critical — a healthy
    reading has no root cause to report. Uses the canonical XJTU-SY vibration
    channels (horizontal RMS + kurtosis); the IMS-era thermal and high-band-energy
    heuristics are gone with the schema. Root cause is refined alongside RUL in a
    later phase.
    """
    kurtosis = row.get("vibration_h_kurtosis")
    rms = row.get("vibration_h_rms")

    if kurtosis is None or pd.isna(kurtosis):
        return SENSOR_DATA_QUALITY

    if kurtosis >= HIGH_KURTOSIS:
        return BEARING_WEAR

    if rms is not None and not pd.isna(rms) and rms > 0:
        return IMBALANCE

    return UNKNOWN
```

Leave `ABNORMAL_STATES` (line 57) and `add_probable_cause` (lines 60-68) unchanged — `add_probable_cause` is only invoked by the archived `run_pipeline` and still works with the canonical classifier.

- [ ] **Step 10: Adapt `src/prediction/live.py` (canonical synthetic reading)**

Replace the whole body of `src/prediction/live.py` from line 22 (the `SEVERITY_MULTIPLIERS` comment/dict) through the end of `evaluate_new_reading` with:

```python
# Applied to the machine's latest real reading to synthesize a demo reading
# at the requested severity. Vibration features have a near-zero healthy
# baseline, so they scale multiplicatively (same "documented heuristic
# constant" convention as root_cause/rule_based.py's thresholds).
SEVERITY_MULTIPLIERS = {
    "healthy": 1.0,
    "degrading": 2.5,
    "faulty": 5.0,
    "critical": 9.0,
}

# Used only when a machine's latest reading is missing a value (defensive).
_FALLBACK = {
    "vibration_h_rms": 0.1,
    "vibration_h_kurtosis": 2.5,
    "vibration_v_rms": 0.1,
    "vibration_v_kurtosis": 2.5,
    "cross_axis_rms_ratio": 1.0,
    "cross_axis_correlation": 0.0,
    "speed_rpm": 2100.0,
    "load_kn": 12.0,
    "sample_rate_hz": 25_600.0,
}


class UnknownMachineError(ValueError):
    pass


def _latest_reading(conn, machine_id: str) -> dict:
    row = conn.execute(
        """SELECT cycle, elapsed_minutes, speed_rpm, load_kn, sample_rate_hz,
                  vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
                  vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation
           FROM readings WHERE machine_id = ? ORDER BY timestamp DESC LIMIT 1""",
        (machine_id,),
    ).fetchone()
    if row is None:
        raise UnknownMachineError(f"no baseline reading for machine: {machine_id}")
    return dict(row)


def _scaled(baseline: dict, key: str, multiplier: float) -> float:
    return round((baseline.get(key) or _FALLBACK[key]) * multiplier, 4)


def evaluate_new_reading(conn, machine_id: str, target_state: str) -> dict:
    """Synthesize one reading for `machine_id` at `target_state` severity,
    persist it + its prediction, and return the prediction dict (reading_id,
    machine_id, timestamp, health_state, confidence, source, model_name,
    probable_cause, created_at)."""
    baseline = _latest_reading(conn, machine_id)
    multiplier = SEVERITY_MULTIPLIERS[target_state]
    timestamp = datetime.now(timezone.utc).isoformat()

    synthetic = {
        "machine_id": machine_id,
        "timestamp": timestamp,
        # Monotonic cycle from the latest reading keeps UNIQUE(machine_id, cycle).
        "cycle": int(baseline.get("cycle") or 0) + 1,
        "elapsed_minutes": float(baseline.get("elapsed_minutes") or 0.0) + 1.0,
        "speed_rpm": baseline.get("speed_rpm") or _FALLBACK["speed_rpm"],
        "load_kn": baseline.get("load_kn") if baseline.get("load_kn") is not None else _FALLBACK["load_kn"],
        "sample_rate_hz": baseline.get("sample_rate_hz") or _FALLBACK["sample_rate_hz"],
        "vibration_h_rms": _scaled(baseline, "vibration_h_rms", multiplier),
        "vibration_h_kurtosis": _scaled(baseline, "vibration_h_kurtosis", multiplier),
        "vibration_v_rms": _scaled(baseline, "vibration_v_rms", multiplier),
        "vibration_v_kurtosis": _scaled(baseline, "vibration_v_kurtosis", multiplier),
        "cross_axis_rms_ratio": baseline.get("cross_axis_rms_ratio") or _FALLBACK["cross_axis_rms_ratio"],
        "cross_axis_correlation": baseline.get("cross_axis_correlation") if baseline.get("cross_axis_correlation") is not None else _FALLBACK["cross_axis_correlation"],
        "rul_minutes": None,  # live rows carry predicted RUL in predictions, never a label
        "features_json": "{}",
        "dataset": "xjtu_sy",
    }

    reading_id = insert_single_reading(conn, synthetic)

    probable_cause = None
    if target_state in ABNORMAL_STATES:
        probable_cause = classify_probable_cause(pd.Series(synthetic))

    prediction = {
        "reading_id": reading_id,
        "machine_id": machine_id,
        "timestamp": timestamp,
        "health_state": target_state,
        "confidence": None,
        "source": "demo",
        "model_name": "demo_simulator",
        "probable_cause": probable_cause,
        "created_at": timestamp,
    }
    insert_predictions(conn, [prediction])
    return prediction
```

Also update the module's imports/top matter: the imports at lines 16-21 (`from datetime import datetime, timezone`, `import pandas as pd`, `from src.root_cause.rule_based import ABNORMAL_STATES, classify_probable_cause`, `from src.storage.db import insert_predictions, insert_single_reading`) are all still used — leave them. The old `TEMP_DELTA_C` dict is removed by the replacement above.

- [ ] **Step 11: Update the failing KPI severity test**

In `tests/test_kpi.py`, replace `test_vibration_and_temperature_severity_from_latest_reading` (lines 28-32) with:

```python
def test_vibration_severity_from_latest_reading(conn):
    m1 = kpi.machine_health_kpis(conn, "m1")[0]
    # latest m1 reading: kurt 6.0 (>=5) and features_json band 0.5 (>=0.3) -> high
    assert m1["vibration_severity"] == "high"
```

- [ ] **Step 12: Update `tests/conftest.py` to seed the canonical schema**

In `tests/conftest.py`:

Change the import on line 14 from `from src.storage.db import SCHEMA` to:

```python
from src.storage.db import init_schema
```

Replace the body of `_populate` (lines 28-90) with:

```python
def _populate(conn: sqlite3.Connection) -> None:
    init_schema(conn)

    conn.executemany(
        """INSERT INTO machines
           (machine_id, bearing_id, operating_condition, speed_rpm, load_kn, dataset,
            is_documented_failure)
           VALUES (?,?,?,?,?,?,?)""",
        [
            ("m1", "Bearing1_1", 1, 2100.0, 12.0, "xjtu_sy", 1),  # documented failure, ends critical
            ("m2", "Bearing1_2", 1, 2100.0, 12.0, "xjtu_sy", 0),  # stays healthy
        ],
    )

    # readings: two per machine, latest last. m1's latest is high-vibration; the
    # high-band-energy ratio lives in features_json (canonical schema drops it as
    # a promoted column) so _vibration_severity can still read it.
    readings = [
        # machine, ts, cycle, elapsed, rms, kurt, v_rms, v_kurt, cross_ratio, cross_corr, rul_min, band
        ("m1", _iso(2003, 10, 22, 12, 0), 0, 0.0, 0.1, 3.0, 0.1, 3.0, 0.9, 0.4, 100.0, 0.1),
        ("m1", _iso(2003, 10, 22, 13, 0), 1, 60.0, 0.9, 6.0, 0.8, 5.5, 1.1, 0.8, 1.0, 0.5),
        ("m2", _iso(2003, 10, 22, 12, 0), 0, 0.0, 0.1, 2.5, 0.1, 2.4, 0.95, 0.1, 200.0, 0.05),
        ("m2", _iso(2003, 10, 22, 13, 0), 1, 60.0, 0.12, 2.6, 0.11, 2.5, 0.96, 0.12, 199.0, 0.06),
    ]
    conn.executemany(
        """INSERT INTO readings
           (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm, load_kn,
            sample_rate_hz, vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
            vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation,
            rul_minutes, features_json, dataset)
           VALUES (?,?,?,?, 2100.0, 12.0, 25600.0, ?,?,?,?,?,?,?,
                   json_object('vibration_h_high_band_energy_ratio', ?), 'xjtu_sy')""",
        [
            (m, ts, cyc, elapsed, rms, kurt, v_rms, v_kurt, cr_ratio, cr_corr, rul, band)
            for (m, ts, cyc, elapsed, rms, kurt, v_rms, v_kurt, cr_ratio, cr_corr, rul, band) in readings
        ],
    )

    # predictions: m1 degrading then critical (2 abnormal), m2 healthy twice.
    predictions = [
        ("m1", _iso(2003, 10, 22, 12, 0), "degrading", 0.8, "ml", "ml", "bearing_wear"),
        ("m1", _iso(2003, 10, 22, 13, 0), "critical", 0.95, "ml", "ml", "bearing_wear"),
        ("m2", _iso(2003, 10, 22, 12, 0), "healthy", None, "rule_based", "rule_based", None),
        ("m2", _iso(2003, 10, 22, 13, 0), "healthy", None, "rule_based", "rule_based", None),
    ]
    conn.executemany(
        """INSERT INTO predictions
           (reading_id, machine_id, timestamp, health_state, confidence, source, model_name,
            probable_cause, created_at)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(m, t, h, c, s, mn, pc, NOW) for (m, t, h, c, s, mn, pc) in predictions],
    )

    # alerts: m1 has one resolved (5 min) and one open.
    conn.executemany(
        """INSERT INTO alerts
           (machine_id, opened_at, resolved_at, severity, health_state, probable_cause,
            message, status, source, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [
            ("m1", _iso(2003, 10, 22, 12, 0), _iso(2003, 10, 22, 12, 5), "low",
             "degrading", "bearing_wear", "m1 degrading", "resolved", "ml", NOW),
            ("m1", _iso(2003, 10, 22, 13, 0), None, "high",
             "critical", "bearing_wear", "m1 critical", "open", "ml", NOW),
        ],
    )

    conn.executemany(
        """INSERT INTO users (email, name, hashed_password, role, is_active, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        [(email, name, hashed, role, NOW) for email, name, hashed, role in _DEMO_USER_ROWS],
    )
    conn.commit()
```

(Note: `json_object(...)` is a built-in SQLite function, so no Python-side JSON import is needed in conftest.)

- [ ] **Step 13: Update `tests/test_db.py` machine inserts to the canonical shape**

In `tests/test_db.py`, the two tests that insert into `machines` with the old `source_test`/`bearing` columns must use canonical columns (the migration drops those columns).

Replace line 86 (inside `test_maintenance_records_type_check_constraint`):

```python
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
```

Replace lines 116-123 (the inline old-shape `CREATE TABLE machines` in `test_maintenance_records_type_check_constraint_via_alter_upgrade_path`) with the legacy IMS shape it is meant to simulate (keep `source_test` here — this is the *pre-migration* legacy table the runner will drop and recreate):

```python
    conn.execute(
        """CREATE TABLE machines (
            machine_id TEXT PRIMARY KEY,
            source_test TEXT,
            bearing TEXT,
            is_documented_failure INTEGER
        )"""
    )
```

(The above block is unchanged from the current file — leave it as-is; it correctly represents the legacy pre-migration state.)

Replace line 132 (the post-`init_schema` insert, which must now use the canonical machines shape):

```python
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
```

- [ ] **Step 14: Run the full backend suite**

Run: `python -m pytest -q`
Expected: PASS (all backend tests green — migrations, db, kpi, api, demo, maintenance, notifications, realtime, auth, rul_realtime, xjtu_rul).

If any test references a removed column, fix it to the canonical equivalent following the same patterns above, then re-run.

- [ ] **Step 15: Commit**

```bash
git add src/storage/db.py src/storage/migrations.py src/kpi/calculations.py \
        src/api/schemas.py src/api/routes/machines.py src/root_cause/rule_based.py \
        src/prediction/live.py tests/storage/__init__.py tests/storage/test_migrations.py \
        tests/conftest.py tests/test_kpi.py tests/test_db.py
git commit -m "feat(storage): XJTU-SY canonical schema + versioned migration runner

Replace the IMS-shaped schema with the canonical XJTU-SY model behind a
schema_version-backed migration runner; init_schema now delegates to it.
Migration 1 regenerates dataset tables (drop+recreate per design §4.8) while
preserving alerts/maintenance/users/notifications. Adapt KPI, machine routes,
schemas, root-cause and the demo/live path off temperature/rul_hours/sensor_id;
predictions keeps confidence/model_name/probable_cause/created_at per spec §4.3.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Archive the legacy IMS training island

Move the offline IMS training/prediction island (imported by nothing runtime and no test) to `src/legacy/`, rewriting intra-island imports so the quarantined package still resolves. This satisfies spec §9 (IMS retirement) and the acceptance grep (`temperature_c`/`rul_hours`/`source_test`/`sensor_id` survive only under `src/legacy/`).

**Files:**
- Create: `src/legacy/__init__.py`
- Move (git mv): `src/ingestion/ims_bearing.py`, `src/prediction/ml_model.py`, `src/prediction/router.py`, `src/prediction/rule_based.py`, `src/features/temperature.py`, `src/training/anomaly_detector.py`, `src/training/export.py`, `src/training/rul_regressor.py`, `src/training/stage_classifiers.py`, `src/training/run_pipeline.py` → `src/legacy/`
- Modify: `src/legacy/run_pipeline.py` (import rewrites), plus any moved file whose intra-island imports need rewriting
- Modify (if present): stale re-exports in `src/prediction/__init__.py`, `src/training/__init__.py`, `src/ingestion/__init__.py`, `src/features/__init__.py`

**Interfaces:**
- Consumes: nothing runtime. Produces: nothing runtime (quarantine only).

- [ ] **Step 1: Create the legacy package**

Create `src/legacy/__init__.py`:

```python
"""Quarantined legacy IMS training + prediction island (design §9).

Archived, not deleted — retained for later NASA/IMS validation. Nothing in the
runtime API or the test suite imports this package. These modules target the
pre-XJTU IMS schema and are NOT runnable against the current canonical DB.
"""
```

- [ ] **Step 2: Move the island files with git mv**

Run:

```bash
git mv src/ingestion/ims_bearing.py src/legacy/ims_bearing.py
git mv src/prediction/ml_model.py src/legacy/ml_model.py
git mv src/prediction/router.py src/legacy/router.py
git mv src/prediction/rule_based.py src/legacy/rule_based.py
git mv src/features/temperature.py src/legacy/temperature.py
git mv src/training/anomaly_detector.py src/legacy/anomaly_detector.py
git mv src/training/export.py src/legacy/export.py
git mv src/training/rul_regressor.py src/legacy/rul_regressor.py
git mv src/training/stage_classifiers.py src/legacy/stage_classifiers.py
git mv src/training/run_pipeline.py src/legacy/run_pipeline.py
```

- [ ] **Step 3: Rewrite intra-island imports**

In `src/legacy/run_pipeline.py`, rewrite the island imports (currently lines 20-29) to point at `src.legacy.*`:

```python
from src.legacy.temperature import synthesize_temperature  # noqa: E402
from src.legacy.ims_bearing import load_all_tests  # noqa: E402
from src.legacy import ml_model, router  # noqa: E402
from src.legacy.rule_based import add_health_score_and_stage  # noqa: E402
from src.legacy.anomaly_detector import train_anomaly_detector  # noqa: E402
from src.legacy.export import export_winner  # noqa: E402
from src.legacy.rul_regressor import add_rul, train_leave_one_trajectory_out  # noqa: E402
from src.legacy.stage_classifiers import feature_columns, run_benchmark, select_winner, time_based_split  # noqa: E402
```

Then find any *other* intra-island imports among the moved files and rewrite them the same way. Run:

```bash
grep -rnE "from src\.(prediction\.(ml_model|router|rule_based)|ingestion\.ims_bearing|features\.temperature|training\.(anomaly_detector|export|rul_regressor|stage_classifiers)) import|from src\.(prediction|legacy) import (ml_model|router)" src/legacy
```

For each hit, replace the `src.prediction.` / `src.ingestion.` / `src.features.` / `src.training.` prefix with `src.legacy.`. Leave imports of modules that stayed in place (`src.storage.db`, `src.alerts.generation`, `src.root_cause.rule_based`, `src.preprocessing.*`, `src.training.xjtu_rul`) untouched.

- [ ] **Step 4: Remove stale re-exports from the vacated packages**

Check each package `__init__.py` for imports/re-exports of a moved module and delete those lines:

```bash
grep -nE "ims_bearing|ml_model|\brouter\b|rule_based|temperature|anomaly_detector|\bexport\b|rul_regressor|stage_classifiers|run_pipeline" \
     src/prediction/__init__.py src/training/__init__.py src/ingestion/__init__.py src/features/__init__.py
```

Delete only lines that import/re-export a *moved* module. (If a file has no such lines or does not exist, skip it.)

- [ ] **Step 5: Verify nothing runtime broke — run the full suite**

Run: `python -m pytest -q`
Expected: PASS (unchanged from Task 1 — the moved code is imported by nothing under test).

- [ ] **Step 6: Run the acceptance grep**

Run:

```bash
grep -rnE "\b(temperature_c|temperature_is_synthetic|rul_hours|source_test|sensor_id)\b" src
```

Expected: every returned path is under `src/legacy/`. No hits under any other `src/` subdirectory. (`predicted_rul_hours` in `src/api/schemas.py` / `src/prediction/rul_realtime.py` does NOT match `\brul_hours\b` — the preceding `_` is a word character — and is an allowed derived display field.)

Also confirm the high-band-energy promoted column is gone from live schema readers:

```bash
grep -rn "vibration_h_high_band_energy_ratio" src
```

Expected: hits only under `src/legacy/` (and none in `src/kpi`, `src/api`, `src/prediction/live.py`).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(legacy): archive IMS training island to src/legacy

Move the offline IMS training/prediction island (ims_bearing, ml_model,
router, prediction.rule_based, features.temperature, anomaly_detector, export,
rul_regressor, stage_classifiers, run_pipeline) under src/legacy, rewriting
intra-island imports. Nothing runtime or under test imports it; retained
import-guarded for later NASA/IMS validation (design §9). No temperature_c/
rul_hours/source_test/sensor_id survives outside src/legacy.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Review Gate (Phase 1 → Phase 2)

After Task 2 commits, run the `code-review` skill on the Phase 1 diff (`git diff main...HEAD`). Review focus per the roadmap:
- Migration is idempotent and loses no `alerts`/`maintenance_records`/`users`/`notifications` data.
- Every removed-token grep hit is handled; nothing outside `src/legacy/` references `temperature_c`/`rul_hours`/`source_test`/`sensor_id`.
- Fresh DB and an upgraded legacy DB both reach `schema_version = 1`.
- Full backend suite green.

Then **pause for user approval** before expanding Phase 2.

## Self-Review (author checklist — completed at plan time)

1. **Spec/roadmap coverage:** migration runner + `schema_version` (Task 1 Steps 1-6); canonical DDL incl. the 4 new tables (Step 3); every Ripple-Effect Register consumer (kpi/schemas/machines Steps 7-8, root_cause Step 9, live Step 10); fixtures (Steps 11-13); archive (Task 2). The two schema conflicts are reconciled spec-first and documented.
2. **Placeholder scan:** none — every step carries literal code and exact commands.
3. **Type consistency:** `run_migrations(conn) -> int`, `current_version(conn) -> int`, `insert_readings(conn, list[dict])`, `insert_single_reading(conn, dict) -> int`, `_READING_COLUMNS`, and the canonical column names are used identically across db.py, migrations.py, live.py, conftest, and the tests.
```

**Frontend note (`npm test`):** the roadmap's Phase 1 acceptance also lists `npm test` green "with temperature fields removed from any frontend fixtures/types touched here." Phase 1 touches **no** frontend files — the API still returns the same `MachineSummary` minus `temperature_severity`, and the frontend temperature fields/trend option are explicitly scheduled for Phase 7 (Ripple Register row: "frontend fields ... Phase 7"). No frontend change is in scope for Phase 1; `npm test` should be unaffected. Verify it is still green as a sanity check before the review gate, but do not modify frontend types here.
