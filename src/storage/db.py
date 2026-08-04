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
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd

# Overridable via MAINTAINIQ_DB_PATH (e.g. to point at a mounted volume in
# Docker) since the file lives outside the repo tree in that case.
DEFAULT_DB_PATH = Path(os.environ.get("MAINTAINIQ_DB_PATH", str(Path(__file__).resolve().parents[2] / "maintainiq.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    machine_id TEXT PRIMARY KEY,
    source_test TEXT,
    bearing TEXT,
    is_documented_failure INTEGER
);

CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    timestamp TEXT NOT NULL,
    sensor_id TEXT,
    vibration_h_rms REAL,
    vibration_h_kurtosis REAL,
    vibration_h_high_band_energy_ratio REAL,
    temperature_c REAL,
    temperature_is_synthetic INTEGER,
    rul_hours REAL,
    features_json TEXT,
    UNIQUE(machine_id, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_readings_machine_ts ON readings(machine_id, timestamp);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reading_id INTEGER NOT NULL REFERENCES readings(id),
    machine_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    health_state TEXT NOT NULL,
    confidence REAL,
    source TEXT NOT NULL,
    model_name TEXT,
    probable_cause TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_machine_ts ON predictions(machine_id, timestamp);

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

-- Schema only for now: M5 populates this via a dashboard form/API.
CREATE TABLE IF NOT EXISTS maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    performed_at TEXT NOT NULL,
    description TEXT,
    technician TEXT,
    created_at TEXT NOT NULL
);

-- Auth + RBAC. Only 3 roles are supported (see src/auth/ — derived from
-- SRS 2.3's Maintenance Operator / Supervisor user classes plus an admin
-- role for user management); enforced with a CHECK rather than a separate
-- roles table since the set is fixed and small.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','supervisor','operator')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- One row per email actually sent (not per alert), so this table doubles as
-- both the Mailpit-backing audit log and the /notifications page's data
-- source without a join fan-out.
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

_FEATURE_PREFIX = ("vibration_h_", "vibration_v_")


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI runs sync endpoints and dependency
    # teardown across an AnyIO threadpool, so a single request's connection may
    # be created, used, and closed on different threads. Each request still gets
    # its own connection (no concurrent sharing), so this is safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for column in ("acknowledged_at TEXT", "acknowledged_by INTEGER"):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB created via SCHEMA above)
    conn.commit()


def insert_machines(conn: sqlite3.Connection, long_df: pd.DataFrame) -> None:
    machines = long_df[["machine_id", "source_test", "bearing", "is_documented_failure"]].drop_duplicates("machine_id")
    conn.executemany(
        "INSERT OR IGNORE INTO machines (machine_id, source_test, bearing, is_documented_failure) VALUES (?, ?, ?, ?)",
        [
            (r.machine_id, r.source_test, r.bearing, int(r.is_documented_failure))
            for r in machines.itertuples()
        ],
    )
    conn.commit()


def insert_readings(conn: sqlite3.Connection, long_df: pd.DataFrame) -> None:
    feature_cols = [c for c in long_df.columns if c.startswith(_FEATURE_PREFIX)]
    rows = []
    for r in long_df.itertuples():
        row_dict = r._asdict()
        features = {c: float(row_dict[c]) for c in feature_cols if pd.notna(row_dict.get(c))}
        rows.append((
            r.machine_id,
            r.timestamp.isoformat(),
            r.sensor_id,
            float(r.vibration_h_rms) if pd.notna(r.vibration_h_rms) else None,
            float(r.vibration_h_kurtosis) if pd.notna(r.vibration_h_kurtosis) else None,
            float(r.vibration_h_high_band_energy_ratio) if pd.notna(r.vibration_h_high_band_energy_ratio) else None,
            float(r.temperature_c) if pd.notna(r.temperature_c) else None,
            int(bool(r.temperature_is_synthetic)),
            float(r.rul_hours) if pd.notna(r.rul_hours) else None,
            json.dumps(features),
        ))

    conn.executemany(
        """INSERT OR IGNORE INTO readings
           (machine_id, timestamp, sensor_id, vibration_h_rms, vibration_h_kurtosis,
            vibration_h_high_band_energy_ratio, temperature_c, temperature_is_synthetic,
            rul_hours, features_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def insert_single_reading(conn: sqlite3.Connection, reading: dict) -> int:
    """Single-row counterpart to insert_readings, for the live/demo path
    (src/prediction/live.py) where there is one new reading, not a batch
    dataframe. `reading` keys: machine_id, timestamp (ISO string), sensor_id,
    vibration_h_rms, vibration_h_kurtosis, vibration_h_high_band_energy_ratio,
    temperature_c, temperature_is_synthetic, rul_hours. Returns the new row's id."""
    cur = conn.execute(
        """INSERT INTO readings
           (machine_id, timestamp, sensor_id, vibration_h_rms, vibration_h_kurtosis,
            vibration_h_high_band_energy_ratio, temperature_c, temperature_is_synthetic,
            rul_hours, features_json)
           VALUES (:machine_id, :timestamp, :sensor_id, :vibration_h_rms, :vibration_h_kurtosis,
                   :vibration_h_high_band_energy_ratio, :temperature_c, :temperature_is_synthetic,
                   :rul_hours, '{}')""",
        reading,
    )
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
