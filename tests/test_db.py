"""Tests for src/storage/db.py schema (M-ack)."""
import sqlite3

import pytest

from src.storage.db import (
    SCHEMA,
    init_schema,
    insert_machines,
    insert_readings,
    insert_single_reading,
)


def test_alerts_table_has_acknowledge_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
    assert "acknowledged_at" in cols
    assert "acknowledged_by" in cols


def test_init_schema_is_idempotent_on_existing_db():
    """A DB created before this change (no ack columns) must still work after
    init_schema runs again — the guarded ALTER TABLE must not raise."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Simulate a pre-existing DB: create the old alerts table shape only.
    conn.execute(
        """CREATE TABLE alerts (
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
        )"""
    )
    conn.commit()

    init_schema(conn)  # must not raise, and must add the new columns

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
    assert "acknowledged_at" in cols
    assert "acknowledged_by" in cols


def test_maintenance_records_table_has_alert_id_and_type_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(maintenance_records)")}
    assert "alert_id" in cols
    assert "type" in cols


def test_init_schema_is_idempotent_for_maintenance_records_on_existing_db():
    """A DB created before this change (no alert_id/type columns) must still
    work after init_schema runs again — the guarded ALTER TABLE must not raise."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE maintenance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            performed_at TEXT NOT NULL,
            description TEXT,
            technician TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()

    init_schema(conn)  # must not raise, and must add the new columns

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(maintenance_records)")}
    assert "alert_id" in cols
    assert "type" in cols


def test_maintenance_records_type_check_constraint():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO maintenance_records (machine_id, performed_at, created_at, type)
               VALUES ('m1', '2026-01-01T00:00:00', '2026-01-01T00:00:00', 'bogus')"""
        )


def test_maintenance_records_type_check_constraint_via_alter_upgrade_path():
    """The `type` column is added via ALTER TABLE for pre-existing DBs (the
    branch at src/storage/db.py's init_schema that only fires when the column
    is missing). Simulate that upgrade path — create the table in its
    old shape (no alert_id/type), run init_schema to add the columns via
    ALTER, then confirm the CHECK constraint added by that ALTER is enforced
    exactly like the fresh-SCHEMA path: valid types insert, an invalid type
    raises IntegrityError."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE maintenance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            performed_at TEXT NOT NULL,
            description TEXT,
            technician TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE machines (
            machine_id TEXT PRIMARY KEY,
            source_test TEXT,
            bearing TEXT,
            is_documented_failure INTEGER
        )"""
    )
    conn.commit()

    init_schema(conn)  # must not raise, and must add alert_id/type via ALTER

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(maintenance_records)")}
    assert "type" in cols

    conn.execute("INSERT INTO machines (machine_id) VALUES ('m1')")
    conn.commit()

    for valid_type in ("preventive", "corrective"):
        conn.execute(
            """INSERT INTO maintenance_records (machine_id, performed_at, created_at, type)
               VALUES ('m1', '2026-01-01T00:00:00', '2026-01-01T00:00:00', ?)""",
            (valid_type,),
        )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO maintenance_records (machine_id, performed_at, created_at, type)
               VALUES ('m1', '2026-01-01T00:00:00', '2026-01-01T00:00:00', 'bogus')"""
        )


def test_insert_machines_round_trip_with_defaults():
    """Insert a machine dict with a mix of required/optional keys and read it
    back, asserting the dataset/is_documented_failure defaults kick in when
    those keys are omitted from the input dict."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    insert_machines(
        conn,
        [
            {
                "machine_id": "m1",
                "bearing_id": "b1",
                "operating_condition": 2,
                "speed_rpm": 2100.0,
                "load_kn": 12.5,
            }
        ],
    )

    row = conn.execute("SELECT * FROM machines WHERE machine_id = 'm1'").fetchone()
    assert row is not None
    assert row["bearing_id"] == "b1"
    assert row["operating_condition"] == 2
    assert row["speed_rpm"] == 2100.0
    assert row["load_kn"] == 12.5
    assert row["dataset"] == "xjtu_sy"
    assert row["is_documented_failure"] == 0


def _canonical_reading(machine_id="m1", cycle=1, **overrides):
    """Build a canonical XJTU-SY reading dict (see db._READING_COLUMNS) with
    sane defaults, letting individual tests override fields as needed."""
    base = {
        "machine_id": machine_id,
        "timestamp": "2026-01-01T00:00:00",
        "cycle": cycle,
        "elapsed_minutes": 1.0,
        "speed_rpm": 2100.0,
        "load_kn": 12.5,
        "sample_rate_hz": 25600.0,
        "vibration_h_rms": 0.1,
        "vibration_h_kurtosis": 3.0,
        "vibration_v_rms": 0.2,
        "vibration_v_kurtosis": 3.1,
        "cross_axis_rms_ratio": 0.5,
        "cross_axis_correlation": 0.9,
        "rul_minutes": 100.0,
    }
    base.update(overrides)
    return base


def test_insert_readings_idempotent_on_machine_id_and_cycle():
    """insert_readings uses INSERT OR IGNORE against UNIQUE(machine_id, cycle);
    inserting two reading dicts with the same key must persist exactly one row,
    keeping the first insert's values."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    insert_machines(conn, [{"machine_id": "m1"}])

    insert_readings(conn, [_canonical_reading(cycle=1)])
    insert_readings(conn, [_canonical_reading(cycle=1, vibration_h_rms=999.0)])

    rows = conn.execute(
        "SELECT * FROM readings WHERE machine_id = 'm1' AND cycle = 1"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["vibration_h_rms"] == 0.1


def test_insert_single_reading_returns_id_and_defaults_features_json():
    """insert_single_reading returns the new row's id, and defaults
    features_json to '{}' when the caller omits it (the live/demo path)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    insert_machines(conn, [{"machine_id": "m1"}])

    reading_id = insert_single_reading(conn, _canonical_reading(cycle=1))

    row = conn.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()
    assert row is not None
    assert row["machine_id"] == "m1"
    assert row["cycle"] == 1
    assert row["features_json"] == "{}"
