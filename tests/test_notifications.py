"""Tests for src/notifications/dispatch.py. send_email is monkeypatched so
these tests don't need a real SMTP server."""
import json

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


# --- Device-health paging (design/2026-10-06-device-health-design.md) -------------

INCIDENT = {
    "id": None,  # filled in from a real row so the FK-shaped column is honest
    "device_id": "esp32-a1b2c3",
    "machine_id": "sim-01",
    "kind": "silent",
    "status": "open",
    "opened_at": "2026-10-06T12:00:35.000Z",
    "last_seen_at": "2026-10-06T12:00:00.000Z",
}


def _incident(conn, **overrides):
    incident = {**INCIDENT, **overrides}
    cur = conn.execute(
        """INSERT INTO device_incidents (device_id, machine_id, kind, status, opened_at,
                                         last_seen_at, created_at)
           VALUES (?, ?, ?, 'open', ?, ?, ?)""",
        (incident["device_id"], incident["machine_id"], incident["kind"],
         incident["opened_at"], incident["last_seen_at"], incident["opened_at"]),
    )
    conn.commit()
    incident["id"] = cur.lastrowid
    return incident


def test_recipients_default_and_roles(conn):
    default = {r["email"] for r in dispatch._recipients(conn)}
    assert default == {"admin@maintainiq.local", "supervisor@maintainiq.local"}
    admins = dispatch._recipients(conn, roles=("admin",))
    assert [r["role"] for r in admins] == ["admin"]
    assert dispatch._recipients(conn, roles=()) == []


def test_notify_device_incident_silent(conn, sent_emails):
    incident = _incident(conn)
    assert dispatch.notify_device_incident(conn, incident) == 2
    assert {e["to"] for e in sent_emails} == {"admin@maintainiq.local", "supervisor@maintainiq.local"}
    subject = sent_emails[0]["subject"]
    assert subject.startswith("MaintainIQ DEVICE: esp32-a1b2c3 went silent")
    assert "sim-01" in subject
    body = sent_emails[0]["body"]
    assert "esp32-a1b2c3" in body and "sim-01" in body
    assert "Devices page" in body and incident["last_seen_at"] in body
    rows = conn.execute("SELECT * FROM notifications ORDER BY id").fetchall()
    assert len(rows) == 2
    assert all(r["alert_id"] is None and r["device_incident_id"] == incident["id"] for r in rows)
    assert all(r["status"] == "sent" for r in rows)


def test_notify_device_incident_lwt_unassigned(conn, sent_emails):
    incident = _incident(conn, kind="lwt", machine_id=None)
    dispatch.notify_device_incident(conn, incident)
    assert "esp32-a1b2c3 disconnected" in sent_emails[0]["subject"]
    assert "unassigned" in sent_emails[0]["body"]


def test_notify_device_incident_counts_only_sent(conn, monkeypatch):
    incident = _incident(conn)
    calls = []

    def _flaky(to, subject, body_text):
        calls.append(to)
        if to.startswith("admin"):
            raise ConnectionRefusedError("no mailpit")

    monkeypatch.setattr(dispatch, "send_email", _flaky)
    assert dispatch.notify_device_incident(conn, incident) == 1
    statuses = sorted(r["status"] for r in conn.execute("SELECT status FROM notifications"))
    assert statuses == ["failed", "sent"]


def test_notify_device_incident_respects_roles(conn, sent_emails):
    incident = _incident(conn)
    assert dispatch.notify_device_incident(conn, incident, roles=("supervisor",)) == 1
    assert [e["to"] for e in sent_emails] == ["supervisor@maintainiq.local"]


# --- Paging ladder (design/2026-10-07-work-orders-escalation-design.md, decision 7)

def test_notify_alert_pages_only_the_given_roles_with_level_subject(conn, sent_emails):
    count = dispatch.notify_alert(conn, ALERT, roles=("supervisor",), page_level=1)
    assert count == 1
    assert [e["to"] for e in sent_emails] == ["supervisor@maintainiq.local"]
    assert sent_emails[0]["subject"].startswith("[Unacknowledged — page 1] ")
    assert sent_emails[0]["subject"].endswith("MaintainIQ CRITICAL: m1 needs attention")
    row = conn.execute("SELECT * FROM notifications").fetchone()
    assert row["alert_id"] == 1 and row["recipient_role"] == "supervisor"


def test_paged_body_mentions_the_level(conn, sent_emails):
    dispatch.notify_alert(conn, ALERT, roles=("supervisor", "admin"), page_level=2)
    body = sent_emails[0]["body"]
    assert "Paging level 2 of 2" in body
    assert "Unacknowledged since 2026-07-26T00:00:00+00:00" in body
    assert len(sent_emails) == 2


def test_level_zero_compose_is_unchanged(conn, sent_emails):
    dispatch.notify_alert(conn, ALERT)
    assert sent_emails[0]["subject"] == "MaintainIQ CRITICAL: m1 needs attention"
    assert "Paging level" not in sent_emails[0]["body"]


# --- Push channel (design/2026-10-07-mobile-operator-pwa-design.md, decision 2) ------

def _fcm(name):
    return f"https://fcm.googleapis.com/fcm/send/{name}"


