"""Tests for src/notifications/dispatch.py. send_email is monkeypatched so
these tests don't need a real SMTP server."""
import pytest

from src.notifications import dispatch

ALERT = {
    "id": 1,
    "machine_id": "m1",
    "severity": "high",
    "health_state": "critical",
    "probable_cause": "bearing_wear",
    "opened_at": "2026-07-26T00:00:00+00:00",
    "message": "Machine m1 is critical (probable cause: bearing_wear)",
}


@pytest.fixture
def sent_emails(monkeypatch):
    sent = []

    def _fake_send(to, subject, body_text):
        sent.append({"to": to, "subject": subject, "body": body_text})

    monkeypatch.setattr(dispatch, "send_email", _fake_send)
    return sent


def test_notify_alert_emails_admin_and_supervisor_only(conn, sent_emails):
    count = dispatch.notify_alert(conn, ALERT)
    assert count == 2
    recipients = {e["to"] for e in sent_emails}
    assert recipients == {"admin@maintainiq.local", "supervisor@maintainiq.local"}


def test_notify_alert_logs_one_row_per_recipient(conn, sent_emails):
    dispatch.notify_alert(conn, ALERT)
    rows = conn.execute("SELECT * FROM notifications ORDER BY recipient_email").fetchall()
    assert len(rows) == 2
    assert all(row["status"] == "sent" for row in rows)
    assert all(row["alert_id"] == 1 for row in rows)


def test_notify_alert_subject_reflects_severity(conn, sent_emails):
    dispatch.notify_alert(conn, ALERT)
    assert "CRITICAL" in sent_emails[0]["subject"]
    assert "m1" in sent_emails[0]["subject"]


def test_notify_alert_handles_send_failure(conn, monkeypatch):
    def _boom(to, subject, body_text):
        raise ConnectionRefusedError("no mailpit")

    monkeypatch.setattr(dispatch, "send_email", _boom)

    count = dispatch.notify_alert(conn, ALERT)
    assert count == 0
    rows = conn.execute("SELECT status FROM notifications").fetchall()
    assert len(rows) == 2
    assert all(row["status"] == "failed" for row in rows)
