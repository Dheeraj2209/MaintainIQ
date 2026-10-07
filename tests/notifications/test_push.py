"""Tests for src/notifications/push.py — Web Push via VAPID
(design/2026-10-07-mobile-operator-pwa-design.md, decisions 1, 4-6).

pywebpush is never called for real: push._webpush is swapped for the
FakeWebPush recorder from tests/conftest.py (fixture push_enabled).
"""
import asyncio
import base64
import json
import logging
import sqlite3

import pytest
import requests

from src.notifications import dispatch, push

# Demo user ids, in DEMO_USERS order (tests/conftest.py seeds them).
ADMIN, SUPERVISOR, OPERATOR = 1, 2, 3

ALERT = {
    "id": 2,
    "machine_id": "m1",
    "severity": "high",
    "health_state": "critical",
    "probable_cause": "bearing_wear",
    "opened_at": "2026-07-26T00:00:00+00:00",
    "message": "Machine m1 is critical (probable cause: bearing_wear)",
}


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _endpoint(name: str) -> str:
    return f"https://fcm.googleapis.com/fcm/send/{name}"


# --- settings_from_env (decision 5) ---------------------------------------------------

def test_settings_disabled_without_keys(monkeypatch):
    assert push.settings_from_env() is None


def test_settings_disabled_with_only_one_key(monkeypatch, push_enabled):
    monkeypatch.delenv("VAPID_PRIVATE_KEY")
    assert push.settings_from_env() is None


def test_settings_disabled_with_malformed_public_key(monkeypatch, push_enabled, caplog):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "c2hvcnQ")  # "short": wrong length
    with caplog.at_level(logging.ERROR, logger="src.notifications.push"):
        assert push.settings_from_env() is None
    assert "VAPID_PUBLIC_KEY" in caplog.text
    assert "c2hvcnQ" not in caplog.text


def test_settings_disabled_with_malformed_private_key(monkeypatch, push_enabled, caplog):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "bm90LWEta2V5")
    with caplog.at_level(logging.ERROR, logger="src.notifications.push"):
        assert push.settings_from_env() is None
    assert "VAPID_PRIVATE_KEY" in caplog.text
    assert "bm90LWEta2V5" not in caplog.text


def test_settings_valid_with_generated_keys(push_enabled):
    settings = push.settings_from_env()
    assert settings is not None
    assert settings.subject == "mailto:ops@example.com"


def test_subject_defaults_to_smtp_from(monkeypatch, push_enabled):
    monkeypatch.delenv("VAPID_SUBJECT")
    monkeypatch.setenv("SMTP_FROM", "pager@plant.example")
    assert push.settings_from_env().subject == "mailto:pager@plant.example"
    monkeypatch.delenv("SMTP_FROM")
    assert push.settings_from_env().subject == "mailto:alerts@maintainiq.local"


def test_invalid_subject_disables_push(monkeypatch, push_enabled, caplog):
    monkeypatch.setenv("VAPID_SUBJECT", "foo")
    with caplog.at_level(logging.ERROR, logger="src.notifications.push"):
        assert push.settings_from_env() is None
    assert "VAPID_SUBJECT" in caplog.text


# --- generate_vapid_keys / CLI ------------------------------------------------------

def test_generate_vapid_keys_round_trip():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    public, private = push.generate_vapid_keys()
    raw_public = _b64decode(public)
    raw_private = _b64decode(private)
    assert len(raw_public) == 65 and raw_public[0] == 0x04
    assert len(raw_private) == 32
    assert "=" not in public and "=" not in private

    key = ec.derive_private_key(int.from_bytes(raw_private, "big"), ec.SECP256R1())
    signature = key.sign(b"page", ec.ECDSA(hashes.SHA256()))
    verifier = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw_public)
    verifier.verify(signature, b"page", ec.ECDSA(hashes.SHA256()))  # raises on mismatch


