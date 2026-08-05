"""Tests for src/storage/db.py schema (M-ack)."""
import sqlite3

import pytest

from src.storage.db import SCHEMA, init_schema


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
    conn.execute(
        "INSERT INTO machines (machine_id, source_test, bearing, is_documented_failure) VALUES ('m1', 't', 'b', 0)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO maintenance_records (machine_id, performed_at, created_at, type)
               VALUES ('m1', '2026-01-01T00:00:00', '2026-01-01T00:00:00', 'bogus')"""
        )
