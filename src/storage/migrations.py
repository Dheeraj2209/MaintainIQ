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
