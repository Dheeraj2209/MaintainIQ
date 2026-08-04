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
