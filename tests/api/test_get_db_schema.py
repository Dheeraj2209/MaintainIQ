"""get_db brings a stale (v1+) file to the current schema before yielding it
(work-orders design, decision 11): every alert SELECT reads the migration-4
columns, and migrations do not run at app start."""
import sqlite3

import pytest

from src.api import deps
from src.storage import migrations
from src.storage.migrations import MIGRATIONS, current_version

LATEST = MIGRATIONS[-1][0]


@pytest.fixture(autouse=True)
def _reset_memo():
    migrations._reset_current_schema_memo()
    yield
    migrations._reset_current_schema_memo()


def _v3_file(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    for version, step in MIGRATIONS[:3]:
        step(conn)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.execute("DROP TABLE IF EXISTS work_order_events")
    conn.execute("DROP TABLE IF EXISTS work_orders")
    conn.commit()
    conn.close()


def _connect(path):
    def factory():
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    return factory


def test_get_db_migrates_a_v3_file_before_yielding(tmp_path, monkeypatch):
    path = tmp_path / "v3.db"
    _v3_file(path)
    monkeypatch.setattr(deps, "get_connection", _connect(path))

    gen = deps.get_db()
    conn = next(gen)
    try:
        assert current_version(conn) == LATEST
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "work_orders" in names
    finally:
        gen.close()


def test_get_db_leaves_an_unversioned_file_alone(tmp_path, monkeypatch):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr(deps, "get_connection", _connect(path))

    gen = deps.get_db()
    conn = next(gen)
    try:
        assert current_version(conn) == 0
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []
    finally:
        gen.close()


def test_get_db_still_yields_when_the_upgrade_fails(tmp_path, monkeypatch):
    path = tmp_path / "v3.db"
    _v3_file(path)
    monkeypatch.setattr(deps, "get_connection", _connect(path))

    def boom(conn):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(migrations, "run_migrations", boom)
    gen = deps.get_db()
    conn = next(gen)
    try:
        assert current_version(conn) == 3
    finally:
        gen.close()
