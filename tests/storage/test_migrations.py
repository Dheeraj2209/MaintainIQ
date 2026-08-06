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
