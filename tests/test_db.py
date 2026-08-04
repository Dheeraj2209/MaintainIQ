"""Tests for src/storage/db.py schema (M-ack)."""
import sqlite3

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
