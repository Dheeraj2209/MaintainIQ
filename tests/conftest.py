"""Shared pytest fixtures.

Builds a small, fully-controlled temp SQLite DB using the real schema from
src/storage/db.py, so tests never touch the developer's maintainiq.db and the
expected KPI/maintenance numbers are deterministic.
"""
import sqlite3
from datetime import datetime, timezone

import pytest

from src.auth.security import hash_password
from src.auth.seed import DEMO_USERS
from src.storage.db import init_schema


@pytest.fixture(autouse=True)
def _no_live_mqtt_from_shell(monkeypatch):
    """Keep the developer's shell out of the suite's MQTT config.

    Every TestClient(app) runs the lifespan, which starts live MQTT ingest
    whenever MQTT_BROKER_HOST is set — wired to the REAL maintainiq.db (the
    ingestor's connection factory, not the overridable get_db) and to the
    fixed client id `maintainiq-ingest` with a persistent session. A
    developer who exported MQTT_BROKER_HOST for the live quick start would
    otherwise have `just test` take over the running app's broker session,
    drain its queued telemetry, and write readings and alerts into their
    database. Tests that exercise ingest set the variables they need.
    """
    import os

    # Same for background jobs: MAINTAINIQ_SWEEP_INTERVAL_S would start the
    # scheduler, whose connection factory is the real maintainiq.db too.
    # And push: VAPID_* keys would turn Web Push on for every paging test
    # (design/2026-10-07-mobile-operator-pwa-design.md); tests that exercise
    # it set keys through the push_enabled fixture.
    prefixes = ("MQTT_", "MAINTAINIQ_SWEEP_", "DEVICE_SILENT_", "ESCALATION_", "VAPID_", "PUSH_")
    for name in [key for key in os.environ if key.startswith(prefixes)]:
        monkeypatch.delenv(name, raising=False)


def _iso(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).isoformat()


NOW = datetime.now(timezone.utc).isoformat()

# Hashed once at import time — bcrypt is deliberately slow, and re-hashing
# for every test's fresh DB would noticeably slow the suite down.
_DEMO_USER_ROWS = [(email, name, hash_password(pw), role) for email, name, pw, role in DEMO_USERS]