def test_cli_generate_vapid(capsys):
    assert push.main(["--generate-vapid"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [line.split("=", 1)[0] for line in lines] == [
        "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"]


def test_cli_without_flags_prints_usage(capsys):
    assert push.main([]) == 2
    assert "--generate-vapid" in capsys.readouterr().err


# --- endpoint_allowed (decision 6) --------------------------------------------------

@pytest.mark.parametrize("endpoint", [
    "https://fcm.googleapis.com/x",
    "https://web.push.apple.com/x",
    "https://wns2-par02p.notify.windows.com/w/?token=abc",
    "https://updates.push.services.mozilla.com/wpush/v2/x",
])
def test_endpoint_allowed(endpoint):
    assert push.endpoint_allowed(endpoint)


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/x",
    "https://evilfcm.googleapis.com.attacker.io/x",
    "https://evilfcm.googleapis.com/x",
    "https://127.0.0.1/x",
    "https://user@fcm.googleapis.com/x",
    "https://fcm.googleapis.com:8443/x",
    "not a url",
    "",
    # Parser differential (SSRF): requests/urllib3 end the host at '\'.
    "https://169.254.169.254\\.fcm.googleapis.com/latest",
    "https://127.0.0.1\\.fcm.googleapis.com/x",
    "https://fcm.googleapis.com\\@127.0.0.1/x",
    "https://127.0.0.1%2f.fcm.googleapis.com/x",
    "https://fcm.googleapis.com /x",
    "https://fcm.googleapis.com/x\ty",
    "https://fcm.googleapis.com/\u00e9",
    "https://[::1].fcm.googleapis.com/x",
])
def test_endpoint_rejected(endpoint):
    assert not push.endpoint_allowed(endpoint)


@pytest.mark.parametrize("endpoint", [
    "https://169.254.169.254\\.fcm.googleapis.com/latest",
    "https://127.0.0.1\\.fcm.googleapis.com/x",
])
def test_send_push_refuses_backslash_endpoints(push_enabled, endpoint, monkeypatch):
    calls = []
    monkeypatch.setattr(push, "_webpush", lambda *a, **k: calls.append(a) or None, raising=False)
    settings = push.settings_from_env()
    assert push.send_push(_sub(endpoint), push.alert_payload(ALERT), settings) != "sent"
    assert calls == []


def test_allowed_hosts_env_replaces_defaults(monkeypatch):
    monkeypatch.setenv("PUSH_ALLOWED_HOSTS", "push.example.org, ")
    assert push.allowed_hosts() == ("push.example.org",)
    assert push.endpoint_allowed("https://eu.push.example.org/x")
    assert not push.endpoint_allowed("https://fcm.googleapis.com/x")


# --- send_push ----------------------------------------------------------------------

def _sub(endpoint=None):
    return {"id": 1, "endpoint": endpoint or _endpoint("a"), "p256dh": "p", "auth": "a"}


def test_send_push_sends_with_vapid_and_urgency(push_enabled):
    settings = push.settings_from_env()
    payload = push.alert_payload(ALERT)
    assert push.send_push(_sub(), payload, settings) == "sent"
    (call,) = push_enabled.calls
    assert call["subscription_info"] == {"endpoint": _endpoint("a"),
                                         "keys": {"p256dh": "p", "auth": "a"}}
    assert json.loads(call["data"]) == payload
    assert call["vapid_private_key"] == settings.private_key
    assert call["vapid_claims"] == {"sub": "mailto:ops@example.com"}
    assert call["ttl"] == 43200
    assert call["timeout"] == 5
    assert call["headers"] == {"Urgency": "high"}


def test_send_push_normal_urgency_for_low_severity(push_enabled):
    push.send_push(_sub(), push.alert_payload({**ALERT, "severity": "low"}),
                   push.settings_from_env())
    assert push_enabled.calls[0]["headers"] == {"Urgency": "normal"}


@pytest.mark.parametrize("outcome, expected", [
    (410, "expired"),
    (404, "expired"),
    (500, "failed"),
    (requests.ConnectionError("down"), "failed"),
    (ValueError("bug"), "failed"),
])
def test_send_push_classifies_failures(push_enabled, outcome, expected):
    push_enabled.fail[_endpoint("a")] = outcome
    assert push.send_push(_sub(), push.test_payload({}), push.settings_from_env()) == expected


def test_send_push_refuses_a_disallowed_host(push_enabled):
    result = push.send_push(_sub("https://10.0.0.5/internal"), push.test_payload({}),
                            push.settings_from_env())
    assert result == "failed"
    assert push_enabled.calls == []


# --- payloads -----------------------------------------------------------------------

@pytest.mark.parametrize("page_level", [0, 1])
def test_alert_payload(page_level):
    payload = push.alert_payload(ALERT, page_level)
    assert payload["title"] == dispatch._compose(ALERT, page_level)[0]
    assert payload["kind"] == "alert" and payload["v"] == 1
    assert payload["url"] == "/m/alerts/2"
    assert payload["tag"] == "alert-2"
    assert payload["alert_id"] == 2 and payload["page_level"] == page_level
    assert payload["severity"] == "high"
    assert payload["body"] == "critical · probable cause bearing_wear"
    encoded = json.dumps(payload).encode()
    assert len(encoded) < 1024
    assert b"@" not in encoded


