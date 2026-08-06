"""SQLite schema and access for the M3 storage layer.

Design note (documented limitation, per this project's "flag limitations"
convention): the plan calls for separate "raw readings" and "feature
windows" tables, but the IMS ingestion path
(src/ingestion/ims_bearing.py + src/preprocessing/cleaning.py) only ever
produces pre-windowed feature rows — there is no raw waveform available to
store separately. The `readings` table below therefore represents both at
once (one row per machine per timestamp, holding its extracted features).
A future M6 raw-telemetry source that windows on the fly would be the first
real user of a genuinely separate raw-readings table.

Feature columns vary across machines (IMS Test 1 has a second vibration
channel that Tests 2/3 lack — see stage_classifiers.py's feature_columns
docstring), so the full feature set per row is stored as a JSON blob
(`features_json`) rather than as many sparse/nullable columns. The handful
of columns every downstream module actually filters or sorts on
(vibration_h_rms, vibration_h_kurtosis, temperature_c, ...) are promoted to
real columns for queryability.
"""
import os
import sqlite3
from pathlib import Path

# Overridable via MAINTAINIQ_DB_PATH (e.g. to point at a mounted volume in
# Docker) since the file lives outside the repo tree in that case.
DEFAULT_DB_PATH = Path(os.environ.get("MAINTAINIQ_DB_PATH", str(Path(__file__).resolve().parents[2] / "maintainiq.db")))

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


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI runs sync endpoints and dependency
    # teardown across an AnyIO threadpool, so a single request's connection may
    # be created, used, and closed on different threads. Each request still gets
    # its own connection (no concurrent sharing), so this is safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    # Delegates to the versioned migration runner (design §4.8). Import locally
    # to avoid a circular import: migrations.py imports SCHEMA from this module.
    from src.storage.migrations import run_migrations

    run_migrations(conn)


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


def get_reading_id_map(conn: sqlite3.Connection) -> dict:
    """(machine_id, timestamp_iso) -> reading_id, for linking predictions/alerts
    to the reading row inserted by insert_readings."""
    cur = conn.execute("SELECT id, machine_id, timestamp FROM readings")
    return {(row["machine_id"], row["timestamp"]): row["id"] for row in cur.fetchall()}


def insert_predictions(conn: sqlite3.Connection, predictions: list) -> None:
    """predictions: list of dicts with keys reading_id, machine_id, timestamp,
    health_state, confidence, source, model_name, probable_cause, created_at."""
    conn.executemany(
        """INSERT INTO predictions
           (reading_id, machine_id, timestamp, health_state, confidence, source,
            model_name, probable_cause, created_at)
           VALUES (:reading_id, :machine_id, :timestamp, :health_state, :confidence,
                   :source, :model_name, :probable_cause, :created_at)""",
        predictions,
    )
    conn.commit()


def insert_alerts(conn: sqlite3.Connection, alerts: list) -> None:
    """alerts: list of dicts with keys machine_id, opened_at, resolved_at, severity,
    health_state, probable_cause, message, status, source, created_at."""
    conn.executemany(
        """INSERT INTO alerts
           (machine_id, opened_at, resolved_at, severity, health_state, probable_cause,
            message, status, source, created_at)
           VALUES (:machine_id, :opened_at, :resolved_at, :severity, :health_state,
                   :probable_cause, :message, :status, :source, :created_at)""",
        alerts,
    )
    conn.commit()
