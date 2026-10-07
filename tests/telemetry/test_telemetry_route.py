"""GET /api/telemetry/status and /api/telemetry/devices (contract §8)."""
from datetime import datetime, timedelta, timezone

from src.telemetry import protocol


def test_status_when_disabled(client):
    resp = client.get("/api/telemetry/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False and body["connected"] is False and body["broker"] is None
    assert body["stats"]["received"] == 0


def test_disabled_status_reports_configured_prefix(client, monkeypatch):
    monkeypatch.setenv("MQTT_TOPIC_PREFIX", "plant7/v1")
    assert client.get("/api/telemetry/status").json()["topic_prefix"] == "plant7/v1"
    # A malformed MQTT_* value must not break a read-only status call.
    monkeypatch.setenv("MQTT_BROKER_PORT", "not-a-port")
    assert client.get("/api/telemetry/status").json()["topic_prefix"] == protocol.DEFAULT_TOPIC_PREFIX


def test_status_uses_the_service(client):
    from src.api.app import app
    from src.api.routes.telemetry import get_mqtt_service

    class FakeService:
        def status(self):
            return {"enabled": True, "connected": True, "broker": "mosquitto:1883",
                    "topic_prefix": "maintainiq/v1", "started_at": None, "last_message_at": None,
                    "stats": {"received": 3}}

    app.dependency_overrides[get_mqtt_service] = lambda: FakeService()
    body = client.get("/api/telemetry/status").json()
    assert body["connected"] is True and body["broker"] == "mosquitto:1883"


def test_requires_authentication(anon_client):
    assert anon_client.get("/api/telemetry/status").status_code == 401
    assert anon_client.get("/api/telemetry/devices").status_code == 401


def test_devices_apply_online_rule(client, conn):
    now = datetime.now(timezone.utc)
    rows = [
        # fresh heartbeat, online
        ("dev-a", "sim-01", 1, protocol.format_utc(now - timedelta(seconds=5)), 10.0),
        # said online but silent for > 3 heartbeats
        ("dev-b", "sim-02", 1, protocol.format_utc(now - timedelta(seconds=40)), 10.0),
        # LWT received
        ("dev-c", "sim-03", 0, protocol.format_utc(now), None),
    ]
    # payload_json set: every node has reported a status, so the LWT row is
    # `offline`, not `never_reported` (device-health design, decision 3).
    conn.executemany(
        """INSERT INTO device_status (device_id, machine_id, online, last_seen_at, heartbeat_interval_s,
                                      buffer_capacity, firmware, payload_json)
           VALUES (?, ?, ?, ?, ?, 50, 'sim/0.1', '{}')""",
        rows,
    )
    conn.commit()
    devices = client.get("/api/telemetry/devices").json()
    assert [d["device_id"] for d in devices] == ["dev-a", "dev-b", "dev-c"]
    assert [d["online"] for d in devices] == [True, False, False]
    assert set(devices[0]) == {
        "device_id", "machine_id", "online", "last_seen_at", "firmware", "uptime_s",
        "buffer_depth", "buffer_capacity", "buffer_dropped_total", "publish_attempts_total",
        "publish_failures_total", "wifi_rssi_dbm", "snapshot_interval_s", "heartbeat_interval_s",
        "state", "silent_for_s", "expected_heartbeat_s", "open_incident_id",
    }
    assert devices[0]["buffer_capacity"] == 50
    assert [d["state"] for d in devices] == ["online", "offline", "offline"]
    assert [d["expected_heartbeat_s"] for d in devices] == [10.0, 10.0, 10.0]
    assert all(d["open_incident_id"] is None for d in devices)


def test_devices_empty(client):
    assert client.get("/api/telemetry/devices").json() == []


def test_lifespan_starts_and_stops_service_with_unreachable_broker(_db_override, monkeypatch):
    """MQTT_BROKER_HOST set but nothing listening: the app must still start
    promptly (paho retries in the background) and shut the service down."""
    import time

    from fastapi.testclient import TestClient

    from src.api.app import app
    from src.api.routes import telemetry as telemetry_route

    monkeypatch.setenv("MQTT_BROKER_HOST", "127.0.0.1")
    monkeypatch.setenv("MQTT_BROKER_PORT", "1")  # nothing listens here
    started = time.monotonic()
    with TestClient(app) as test_client:
        assert time.monotonic() - started < 5
        svc = telemetry_route.get_mqtt_service()
        assert svc is not None and svc.running
        from src.auth.seed import DEMO_USERS

        email, _n, password, _r = DEMO_USERS[0]
        test_client.post("/api/auth/login", json={"email": email, "password": password})
        body = test_client.get("/api/telemetry/status").json()
        assert body["enabled"] is True and body["connected"] is False
        assert body["broker"] == "127.0.0.1:1"
    assert telemetry_route.get_mqtt_service() is None and not svc.running


def test_lifespan_with_invalid_mqtt_config_still_starts(_db_override, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.app import app
    from src.api.routes import telemetry as telemetry_route

    monkeypatch.setenv("MQTT_BROKER_HOST", "127.0.0.1")
    monkeypatch.setenv("MQTT_BROKER_PORT", "not-a-port")
    with TestClient(app) as test_client:
        assert test_client.get("/api/health").status_code == 200
        assert telemetry_route.get_mqtt_service() is None


def test_devices_on_pre_m6_db_is_empty(client, conn):
    conn.execute("DROP TABLE device_status")
    conn.commit()
    resp = client.get("/api/telemetry/devices")
    assert resp.status_code == 200 and resp.json() == []