def test_alert_payload_truncates_the_body():
    payload = push.alert_payload({**ALERT, "probable_cause": "x" * 500})
    assert len(payload["body"]) <= 120


def test_device_payload_links_to_the_machine_or_devices():
    incident = {"id": 7, "device_id": "esp32-a", "machine_id": "m1", "kind": "silent",
                "opened_at": "2026-10-06T12:00:00Z", "last_seen_at": "2026-10-06T11:59:00Z"}
    payload = push.device_payload(incident)
    assert payload["kind"] == "device"
    assert payload["title"] == dispatch._compose_device(incident)[0]
    assert payload["url"] == "/m/machines/m1"
    assert payload["tag"] == "device-7"
    assert payload["body"] == "Last heard 2026-10-06T11:59:00Z"
    assert push.device_payload({**incident, "machine_id": None})["url"] == "/devices"


def test_test_payload():
    payload = push.test_payload({"email": "a@b.c", "name": "A"})
    assert payload["kind"] == "test"
    assert payload["url"] == "/m/settings" and payload["tag"] == "test"
    assert "a@b.c" not in json.dumps(payload)


# --- push_to_roles / push_to_user ---------------------------------------------------

@pytest.fixture
def subs(conn, add_sub):
    """One active subscription per demo role, plus an inactive one and one of
    a deactivated user (a fourth, operator-role account)."""
    add_sub(conn, ADMIN, _endpoint("admin"))
    add_sub(conn, SUPERVISOR, _endpoint("supervisor"))
    add_sub(conn, OPERATOR, _endpoint("operator"))
    add_sub(conn, OPERATOR, _endpoint("operator-old"), is_active=0)
    conn.execute("INSERT INTO users (email, name, hashed_password, role, is_active, created_at) "
                 "VALUES ('gone@maintainiq.local', 'Gone', 'x', 'operator', 0, 'x')")
    gone = conn.execute("SELECT id FROM users WHERE email = 'gone@maintainiq.local'").fetchone()[0]
    add_sub(conn, gone, _endpoint("gone"))
    return conn


def _push_rows(conn):
    return conn.execute("SELECT * FROM notifications WHERE channel = 'push' ORDER BY id").fetchall()


def test_push_to_roles_targets_active_subscriptions_of_active_users(push_enabled, subs):
    counts = push.push_to_roles(subs, ("supervisor", "operator"), push.alert_payload(ALERT),
                                alert_id=2)
    assert counts == {"sent": 2, "failed": 0, "expired": 0}
    assert push_enabled.endpoints == [_endpoint("supervisor"), _endpoint("operator")]
    rows = _push_rows(subs)
    assert [(r["recipient_email"], r["recipient_role"]) for r in rows] == [
        ("supervisor@maintainiq.local", "supervisor"), ("operator@maintainiq.local", "operator")]
    assert all(r["alert_id"] == 2 and r["device_incident_id"] is None for r in rows)
    assert all(r["status"] == "sent" for r in rows)
    assert all(r["subject"] == "MaintainIQ CRITICAL: m1 needs attention" for r in rows)
    assert all(r["body"] == "critical · probable cause bearing_wear" for r in rows)
    stamped = subs.execute("SELECT endpoint FROM push_subscriptions WHERE last_used_at IS NOT NULL "
                           "ORDER BY id").fetchall()
    assert [r[0] for r in stamped] == [_endpoint("supervisor"), _endpoint("operator")]


def test_push_to_roles_deactivates_expired_and_keeps_failed(push_enabled, subs):
    push_enabled.fail[_endpoint("admin")] = 410
    push_enabled.fail[_endpoint("supervisor")] = 500
    counts = push.push_to_roles(subs, ("admin", "supervisor", "operator"),
                                push.alert_payload(ALERT), alert_id=2)
    assert counts == {"sent": 1, "failed": 1, "expired": 1}
    state = {r["endpoint"]: r for r in subs.execute("SELECT * FROM push_subscriptions")}
    assert state[_endpoint("admin")]["is_active"] == 0
    assert state[_endpoint("admin")]["deactivated_at"] is not None
    assert state[_endpoint("supervisor")]["is_active"] == 1
    assert state[_endpoint("supervisor")]["last_used_at"] is None
    statuses = {r["recipient_role"]: r["status"] for r in _push_rows(subs)}
    assert statuses == {"admin": "failed", "supervisor": "failed", "operator": "sent"}


