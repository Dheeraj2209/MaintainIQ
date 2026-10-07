"""Tests for GET /api/notifications (src/api/routes/notifications.py)."""


def _seed_one(conn):
    conn.execute(
        """INSERT INTO notifications
           (alert_id, recipient_email, recipient_role, subject, body, status, created_at)
           VALUES (1, 'admin@maintainiq.local', 'admin', 'MaintainIQ CRITICAL: m1', 'body', 'sent', ?)""",
        ("2026-07-26T00:00:00+00:00",),
    )
    conn.commit()


def test_notifications_forbidden_for_operator(auth_client, db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    _seed_one(conn)
    conn.close()

    resp = auth_client("operator").get("/api/notifications")
    assert resp.status_code == 403


def test_notifications_ok_for_supervisor_and_admin(auth_client, db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    _seed_one(conn)
    conn.close()

    for role in ("supervisor", "admin"):
        resp = auth_client(role).get("/api/notifications")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["recipient_email"] == "admin@maintainiq.local"


def test_notifications_requires_auth(anon_client):
    assert anon_client.get("/api/notifications").status_code == 401


def test_notifications_carry_device_incident_id(auth_client, db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    _seed_one(conn)
    conn.execute(
        """INSERT INTO device_incidents (device_id, kind, status, opened_at, created_at)
           VALUES ('dev-a', 'silent', 'open', '2026-10-06T00:00:00Z', '2026-10-06T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO notifications
           (alert_id, device_incident_id, recipient_email, recipient_role, subject, body, status,
            created_at)
           VALUES (NULL, 1, 'admin@maintainiq.local', 'admin', 'MaintainIQ DEVICE: dev-a went silent',
                   'body', 'sent', '2026-10-06T00:00:01+00:00')"""
    )
    conn.commit()
    conn.close()

    body = auth_client("admin").get("/api/notifications").json()
    assert [(n["alert_id"], n["device_incident_id"]) for n in body] == [(None, 1), (1, None)]


def test_notifications_on_pre_v3_db_without_the_column(auth_client, db_path):
    """Migrations are not run at app start, so a v2 DB has no
    notifications.device_incident_id yet; the log must still load."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """DROP TABLE notifications;
           CREATE TABLE notifications (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               alert_id INTEGER REFERENCES alerts(id),
               recipient_email TEXT NOT NULL,
               recipient_role TEXT,
               subject TEXT NOT NULL,
               body TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'sent',
               created_at TEXT NOT NULL
           );"""
    )
    _seed_one(conn)
    conn.close()

    resp = auth_client("admin").get("/api/notifications")
    assert resp.status_code == 200
    assert [n["device_incident_id"] for n in resp.json()] == [None]


# --- channel (design/2026-10-07-mobile-operator-pwa-design.md) --------------------------

def _seed_push(conn):
    conn.execute(
        """INSERT INTO notifications
           (alert_id, recipient_email, recipient_role, subject, body, status, channel, created_at)
           VALUES (1, 'operator@maintainiq.local', 'operator', 'MaintainIQ CRITICAL: m1', 'b',
                   'sent', 'push', '2026-10-07T00:00:00+00:00')"""
    )
    conn.commit()


def test_notifications_carry_and_filter_by_channel(auth_client, db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    _seed_one(conn)
    _seed_push(conn)
    conn.close()

    client = auth_client("admin")
    body = client.get("/api/notifications").json()
    assert [n["channel"] for n in body] == ["push", "email"]
    pushed = client.get("/api/notifications", params={"channel": "push"}).json()
    assert [n["recipient_role"] for n in pushed] == ["operator"]
    emailed = client.get("/api/notifications", params={"channel": "email"}).json()
    assert [n["channel"] for n in emailed] == ["email"]
    resp = client.get("/api/notifications", params={"channel": "sms"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "channel must be 'email' or 'push'"


def test_notifications_without_the_channel_column_report_email(auth_client, db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    _seed_one(conn)
    conn.execute("ALTER TABLE notifications DROP COLUMN channel")
    conn.commit()
    conn.close()

    client = auth_client("admin")
    assert [n["channel"] for n in client.get("/api/notifications").json()] == ["email"]
    assert len(client.get("/api/notifications", params={"channel": "email"}).json()) == 1
    assert client.get("/api/notifications", params={"channel": "push"}).json() == []