@pytest.fixture
def role_subs(conn, add_sub):
    """One push subscription per demo role (ids 1-3: admin, supervisor, operator)."""
    for user_id, role in ((1, "admin"), (2, "supervisor"), (3, "operator")):
        add_sub(conn, user_id, _fcm(role))
    return conn


def test_level_zero_alert_pushes_operators_too_but_counts_emails(role_subs, sent_emails,
                                                                 push_enabled):
    assert dispatch.notify_alert(role_subs, ALERT) == 2
    assert {e["to"] for e in sent_emails} == {"admin@maintainiq.local",
                                             "supervisor@maintainiq.local"}
    assert push_enabled.endpoints == [_fcm("admin"), _fcm("supervisor"), _fcm("operator")]
    channels = [r["channel"] for r in role_subs.execute(
        "SELECT channel FROM notifications ORDER BY id")]
    assert channels == ["email", "email", "push", "push", "push"]


def test_ladder_levels_push_only_the_ladder_roles(role_subs, sent_emails, push_enabled):
    dispatch.notify_alert(role_subs, ALERT, roles=("supervisor",), page_level=1)
    assert push_enabled.endpoints == [_fcm("supervisor")]
    title = json.loads(push_enabled.calls[0]["data"])["title"]
    assert title.startswith("[Unacknowledged — page 1]")

    push_enabled.calls.clear()
    dispatch.notify_alert(role_subs, ALERT, roles=("admin",), page_level=2)
    assert push_enabled.endpoints == [_fcm("admin")]


def test_explicit_push_roles_override(role_subs, sent_emails, push_enabled):
    dispatch.notify_alert(role_subs, ALERT, push_roles=("operator",))
    assert push_enabled.endpoints == [_fcm("operator")]


def test_device_incident_pushes_all_three_roles(role_subs, sent_emails, push_enabled):
    incident = _incident(role_subs)
    assert dispatch.notify_device_incident(role_subs, incident) == 2
    assert push_enabled.endpoints == [_fcm("admin"), _fcm("supervisor"), _fcm("operator")]
    payload = json.loads(push_enabled.calls[0]["data"])
    assert payload["url"] == "/m/machines/sim-01"
    rows = role_subs.execute(
        "SELECT * FROM notifications WHERE channel = 'push'").fetchall()
    assert len(rows) == 3
    assert all(r["device_incident_id"] == incident["id"] and r["alert_id"] is None for r in rows)


def test_push_bug_never_undoes_the_emails(role_subs, sent_emails, push_enabled, monkeypatch,
                                          caplog):
    from src.notifications import push

    def _bug(*args, **kwargs):
        raise RuntimeError("bug in push")

    monkeypatch.setattr(push, "push_to_roles", _bug)
    assert dispatch.notify_alert(role_subs, ALERT) == 2
    assert dispatch.notify_device_incident(role_subs, _incident(role_subs)) == 2
    rows = role_subs.execute("SELECT channel FROM notifications").fetchall()
    assert [r["channel"] for r in rows] == ["email"] * 4
    assert "bug in push" in caplog.text


def test_email_rows_are_labelled_email(conn, sent_emails):
    dispatch.notify_alert(conn, ALERT)
    assert {r["channel"] for r in conn.execute("SELECT channel FROM notifications")} == {"email"}


def test_no_push_rows_when_push_is_off(role_subs, sent_emails):
    dispatch.notify_alert(role_subs, ALERT)
    assert role_subs.execute(
        "SELECT COUNT(*) FROM notifications WHERE channel = 'push'").fetchone()[0] == 0


def _write_during_every_send(db_path, monkeypatch):
    """A fake send_email that, while 'talking to SMTP', tries a write from
    another connection with no busy wait. Returns the list of outcomes."""
    import sqlite3

    outcomes = []

    def _send(to, subject, body_text):
        other = sqlite3.connect(db_path, timeout=0)
        try:
            other.execute("UPDATE machines SET speed_rpm = speed_rpm WHERE machine_id = 'm2'")
            other.commit()
            outcomes.append("ok")
        except sqlite3.OperationalError as exc:
            outcomes.append(str(exc))
        finally:
            other.close()

    monkeypatch.setattr(dispatch, "send_email", _send)
    return outcomes


def test_notify_alert_holds_no_write_lock_while_sending(db_path, conn, monkeypatch):
    # Regression: a notifications row used to be INSERTed after each send and
    # committed after the loop, so from the second recipient on every SMTP
    # send ran while this connection held SQLite's write lock.
    outcomes = _write_during_every_send(db_path, monkeypatch)
    assert dispatch.notify_alert(conn, ALERT) == 2
    assert outcomes == ["ok", "ok"]
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 2


def test_notify_device_incident_holds_no_write_lock_while_sending(db_path, conn, monkeypatch):
    outcomes = _write_during_every_send(db_path, monkeypatch)
    incident = {"id": None, "device_id": "esp32-01", "machine_id": "m1", "kind": "silent",
                "last_seen_at": None, "opened_at": "2026-10-07T00:00:00+00:00"}
    assert dispatch.notify_device_incident(conn, incident) == 2
    assert outcomes == ["ok", "ok"]