def test_push_to_roles_disabled_is_a_no_op(subs, monkeypatch):
    calls = []
    monkeypatch.setattr(push, "_webpush", lambda **kw: calls.append(kw))
    assert push.push_to_roles(subs, ("admin",), push.alert_payload(ALERT)) == {
        "sent": 0, "failed": 0, "expired": 0}
    assert calls == []
    assert _push_rows(subs) == []


def test_push_to_roles_on_a_v6_connection(push_enabled, conn):
    conn.execute("DROP TABLE push_subscriptions")
    conn.commit()
    assert push.push_to_roles(conn, ("admin",), push.alert_payload(ALERT)) == {
        "sent": 0, "failed": 0, "expired": 0}
    assert push_enabled.calls == []


def test_push_to_roles_without_the_channel_column(push_enabled, subs):
    subs.execute("ALTER TABLE notifications DROP COLUMN channel")
    subs.commit()
    assert push.push_to_roles(subs, ("admin",), push.alert_payload(ALERT))["sent"] == 0
    assert push_enabled.calls == []


def test_push_to_roles_refuses_to_run_on_the_event_loop(push_enabled, subs, caplog):
    async def _on_loop():
        return push.push_to_roles(subs, ("admin",), push.alert_payload(ALERT))

    with caplog.at_level(logging.ERROR, logger="src.notifications.push"):
        counts = asyncio.run(_on_loop())
    assert counts == {"sent": 0, "failed": 0, "expired": 0}
    assert push_enabled.calls == []
    assert "event loop" in caplog.text


def test_push_to_roles_with_no_roles(push_enabled, subs):
    assert push.push_to_roles(subs, (), push.alert_payload(ALERT))["sent"] == 0
    assert push_enabled.calls == []


def test_push_to_user_only_pushes_that_user(push_enabled, subs, add_sub):
    add_sub(subs, OPERATOR, _endpoint("operator-tablet"))
    counts = push.push_to_user(subs, OPERATOR, push.test_payload({}))
    assert counts == {"sent": 2, "failed": 0, "expired": 0}
    assert push_enabled.endpoints == [_endpoint("operator"), _endpoint("operator-tablet")]
    rows = _push_rows(subs)
    assert len(rows) == 2
    assert all(r["alert_id"] is None and r["device_incident_id"] is None for r in rows)


def test_push_rows_never_log_the_endpoint(push_enabled, subs, caplog):
    push_enabled.fail[_endpoint("admin")] = 500
    with caplog.at_level(logging.DEBUG, logger="src.notifications.push"):
        push.push_to_roles(subs, ("admin",), push.alert_payload(ALERT))
    assert _endpoint("admin") not in caplog.text


def test_failed_logging_does_not_leave_an_open_transaction(push_enabled, subs):
    push.push_to_roles(subs, ("admin",), push.alert_payload(ALERT))
    assert not subs.in_transaction
    other = sqlite3.connect(subs.execute("PRAGMA database_list").fetchone()[2])
    assert other.execute("SELECT COUNT(*) FROM notifications WHERE channel = 'push'").fetchone()[0] == 1
    other.close()


def test_no_write_lock_is_held_while_sending(push_enabled, subs, monkeypatch):
    """A slow push service must not hold the SQLite write lock: every send
    happens outside a transaction, so another writer (an acknowledge, the
    MQTT ingest worker) is never blocked behind network I/O."""
    path = subs.execute("PRAGMA database_list").fetchone()[2]
    seen = []

    def _send(**kwargs):
        seen.append(subs.in_transaction)
        other = sqlite3.connect(path, timeout=0.1)
        try:
            other.execute("UPDATE users SET name = name WHERE id = 1")
            other.commit()
        finally:
            other.close()

    monkeypatch.setattr(push, "_webpush", _send)
    counts = push.push_to_roles(subs, ("admin", "supervisor", "operator"),
                                push.alert_payload(ALERT), alert_id=2)
    assert counts == {"sent": 3, "failed": 0, "expired": 0}
    assert seen == [False, False, False]
    assert len(_push_rows(subs)) == 3
    assert not subs.in_transaction