def _populate(conn: sqlite3.Connection) -> None:
    init_schema(conn)

    conn.executemany(
        """INSERT INTO machines
           (machine_id, bearing_id, operating_condition, speed_rpm, load_kn, dataset,
            is_documented_failure)
           VALUES (?,?,?,?,?,?,?)""",
        [
            ("m1", "Bearing1_1", 1, 2100.0, 12.0, "xjtu_sy", 1),  # documented failure, ends critical
            ("m2", "Bearing1_2", 1, 2100.0, 12.0, "xjtu_sy", 0),  # stays healthy
        ],
    )

    # readings: two per machine, latest last. m1's latest is high-vibration; the
    # high-band-energy ratio lives in features_json (canonical schema drops it as
    # a promoted column) so _vibration_severity can still read it.
    readings = [
        # machine, ts, cycle, elapsed, rms, kurt, v_rms, v_kurt, cross_ratio, cross_corr, rul_min, band
        ("m1", _iso(2003, 10, 22, 12, 0), 0, 0.0, 0.1, 3.0, 0.1, 3.0, 0.9, 0.4, 100.0, 0.1),
        ("m1", _iso(2003, 10, 22, 13, 0), 1, 60.0, 0.9, 6.0, 0.8, 5.5, 1.1, 0.8, 1.0, 0.5),
        ("m2", _iso(2003, 10, 22, 12, 0), 0, 0.0, 0.1, 2.5, 0.1, 2.4, 0.95, 0.1, 200.0, 0.05),
        ("m2", _iso(2003, 10, 22, 13, 0), 1, 60.0, 0.12, 2.6, 0.11, 2.5, 0.96, 0.12, 199.0, 0.06),
    ]
    conn.executemany(
        """INSERT INTO readings
           (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm, load_kn,
            sample_rate_hz, vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
            vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation,
            rul_minutes, features_json, dataset)
           VALUES (?,?,?,?, 2100.0, 12.0, 25600.0, ?,?,?,?,?,?,?,
                   json_object('vibration_h_high_band_energy_ratio', ?), 'xjtu_sy')""",
        [
            (m, ts, cyc, elapsed, rms, kurt, v_rms, v_kurt, cr_ratio, cr_corr, rul, band)
            for (m, ts, cyc, elapsed, rms, kurt, v_rms, v_kurt, cr_ratio, cr_corr, rul, band) in readings
        ],
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

    conn.executemany(
        """INSERT INTO users (email, name, hashed_password, role, is_active, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        [(email, name, hashed, role, NOW) for email, name, hashed, role in _DEMO_USER_ROWS],
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
def _db_override(db_path):
    """Registers get_db override onto the temp DB; cleans up after the test."""
    from src.api.app import app
    from src.api.deps import get_db

    def _override():
        # check_same_thread=False: matches src.storage.db.get_connection — the
        # websocket route resolves this sync dependency via AnyIO's threadpool
        # but uses the connection from its own async endpoint body, so creation
        # and use can land on different threads.
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(_db_override):
    """TestClient pre-authenticated as admin. Most existing tests exercise
    business logic (machines/alerts/kpis), not auth itself, so they should not
    need to care that routes are now behind a login — RBAC-specific tests use
    `auth_client`/`anon_client` instead."""
    from fastapi.testclient import TestClient

    from src.api.app import app

    with TestClient(app) as test_client:
        email, _name, password, _role = DEMO_USERS[0]  # admin
        resp = test_client.post("/api/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        yield test_client


@pytest.fixture
def auth_client(_db_override):
    """Factory fixture: auth_client("supervisor") -> TestClient logged in as
    the demo user for that role."""
    from fastapi.testclient import TestClient

    from src.api.app import app

    created = []

    def _make(role: str):
        email, _name, password, _role = next(u for u in DEMO_USERS if u[3] == role)
        test_client = TestClient(app)
        resp = test_client.post("/api/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        created.append(test_client)
        return test_client

    yield _make

    for test_client in created:
        test_client.close()


@pytest.fixture
def anon_client(_db_override):
    """TestClient with no session cookie, for testing the unauthenticated path."""
    from fastapi.testclient import TestClient

    from src.api.app import app

    with TestClient(app) as test_client:
        yield test_client


# --- Web Push (design/2026-10-07-mobile-operator-pwa-design.md) ---------------------

class FakeWebPush:
    """Stands in for pywebpush.webpush behind push._webpush: records every call
    and fails endpoints listed in `fail` with the given exception (or an HTTP
    status, raised as a WebPushException carrying a fake response)."""

    def __init__(self):
        self.calls = []
        self.fail = {}

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.fail.get(kwargs["subscription_info"]["endpoint"])
        if outcome is None:
            return None
        if isinstance(outcome, int):
            from types import SimpleNamespace

            from pywebpush import WebPushException

            raise WebPushException("push failed", response=SimpleNamespace(
                status_code=outcome, text="", headers={}))
        raise outcome

    @property
    def endpoints(self):
        return [c["subscription_info"]["endpoint"] for c in self.calls]


_VAPID_KEYS = None


def vapid_keys():
    """One generated VAPID key pair per test session (generation is cheap,
    but stable keys make failures easier to read)."""
    global _VAPID_KEYS
    if _VAPID_KEYS is None:
        from src.notifications.push import generate_vapid_keys

        _VAPID_KEYS = generate_vapid_keys()
    return _VAPID_KEYS


# A valid browser key pair shape: p256dh is an uncompressed P-256 point (65
# bytes, leading 0x04), auth is 16 random bytes; both base64url, unpadded.
def b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


SUB_P256DH = b64url(b"" + bytes(range(64)))
SUB_AUTH = b64url(bytes(range(16)))


def add_push_subscription(conn, user_id, endpoint, *, is_active=1):
    conn.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, is_active,
                                           created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, '2026-10-07T00:00:00+00:00', '2026-10-07T00:00:00+00:00')""",
        (user_id, endpoint, SUB_P256DH, SUB_AUTH, is_active),
    )
    conn.commit()


@pytest.fixture
def push_enabled(monkeypatch):
    """Configure VAPID keys from env and swap the push sender for a recorder."""
    from src.notifications import push

    public, private = vapid_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:ops@example.com")
    fake = FakeWebPush()
    monkeypatch.setattr(push, "_webpush", fake)
    return fake


@pytest.fixture
def add_sub():
    """add_sub(conn, user_id, endpoint, is_active=1) inserts a push subscription
    with valid-shaped browser keys."""
    return add_push_subscription
