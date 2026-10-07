"""Device-health routes on /api/telemetry (design/2026-10-06-device-health-design.md, API).

Kept next to test_telemetry_route.py (which covers the M6 §8 basics) and
seeded the same way: device_status / device_incidents rows written directly.
"""
from datetime import datetime, timedelta, timezone

from src.telemetry import protocol


def _seed(conn, device_id, *, seconds_ago, online=1, machine_id="sim-01", hb=10.0, reported=True):
    conn.execute(
        """INSERT INTO device_status (device_id, machine_id, online, last_seen_at,
                                      heartbeat_interval_s, payload_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (device_id, machine_id, online,
         protocol.format_utc(datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)),
         hb, "{}" if reported else None),
    )
    conn.commit()


def _incident(conn, device_id, *, status="open", opened_at, machine_id="sim-01", kind="silent",
              acknowledged_at=None, acknowledged_by=None):
    cur = conn.execute(
        """INSERT INTO device_incidents (device_id, machine_id, kind, status, opened_at,
                                         last_seen_at, resolved_at, acknowledged_at,
                                         acknowledged_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (device_id, machine_id, kind, status, opened_at, opened_at,
         opened_at if status == "resolved" else None, acknowledged_at, acknowledged_by, opened_at),
    )
    conn.commit()
    return cur.lastrowid


INCIDENT_KEYS = {
    "id", "device_id", "machine_id", "kind", "status", "opened_at", "last_seen_at",
    "resolved_at", "acknowledged_at", "acknowledged_by",
}


def test_devices_report_states_and_open_incident(client, conn):
    _seed(conn, "dev-on", seconds_ago=2)
    _seed(conn, "dev-stale", seconds_ago=20)
    _seed(conn, "dev-off", seconds_ago=120)
    _seed(conn, "dev-new", seconds_ago=1, online=0, reported=False)
    inc = _incident(conn, "dev-off", opened_at="2026-10-06T12:00:00.000Z")
    _incident(conn, "dev-off", status="resolved", opened_at="2026-10-06T11:00:00.000Z")
    devices = {d["device_id"]: d for d in client.get("/api/telemetry/devices").json()}
    assert {k: v["state"] for k, v in devices.items()} == {
        "dev-on": "online", "dev-stale": "stale", "dev-off": "offline", "dev-new": "never_reported",
    }
    for d in devices.values():
        assert d["online"] is (d["state"] in ("online", "stale"))
    assert devices["dev-new"]["online"] is False
    assert devices["dev-off"]["open_incident_id"] == inc
    assert devices["dev-on"]["open_incident_id"] is None
    assert devices["dev-off"]["silent_for_s"] >= 119


def test_devices_machine_filter(client, conn):
    _seed(conn, "dev-1", seconds_ago=2, machine_id="sim-01")
    _seed(conn, "dev-2", seconds_ago=2, machine_id="sim-02")
    _seed(conn, "dev-3", seconds_ago=2, machine_id=None)
    body = client.get("/api/telemetry/devices", params={"machine_id": "sim-02"}).json()
    assert [d["device_id"] for d in body] == ["dev-2"]
    assert client.get("/api/telemetry/devices", params={"machine_id": "nope"}).json() == []


def test_device_detail_with_recent_incidents(client, conn):
    _seed(conn, "dev-a", seconds_ago=2)
    ids = [
        _incident(conn, "dev-a", status="resolved", opened_at=f"2026-10-06T10:{m:02d}:00.000Z")
        for m in range(12)
    ]
    _incident(conn, "dev-other", opened_at="2026-10-06T11:00:00.000Z")
    resp = client.get("/api/telemetry/devices/dev-a")
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == "dev-a" and body["state"] == "online"
    assert [i["id"] for i in body["recent_incidents"]] == list(reversed(ids))[:10]
    assert set(body["recent_incidents"][0]) == INCIDENT_KEYS
    missing = client.get("/api/telemetry/devices/ghost")
    assert missing.status_code == 404 and missing.json()["detail"] == "unknown device: ghost"


def test_incidents_list_order_and_filters(client, conn):
    old_open = _incident(conn, "dev-a", opened_at="2026-10-06T09:00:00.000Z")
    new_resolved = _incident(conn, "dev-b", status="resolved", opened_at="2026-10-06T11:00:00.000Z")
    old_resolved = _incident(conn, "dev-a", status="resolved", opened_at="2026-10-06T08:00:00.000Z")
    newer_open = _incident(conn, "dev-c", opened_at="2026-10-06T10:00:00+00:00", kind="lwt",
                           machine_id=None)

    body = client.get("/api/telemetry/incidents").json()
    assert [i["id"] for i in body] == [newer_open, old_open, new_resolved, old_resolved]
    assert set(body[0]) == INCIDENT_KEYS
    assert body[0]["machine_id"] is None and body[0]["kind"] == "lwt"
    assert [i["id"] for i in client.get("/api/telemetry/incidents?status=open").json()] == [
        newer_open, old_open]
    assert [i["id"] for i in client.get("/api/telemetry/incidents?status=resolved").json()] == [
        new_resolved, old_resolved]
    assert [i["id"] for i in client.get("/api/telemetry/incidents?device_id=dev-a").json()] == [
        old_open, old_resolved]
    assert len(client.get("/api/telemetry/incidents?limit=1").json()) == 1

    bad = client.get("/api/telemetry/incidents?status=bogus")
    assert bad.status_code == 400 and bad.json()["detail"] == "invalid status: bogus"
    assert client.get("/api/telemetry/incidents?limit=0").status_code == 422
    assert client.get("/api/telemetry/incidents?limit=2001").status_code == 422


def test_acknowledge_incident_any_role(auth_client, conn):
    users = {r["role"]: r["id"] for r in conn.execute("SELECT id, role FROM users")}
    for role in ("operator", "supervisor", "admin"):
        inc = _incident(conn, f"dev-{role}", opened_at="2026-10-06T09:00:00.000Z")
        c = auth_client(role)
        resp = c.post(f"/api/telemetry/incidents/{inc}/acknowledge")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == inc and body["acknowledged_by"] == users[role]
        assert body["acknowledged_at"] is not None and body["status"] == "open"
        # Idempotent: a second call (even by someone else) changes nothing.
        again = auth_client("admin").post(f"/api/telemetry/incidents/{inc}/acknowledge").json()
        assert again == body


def test_acknowledge_resolved_and_unknown(client, conn):
    inc = _incident(conn, "dev-a", status="resolved", opened_at="2026-10-06T09:00:00.000Z")
    resp = client.post(f"/api/telemetry/incidents/{inc}/acknowledge")
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved" and resp.json()["acknowledged_at"]
    row = conn.execute("SELECT acknowledged_by FROM device_incidents WHERE id = ?", (inc,)).fetchone()
    assert row["acknowledged_by"] is not None
    missing = client.post("/api/telemetry/incidents/999/acknowledge")
    assert missing.status_code == 404 and missing.json()["detail"] == "unknown incident: 999"


def test_device_health_routes_require_auth(anon_client):
    assert anon_client.get("/api/telemetry/incidents").status_code == 401
    assert anon_client.get("/api/telemetry/devices/x").status_code == 401
    assert anon_client.post("/api/telemetry/incidents/1/acknowledge").status_code == 401


def test_pre_v3_db_without_incident_table(client, conn):
    _seed(conn, "dev-off", seconds_ago=120)
    conn.execute("DROP TABLE device_incidents")
    conn.commit()
    devices = client.get("/api/telemetry/devices").json()
    assert devices[0]["state"] == "offline" and devices[0]["open_incident_id"] is None
    assert client.get("/api/telemetry/devices/dev-off").json()["recent_incidents"] == []
    assert client.get("/api/telemetry/incidents").json() == []
    assert client.post("/api/telemetry/incidents/1/acknowledge").status_code == 404


def test_device_detail_on_pre_m6_db_is_404(client, conn):
    conn.execute("DROP TABLE device_status")
    conn.commit()
    assert client.get("/api/telemetry/devices/dev-a").status_code == 404


def test_status_has_device_watchdog_block_when_disabled(client, monkeypatch):
    monkeypatch.setenv("DEVICE_SILENT_GRACE_S", "45")
    block = client.get("/api/telemetry/status").json()["device_watchdog"]
    assert block == {"enabled": False, "interval_s": None, "grace_s": 45.0, "armed": False,
                     "last_tick_at": None}
    # A malformed value must not break a read-only status call.
    monkeypatch.setenv("DEVICE_SILENT_GRACE_S", "later")
    assert client.get("/api/telemetry/status").json()["device_watchdog"]["grace_s"] == 30.0


def test_status_has_device_watchdog_block_when_enabled(client):
    from src.api.app import app
    from src.api.routes.telemetry import get_mqtt_service
    from src.background import scheduler as sched
    from src.telemetry import watchdog

    class FakeService:
        def status(self):
            return {"enabled": True, "connected": True, "broker": "mosquitto:1883",
                    "topic_prefix": "maintainiq/v1", "started_at": None, "last_message_at": None,
                    "stats": {"received": 3}}

    app.dependency_overrides[get_mqtt_service] = lambda: FakeService()
    watcher = watchdog.DeviceSilenceWatcher(grace_s=30.0, ingest_connected_for=lambda: 12.0)
    sched.set_scheduler(sched.BackgroundScheduler(interval_s=5.0))
    watchdog.set_watcher(watcher)
    try:
        body = client.get("/api/telemetry/status").json()
    finally:
        sched.set_scheduler(None)
        watchdog.set_watcher(None)
    assert body["connected"] is True
    assert body["device_watchdog"] == {"enabled": True, "interval_s": 5.0, "grace_s": 30.0,
                                       "armed": False, "last_tick_at": None}
