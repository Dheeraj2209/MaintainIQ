"""Tests for the versioned migration runner (src/storage/migrations.py)."""
import sqlite3

import pytest

from src.storage.migrations import MIGRATIONS, current_version, run_migrations

LATEST = MIGRATIONS[-1][0]

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


def test_fresh_db_reaches_latest_version():
    conn = _mem()
    assert run_migrations(conn) == LATEST
    assert current_version(conn) == LATEST


def test_run_migrations_is_idempotent():
    conn = _mem()
    run_migrations(conn)
    # Second run applies nothing and does not raise or duplicate rows.
    assert run_migrations(conn) == LATEST
    rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"]
    assert rows == len(MIGRATIONS)


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

    assert version == LATEST
    # App tables + their data survive.
    assert conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM maintenance_records").fetchone()["n"] == 1
    # Legacy alerts/maintenance gained the newer columns.
    assert {"acknowledged_at", "acknowledged_by", "page_level", "last_paged_at"} <= _columns(conn, "alerts")
    # An open alert that predates the paging ladder is marked as already at
    # the top level (never paged by the ladder: last_paged_at stays NULL).
    row = conn.execute("SELECT page_level, last_paged_at FROM alerts").fetchone()
    assert (row["page_level"], row["last_paged_at"]) == (2, None)
    assert {"prediction_id", "reading_id", "model_version", "closed_by"} <= _columns(conn, "alerts")
    assert conn.execute("SELECT prediction_id FROM alerts").fetchone()["prediction_id"] is None
    assert {"alert_id", "type"} <= _columns(conn, "maintenance_records")
    # Dataset tables are now canonical.
    assert "source_test" not in _columns(conn, "machines")
    assert "bearing_id" in _columns(conn, "machines")
    assert "temperature_c" not in _columns(conn, "readings")


# --- Migration 2: M6 live-telemetry tables -------------------------------------

def _v1_db() -> sqlite3.Connection:
    """A DB as it existed before M6: migration 1 applied and stamped, and no
    telemetry tables. Built by running only step 1 then dropping the tables
    that today's SCHEMA (which migration 1 executes) also creates."""
    conn = _mem()
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    MIGRATIONS[0][1](conn)
    conn.execute("DROP TABLE IF EXISTS telemetry_messages")
    conn.execute("DROP TABLE IF EXISTS device_status")
    conn.execute("DROP TABLE IF EXISTS device_incidents")
    conn.execute("DROP TABLE IF EXISTS work_order_events")
    conn.execute("DROP TABLE IF EXISTS work_orders")
    conn.execute("DROP TABLE IF EXISTS alert_feedback")
    conn.execute("DROP TABLE IF EXISTS alert_explanations")
    conn.execute("DROP TABLE IF EXISTS push_subscriptions")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    return conn


