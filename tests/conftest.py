"""Shared pytest fixtures.

Builds a small, fully-controlled temp SQLite DB using the real schema from
src/storage/db.py, so tests never touch the developer's maintainiq.db and the
expected KPI/maintenance numbers are deterministic.
"""
import sqlite3
from datetime import datetime, timezone

import pytest

from src.storage.db import SCHEMA


def _iso(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).isoformat()


NOW = datetime.now(timezone.utc).isoformat()


def _populate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

    conn.executemany(
        "INSERT INTO machines (machine_id, source_test, bearing, is_documented_failure) VALUES (?,?,?,?)",
        [
            ("m1", "test1", "B1", 1),  # documented failure, ends critical
            ("m2", "test1", "B2", 0),  # stays healthy
        ],
    )

    # readings: two per machine, latest last. m1 latest is high-vibration/high-temp.
    readings = [
        # machine, ts, sensor, rms, kurt, band, temp, synth, rul
        ("m1", _iso(2003, 10, 22, 12, 0), "s1", 0.1, 3.0, 0.1, 40.0, 0, 100.0),
        ("m1", _iso(2003, 10, 22, 13, 0), "s1", 0.9, 6.0, 0.5, 70.0, 0, 1.0),
        ("m2", _iso(2003, 10, 22, 12, 0), "s2", 0.1, 2.5, 0.05, 35.0, 0, 200.0),
        ("m2", _iso(2003, 10, 22, 13, 0), "s2", 0.12, 2.6, 0.06, 36.0, 0, 199.0),
    ]
    conn.executemany(
        """INSERT INTO readings
           (machine_id, timestamp, sensor_id, vibration_h_rms, vibration_h_kurtosis,
            vibration_h_high_band_energy_ratio, temperature_c, temperature_is_synthetic,
            rul_hours, features_json)
           VALUES (?,?,?,?,?,?,?,?,?, '{}')""",
        readings,
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
    conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _populate(conn)
    conn.close()
    return path


@pytest.fixture
def conn(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture
def client(db_path):
    """TestClient with get_db overridden to use the temp DB."""
    from fastapi.testclient import TestClient

    from src.api.app import app
    from src.api.deps import get_db

    def _override():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