def _tables(conn) -> set:
    return {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_db_has_telemetry_tables():
    conn = _mem()
    run_migrations(conn)
    assert {"telemetry_messages", "device_status"} <= _tables(conn)
    assert {"device_id", "boot_id", "seq", "machine_id", "sampled_at", "received_at",
            "buffered", "time_synced", "status", "error", "reading_id", "latency_ms",
            "payload_bytes"} <= _columns(conn, "telemetry_messages")
    assert {"device_id", "online", "last_seen_at", "buffer_depth", "buffer_capacity",
            "buffer_dropped_total", "publish_attempts_total", "publish_failures_total",
            "wifi_rssi_dbm", "payload_json"} <= _columns(conn, "device_status")


def test_v1_db_upgrades_to_v2_preserving_data():
    conn = _v1_db()
    assert current_version(conn) == 1
    assert "telemetry_messages" not in _tables(conn)
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
    conn.execute("INSERT INTO users (email, name, hashed_password, role, created_at) "
                 "VALUES ('a@b.c','A','x','admin','2020-01-01T00:00:00')")
    conn.commit()

    assert run_migrations(conn) == LATEST

    assert {"telemetry_messages", "device_status"} <= _tables(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
    versions = [r["version"] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version")]
    assert versions == list(range(1, LATEST + 1))
    # Idempotent: a re-run neither re-stamps nor fails on existing tables.
    assert run_migrations(conn) == LATEST
    assert conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == LATEST


def test_v2_upgrade_is_safe_when_tables_already_exist():
    # e.g. a v1 DB that was opened by newer code whose SCHEMA already had the
    # tables: migration 2 must not fail on CREATE of an existing table.
    conn = _mem()
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    MIGRATIONS[0][1](conn)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    assert run_migrations(conn) == LATEST


def test_telemetry_idempotency_key_and_status_check():
    conn = _mem()
    run_migrations(conn)
    row = ("d1", "b1", 7, "accepted", "2026-10-06T00:00:00Z")
    sql = ("INSERT INTO telemetry_messages (device_id, boot_id, seq, status, received_at) "
           "VALUES (?,?,?,?,?)")
    conn.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("d1", "b1", 8, "bogus", "2026-10-06T00:00:00Z"))
    # Defaults: buffered=0, time_synced=1.
    r = conn.execute("SELECT buffered, time_synced FROM telemetry_messages").fetchone()
    assert (r["buffered"], r["time_synced"]) == (0, 1)


# --- Migration 3: device health (design/2026-10-06-device-health-design.md) -----

_PRE_V3_NOTIFICATIONS = """
CREATE TABLE notifications (
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

_DEVICE_INCIDENT_INDEXES = {
    "idx_device_incidents_device_status",
    "idx_device_incidents_opened",
    "uq_device_incidents_one_open",
}


def _v2_db() -> sqlite3.Connection:
    """A DB as it existed before device health: steps 1 and 2 applied and
    stamped, no device_incidents, and notifications in its pre-v3 shape."""
    conn = _mem()
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    MIGRATIONS[0][1](conn)
    MIGRATIONS[1][1](conn)
    conn.execute("DROP TABLE IF EXISTS device_incidents")
    conn.execute("DROP TABLE IF EXISTS work_order_events")
    conn.execute("DROP TABLE IF EXISTS work_orders")
    conn.execute("DROP TABLE IF EXISTS alert_feedback")
    conn.execute("DROP TABLE IF EXISTS alert_explanations")
    conn.execute("DROP TABLE IF EXISTS push_subscriptions")
    conn.execute("DROP TABLE notifications")
    conn.executescript(_PRE_V3_NOTIFICATIONS)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    conn.commit()
    return conn


def _indexes(conn, table) -> set:
    return {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = ?", (table,))}


def test_v2_db_upgrades_to_v3_preserving_data():
    conn = _v2_db()
    assert current_version(conn) == 2
    assert "device_incidents" not in _tables(conn)
    assert "device_incident_id" not in _columns(conn, "notifications")
    conn.execute("INSERT INTO device_status (device_id, online, last_seen_at) "
                 "VALUES ('d1', 1, '2026-10-06T00:00:00Z')")
    conn.execute("INSERT INTO telemetry_messages (device_id, status, received_at) "
                 "VALUES ('d1', 'accepted', '2026-10-06T00:00:00Z')")
    conn.execute("INSERT INTO users (email, name, hashed_password, role, created_at) "
                 "VALUES ('a@b.c','A','x','admin','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO notifications (recipient_email, subject, body, created_at) "
                 "VALUES ('a@b.c','s','b','2020-01-01T00:00:00')")
    conn.commit()

    assert run_migrations(conn) == LATEST

    for table in ("device_status", "telemetry_messages", "users", "notifications"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 1
    assert "device_incidents" in _tables(conn)
    assert _DEVICE_INCIDENT_INDEXES <= _indexes(conn, "device_incidents")
    assert "device_incident_id" in _columns(conn, "notifications")
    versions = [r["version"] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version")]
    assert versions == list(range(1, LATEST + 1))
    assert 3 in versions
    assert run_migrations(conn) == LATEST
    assert conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == LATEST


def test_fresh_db_has_device_incidents_and_constraints():
    conn = _mem()
    run_migrations(conn)
    assert {"id", "device_id", "machine_id", "kind", "status", "opened_at", "last_seen_at",
            "resolved_at", "acknowledged_at", "acknowledged_by",
            "created_at"} <= _columns(conn, "device_incidents")
    assert "device_incident_id" in _columns(conn, "notifications")
    assert _DEVICE_INCIDENT_INDEXES <= _indexes(conn, "device_incidents")

    sql = ("INSERT INTO device_incidents (device_id, kind, status, opened_at, created_at) "
           "VALUES (?, ?, ?, '2026-10-06T00:00:00Z', '2026-10-06T00:00:00Z')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("d1", "bogus", "open"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("d1", "silent", "closed"))
    # Many resolved incidents per device, but only one open one.
    conn.execute(sql, ("d1", "silent", "resolved"))
    conn.execute(sql, ("d1", "lwt", "resolved"))
    conn.execute(sql, ("d1", "silent", "open"))
    conn.execute(sql, ("d2", "silent", "open"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("d1", "lwt", "open"))
    # Default status is open.
    conn.execute("INSERT INTO device_incidents (device_id, kind, opened_at, created_at) "
                 "VALUES ('d3', 'silent', 'x', 'x')")
    assert conn.execute("SELECT status FROM device_incidents WHERE device_id = 'd3'"
                        ).fetchone()["status"] == "open"


# --- Migration 4: work orders + paging (design/2026-10-07-work-orders-escalation-design.md)

_PRE_V4_ALERTS = """
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
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    acknowledged_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_machine_status ON alerts(machine_id, status);
"""

_WORK_ORDER_INDEXES = {
    "idx_work_orders_status",
    "idx_work_orders_assignee",
    "uq_work_orders_one_active_per_alert",
}


def _v3_db(conn=None) -> sqlite3.Connection:
    """A DB as it existed before work orders: steps 1-3 applied and stamped, no
    work-order tables, and alerts without page_level / last_paged_at."""
    conn = conn or _mem()
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    for _, step in MIGRATIONS[:3]:
        step(conn)
    conn.execute("DROP TABLE IF EXISTS work_order_events")
    conn.execute("DROP TABLE IF EXISTS work_orders")
    conn.execute("DROP TABLE IF EXISTS alert_feedback")
    conn.execute("DROP TABLE IF EXISTS alert_explanations")
    conn.execute("DROP TABLE IF EXISTS push_subscriptions")
    conn.execute("DROP TABLE alerts")
    conn.executescript(_PRE_V4_ALERTS)
    for version in (1, 2, 3):
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    return conn


def test_v3_db_upgrades_to_v4_preserving_data():
    conn = _v3_db()
    assert current_version(conn) == 3
    assert "work_orders" not in _tables(conn)
    assert "page_level" not in _columns(conn, "alerts")
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
    conn.execute("INSERT INTO alerts (machine_id, opened_at, severity, health_state, status, "
                 "created_at, acknowledged_by) VALUES ('m1','2020-01-01T00:00:00','high',"
                 "'critical','open','2020-01-01T00:00:00', 1)")
    conn.execute("INSERT INTO maintenance_records (machine_id, performed_at, created_at) "
                 "VALUES ('m1','2020-01-01T00:00:00','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO users (email, name, hashed_password, role, created_at) "
                 "VALUES ('a@b.c','A','x','admin','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO notifications (recipient_email, subject, body, created_at) "
                 "VALUES ('a@b.c','s','b','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO device_incidents (device_id, kind, opened_at, created_at) "
                 "VALUES ('d1','silent','x','x')")
    conn.commit()

    assert run_migrations(conn) == LATEST

    for table in ("alerts", "maintenance_records", "users", "notifications", "device_incidents"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 1
    alert = conn.execute("SELECT * FROM alerts").fetchone()
    assert alert["page_level"] == 2  # open pre-ladder alert: never paged by the ladder
    assert alert["last_paged_at"] is None
    assert alert["acknowledged_by"] == 1
    assert alert["prediction_id"] is None and alert["closed_by"] is None
    assert {"work_orders", "work_order_events"} <= _tables(conn)
    assert _WORK_ORDER_INDEXES <= _indexes(conn, "work_orders")
    assert "idx_work_order_events_order" in _indexes(conn, "work_order_events")
    versions = [r["version"] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version")]
    assert versions == list(range(1, LATEST + 1))
    assert 4 in versions
    assert run_migrations(conn) == LATEST
    assert conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == LATEST


def _insert_wo(conn, *, alert_id=None, status="open", priority="medium"):
    return conn.execute(
        "INSERT INTO work_orders (alert_id, machine_id, status, priority, title, created_at, updated_at) "
        "VALUES (?, 'm1', ?, ?, 't', 'x', 'x')",
        (alert_id, status, priority),
    ).lastrowid


def test_fresh_db_work_order_constraints():
    conn = _mem()
    run_migrations(conn)
    assert {"page_level", "last_paged_at"} <= _columns(conn, "alerts")
    assert {"id", "alert_id", "machine_id", "status", "priority", "title", "description",
            "assigned_to", "created_by", "due_at", "created_at", "updated_at", "started_at",
            "completed_at", "cancelled_at", "maintenance_record_id",
            "notes"} <= _columns(conn, "work_orders")
    assert {"id", "work_order_id", "event", "from_status", "to_status", "user_id",
            "assigned_to", "note", "created_at"} <= _columns(conn, "work_order_events")
    assert _WORK_ORDER_INDEXES <= _indexes(conn, "work_orders")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_wo(conn, status="closed")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_wo(conn, priority="urgent")
    wo = _insert_wo(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO work_order_events (work_order_id, event, created_at) "
                     "VALUES (?, 'reopened', 'x')", (wo,))
    conn.execute("INSERT INTO work_order_events (work_order_id, event, created_at) "
                 "VALUES (?, 'created', 'x')", (wo,))

    # One active order per alert ...
    first = _insert_wo(conn, alert_id=7)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_wo(conn, alert_id=7, status="assigned")
    # ... but a new one is allowed once the first is finished or cancelled.
    conn.execute("UPDATE work_orders SET status = 'done' WHERE id = ?", (first,))
    second = _insert_wo(conn, alert_id=7)
    conn.execute("UPDATE work_orders SET status = 'cancelled' WHERE id = ?", (second,))
    _insert_wo(conn, alert_id=7, status="in_progress")
    # Free-standing orders are unconstrained.
    _insert_wo(conn)
    _insert_wo(conn)


# --- Migration 5: prediction feedback (design/2026-10-07-prediction-feedback-design.md)

_PRE_V5_ALERTS = """
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
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    acknowledged_by INTEGER,
    page_level INTEGER NOT NULL DEFAULT 0,
    last_paged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_machine_status ON alerts(machine_id, status);
"""

_V5_ALERT_COLUMNS = {"prediction_id", "reading_id", "model_version", "closed_by"}


def _v4_db(conn=None) -> sqlite3.Connection:
    """A DB as it existed before prediction feedback: steps 1-4 applied and
    stamped, no alert_feedback, and alerts without the four link columns."""
    conn = conn or _mem()
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    for _, step in MIGRATIONS[:4]:
        step(conn)
    conn.execute("DROP TABLE IF EXISTS alert_feedback")
    conn.execute("DROP TABLE IF EXISTS alert_explanations")
    conn.execute("DROP TABLE IF EXISTS push_subscriptions")
    conn.execute("DROP TABLE alerts")
    conn.executescript(_PRE_V5_ALERTS)
    for version in (1, 2, 3, 4):
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    return conn


def test_v4_db_upgrades_to_v5_preserving_data():
    conn = _v4_db()
    assert current_version(conn) == 4
    assert "alert_feedback" not in _tables(conn)
    assert not (_V5_ALERT_COLUMNS & _columns(conn, "alerts"))
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
    conn.execute("INSERT INTO alerts (machine_id, opened_at, severity, health_state, status, "
                 "created_at, acknowledged_by, page_level) VALUES ('m1','2020-01-01T00:00:00',"
                 "'high','critical','open','2020-01-01T00:00:00', 1, 2)")
    conn.execute("INSERT INTO maintenance_records (machine_id, performed_at, created_at) "
                 "VALUES ('m1','2020-01-01T00:00:00','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO users (email, name, hashed_password, role, created_at) "
                 "VALUES ('a@b.c','A','x','admin','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO notifications (recipient_email, subject, body, created_at) "
                 "VALUES ('a@b.c','s','b','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO device_incidents (device_id, kind, opened_at, created_at) "
                 "VALUES ('d1','silent','x','x')")
    wo = _insert_wo(conn, alert_id=1)
    conn.execute("INSERT INTO work_order_events (work_order_id, event, created_at) "
                 "VALUES (?, 'created', 'x')", (wo,))
    conn.commit()

    assert run_migrations(conn) == LATEST

    for table in ("alerts", "maintenance_records", "users", "notifications", "device_incidents",
                  "work_orders", "work_order_events"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 1
    alert = conn.execute("SELECT * FROM alerts").fetchone()
    assert (alert["page_level"], alert["acknowledged_by"]) == (2, 1)
    assert _V5_ALERT_COLUMNS <= _columns(conn, "alerts")
    assert all(alert[c] is None for c in _V5_ALERT_COLUMNS)
    assert "alert_feedback" in _tables(conn)
    assert "idx_alert_feedback_outcome" in _indexes(conn, "alert_feedback")
    versions = [r["version"] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version")]
    assert versions == list(range(1, LATEST + 1))
    assert 5 in versions
    assert run_migrations(conn) == LATEST
    assert conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == LATEST


def _insert_feedback(conn, alert_id, outcome="false_alarm", cause=None, failure_at=None):
    conn.execute(
        "INSERT INTO alert_feedback (alert_id, outcome, actual_cause, actual_failure_at, "
        "recorded_by, recorded_at) VALUES (?, ?, ?, ?, 1, 'x')",
        (alert_id, outcome, cause, failure_at),
    )


def test_fresh_db_alert_feedback_constraints():
    conn = _mem()
    run_migrations(conn)
    assert _V5_ALERT_COLUMNS <= _columns(conn, "alerts")
    assert {"id", "alert_id", "outcome", "actual_cause", "actual_failure_at", "notes",
            "work_order_id", "recorded_by", "recorded_at", "updated_by",
            "updated_at"} <= _columns(conn, "alert_feedback")
    assert "model_version" not in _columns(conn, "alert_feedback")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_feedback(conn, 1, outcome="closed")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_feedback(conn, 1, cause="gremlins")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_feedback(conn, 1, outcome="false_alarm", failure_at="2026-10-07T00:00:00+00:00")
    _insert_feedback(conn, 1, cause=None)
    _insert_feedback(conn, 2, cause="other")
    _insert_feedback(conn, 3, outcome="confirmed_failure", failure_at="2026-10-07T00:00:00+00:00")
    # One row per alert.
    with pytest.raises(sqlite3.IntegrityError):
        _insert_feedback(conn, 1, outcome="unknown")


# --- ensure_current_schema (lazy upgrade, decision 11) -------------------------------

def _file_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def reset_schema_memo():
    from src.storage import migrations

    migrations._reset_current_schema_memo()
    yield
    migrations._reset_current_schema_memo()


def test_ensure_current_schema_upgrades_a_v3_file(tmp_path, reset_schema_memo):
    from src.storage.migrations import ensure_current_schema

    conn = _v3_db(_file_conn(tmp_path / "v3.db"))
    assert ensure_current_schema(conn) == LATEST
    assert current_version(conn) == LATEST
    assert "page_level" in _columns(conn, "alerts")


def test_ensure_current_schema_upgrades_a_v4_file(tmp_path, reset_schema_memo):
    from src.storage.migrations import ensure_current_schema

    conn = _v4_db(_file_conn(tmp_path / "v4.db"))
    assert ensure_current_schema(conn) == LATEST
    assert current_version(conn) == LATEST
    assert "prediction_id" in _columns(conn, "alerts")
    assert "alert_feedback" in _tables(conn)


def test_ensure_current_schema_never_touches_a_v0_file(tmp_path, reset_schema_memo):
    from src.storage.migrations import ensure_current_schema

    conn = _file_conn(tmp_path / "legacy.db")
    conn.executescript(_LEGACY_DDL)
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
    conn.commit()
    assert ensure_current_schema(conn) == 0
    assert "source_test" in _columns(conn, "machines")
    assert conn.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"] == 1


def test_ensure_current_schema_memoises_a_current_file(tmp_path, reset_schema_memo, monkeypatch):
    from src.storage import migrations

    path = tmp_path / "v3.db"
    conn = _v3_db(_file_conn(path))
    calls = []
    real = migrations.run_migrations
    monkeypatch.setattr(migrations, "run_migrations", lambda c: calls.append(1) or real(c))
    assert migrations.ensure_current_schema(conn) == LATEST
    assert calls == [1]

    seen = []
    real_version = migrations.current_version
    monkeypatch.setattr(migrations, "current_version", lambda c: seen.append(1) or real_version(c))
    assert migrations.ensure_current_schema(_file_conn(path)) == LATEST
    assert calls == [1] and seen == []


def test_ensure_current_schema_concurrent_callers_stamp_once(tmp_path, reset_schema_memo):
    import threading

    from src.storage.migrations import ensure_current_schema

    path = tmp_path / "v3.db"
    _v3_db(_file_conn(path)).close()
    barrier = threading.Barrier(2)
    errors = []

    def worker():
        conn = _file_conn(path)
        try:
            barrier.wait()
            ensure_current_schema(conn)
        except Exception as exc:  # surfaced by the assert below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == []
    check = _file_conn(path)
    assert check.execute("SELECT COUNT(*) AS n FROM schema_version WHERE version = 4"
                         ).fetchone()["n"] == 1


# --- Migration 6: alert explanations ----------------------------------------------------

def _v5_db(conn=None) -> sqlite3.Connection:
    """A DB as it existed before alert explanations: steps 1-5 applied and
    stamped, and no alert_explanations table."""
    conn = conn or _mem()
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    for _, step in MIGRATIONS[:5]:
        step(conn)
    conn.execute("DROP TABLE IF EXISTS alert_explanations")
    conn.execute("DROP TABLE IF EXISTS push_subscriptions")
    for version in (1, 2, 3, 4, 5):
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    return conn


def test_v5_db_upgrades_to_v6_preserving_data():
    conn = _v5_db()
    assert current_version(conn) == 5
    assert "alert_explanations" not in _tables(conn)
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
    conn.execute("INSERT INTO alerts (machine_id, opened_at, severity, health_state, status, "
                 "created_at, reading_id, model_version) VALUES ('m1','2020-01-01T00:00:00',"
                 "'high','critical','open','2020-01-01T00:00:00', 7, 'v1')")
    conn.execute("INSERT INTO maintenance_records (machine_id, performed_at, created_at) "
                 "VALUES ('m1','2020-01-01T00:00:00','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO users (email, name, hashed_password, role, created_at) "
                 "VALUES ('a@b.c','A','x','admin','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO notifications (recipient_email, subject, body, created_at) "
                 "VALUES ('a@b.c','s','b','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO device_incidents (device_id, kind, opened_at, created_at) "
                 "VALUES ('d1','silent','x','x')")
    wo = _insert_wo(conn, alert_id=1)
    conn.execute("INSERT INTO work_order_events (work_order_id, event, created_at) "
                 "VALUES (?, 'created', 'x')", (wo,))
    _insert_feedback(conn, 1)
    conn.commit()

    assert run_migrations(conn) == LATEST

    for table in ("alerts", "maintenance_records", "users", "notifications", "device_incidents",
                  "work_orders", "work_order_events", "alert_feedback"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 1
    alert = conn.execute("SELECT * FROM alerts").fetchone()
    assert (alert["reading_id"], alert["model_version"]) == (7, "v1")
    assert "alert_explanations" in _tables(conn)
    assert {"idx_alert_explanations_alert", "uq_alert_explanations_one_created"} <= _indexes(
        conn, "alert_explanations")
    versions = [r["version"] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version")]
    assert versions == list(range(1, LATEST + 1))
    assert 6 in versions
    assert run_migrations(conn) == LATEST
    assert conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == LATEST


def _insert_explanation(conn, alert_id, kind="created", body="{}"):
    conn.execute("INSERT INTO alert_explanations (alert_id, kind, created_at, explanation_json) "
                 "VALUES (?, ?, 'x', ?)", (alert_id, kind, body))


def test_fresh_db_alert_explanation_constraints():
    conn = _mem()
    run_migrations(conn)
    assert {"id", "alert_id", "kind", "reading_id", "prediction_id", "created_at",
            "explanation_json"} == _columns(conn, "alert_explanations")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_explanation(conn, 1, kind="other")
    _insert_explanation(conn, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_explanation(conn, 1)  # one creation snapshot per alert
    _insert_explanation(conn, 1, kind="escalated")
    _insert_explanation(conn, 1, kind="escalated")
    _insert_explanation(conn, 2)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_explanation(conn, 3, body=None)
    assert conn.execute("SELECT COUNT(*) AS n FROM alert_explanations").fetchone()["n"] == 4


def test_ensure_current_schema_upgrades_a_v5_file(tmp_path, reset_schema_memo):
    from src.storage.migrations import ensure_current_schema

    conn = _v5_db(_file_conn(tmp_path / "v5.db"))
    assert ensure_current_schema(conn) == LATEST
    assert current_version(conn) == LATEST
    assert "alert_explanations" in _tables(conn)


# --- Migration 7: Web Push subscriptions + notifications.channel ------------------------

def _v6_db(conn=None) -> sqlite3.Connection:
    """A DB as it existed before the mobile operator view: steps 1-6 applied
    and stamped, no push_subscriptions, and notifications without channel."""
    conn = conn or _mem()
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    for _, step in MIGRATIONS[:6]:
        step(conn)
    conn.execute("DROP TABLE IF EXISTS push_subscriptions")
    conn.execute("ALTER TABLE notifications DROP COLUMN channel")
    for version in (1, 2, 3, 4, 5, 6):
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    return conn


def test_v6_db_upgrades_to_v7_preserving_data():
    conn = _v6_db()
    assert current_version(conn) == 6
    assert "push_subscriptions" not in _tables(conn)
    assert "channel" not in _columns(conn, "notifications")
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
    conn.execute("INSERT INTO alerts (machine_id, opened_at, severity, health_state, status, "
                 "created_at) VALUES ('m1','2020-01-01T00:00:00','high','critical','open',"
                 "'2020-01-01T00:00:00')")
    conn.execute("INSERT INTO users (email, name, hashed_password, role, created_at) "
                 "VALUES ('a@b.c','A','x','admin','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO device_incidents (device_id, kind, opened_at, created_at) "
                 "VALUES ('d1','silent','x','x')")
    conn.execute("INSERT INTO notifications (alert_id, recipient_email, subject, body, created_at) "
                 "VALUES (1, 'a@b.c','s','b','2020-01-01T00:00:00')")
    conn.execute("INSERT INTO notifications (device_incident_id, recipient_email, subject, body, "
                 "created_at) VALUES (1, 'a@b.c','s','b','2020-01-01T00:00:01')")
    _insert_wo(conn, alert_id=1)
    _insert_feedback(conn, 1)
    _insert_explanation(conn, 1)
    conn.commit()

    assert run_migrations(conn) == LATEST

    assert "push_subscriptions" in _tables(conn)
    assert "idx_push_subscriptions_user" in _indexes(conn, "push_subscriptions")
    rows = conn.execute("SELECT * FROM notifications ORDER BY id").fetchall()
    assert [r["channel"] for r in rows] == ["email", "email"]
    assert (rows[0]["alert_id"], rows[1]["device_incident_id"]) == (1, 1)
    for table in ("alerts", "users", "device_incidents", "work_orders", "alert_feedback",
                  "alert_explanations"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 1
    versions = [r["version"] for r in conn.execute(
        "SELECT version FROM schema_version ORDER BY version")]
    assert versions == list(range(1, LATEST + 1))
    assert 7 in versions
    assert run_migrations(conn) == LATEST
    assert conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == LATEST


def _insert_sub(conn, endpoint="https://fcm.googleapis.com/fcm/send/a", *, user_id=1,
                is_active=1, p256dh="p", auth="a"):
    conn.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, is_active, "
                 "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'x', 'x')",
                 (user_id, endpoint, p256dh, auth, is_active))


def test_fresh_db_push_subscriptions_constraints():
    conn = _mem()
    run_migrations(conn)
    assert {"id", "user_id", "endpoint", "p256dh", "auth", "user_agent", "is_active",
            "created_at", "updated_at", "last_used_at", "deactivated_at"} == _columns(
        conn, "push_subscriptions")
    _insert_sub(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_sub(conn)  # one row per browser push endpoint
    with pytest.raises(sqlite3.IntegrityError):
        _insert_sub(conn, "https://fcm.googleapis.com/fcm/send/b", is_active=2)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_sub(conn, "https://fcm.googleapis.com/fcm/send/c", p256dh=None)
    conn.execute("INSERT INTO notifications (recipient_email, subject, body, created_at) "
                 "VALUES ('a@b.c','s','b','x')")
    assert conn.execute("SELECT channel FROM notifications").fetchone()["channel"] == "email"


def test_ensure_current_schema_upgrades_a_v6_file(tmp_path, reset_schema_memo):
    from src.storage.migrations import ensure_current_schema

    conn = _v6_db(_file_conn(tmp_path / "v6.db"))
    assert ensure_current_schema(conn) == LATEST
    assert current_version(conn) == LATEST
    assert "push_subscriptions" in _tables(conn)
    assert "channel" in _columns(conn, "notifications")
