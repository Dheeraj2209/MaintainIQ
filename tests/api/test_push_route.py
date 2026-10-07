"""Tests for /api/push (src/api/routes/push.py,
design/2026-10-07-mobile-operator-pwa-design.md, decision 10 and "API").

Keys come from the push_enabled fixture (tests/conftest.py), which also swaps
the pywebpush sender for a recorder; no request ever leaves the process.
"""
import base64
import sqlite3

import pytest

ROLES = ("admin", "supervisor", "operator")
USER_IDS = {"admin": 1, "supervisor": 2, "operator": 3}
ENDPOINT = "https://fcm.googleapis.com/fcm/send/phone-1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _body(endpoint=ENDPOINT, *, seed=1):
    return {
        "endpoint": endpoint,
        "expirationTime": None,
        "keys": {"p256dh": _b64(b"\x04" + bytes([seed]) * 64), "auth": _b64(bytes([seed]) * 16)},
    }


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM push_subscriptions ORDER BY id")]
    finally:
        conn.close()


def _db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# --- GET /api/push/vapid-public-key -------------------------------------------------

def test_public_key_requires_auth(anon_client):
    assert anon_client.get("/api/push/vapid-public-key").status_code == 401


@pytest.mark.parametrize("role", ROLES)
def test_public_key_for_every_role(auth_client, push_enabled, role):
    import os

    resp = auth_client(role).get("/api/push/vapid-public-key")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "public_key": os.environ["VAPID_PUBLIC_KEY"]}
    assert resp.headers["cache-control"] == "no-store"


def test_public_key_when_disabled(auth_client):
    resp = auth_client("operator").get("/api/push/vapid-public-key")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "public_key": None}


# --- POST /api/push/subscribe -------------------------------------------------------

def test_subscribe_requires_auth(anon_client, push_enabled):
    assert anon_client.post("/api/push/subscribe", json=_body()).status_code == 401


def test_subscribe_binds_to_the_caller_and_upserts(auth_client, push_enabled, db_path):
    client = auth_client("operator")
    resp = client.post("/api/push/subscribe", json=_body(), headers={"User-Agent": "Pixel"})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert set(out) == {"id", "endpoint", "is_active", "created_at", "updated_at", "last_used_at"}
    assert out["endpoint"] == ENDPOINT and out["is_active"] is True
    (row,) = _rows(db_path)
    assert row["user_id"] == USER_IDS["operator"]
    assert row["user_agent"] == "Pixel"
    first_updated = row["updated_at"]

    resp = client.post("/api/push/subscribe", json=_body(seed=2))
    assert resp.status_code == 200
    assert resp.json()["id"] == out["id"]
    (row,) = _rows(db_path)
    assert row["p256dh"] == _body(seed=2)["keys"]["p256dh"]
    assert row["auth"] == _body(seed=2)["keys"]["auth"]
    assert row["updated_at"] > first_updated
    assert row["created_at"] == out["created_at"]


def test_subscribe_rebinds_a_shared_phone(auth_client, push_enabled, db_path):
    assert auth_client("operator").post("/api/push/subscribe", json=_body()).status_code == 200
    assert auth_client("supervisor").post("/api/push/subscribe", json=_body()).status_code == 200
    (row,) = _rows(db_path)
    assert row["user_id"] == USER_IDS["supervisor"]


def test_resubscribe_reactivates_an_expired_row(auth_client, push_enabled, db_path):
    client = auth_client("operator")
    client.post("/api/push/subscribe", json=_body())
    conn = _db(db_path)
    conn.execute("UPDATE push_subscriptions SET is_active = 0, deactivated_at = 'x'")
    conn.commit()
    conn.close()

    assert client.post("/api/push/subscribe", json=_body()).status_code == 200
    (row,) = _rows(db_path)
    assert (row["is_active"], row["deactivated_at"]) == (1, None)


@pytest.mark.parametrize("body, status", [
    (_body("http://fcm.googleapis.com/fcm/send/x"), 400),
    (_body("https://169.254.169.254/latest/meta-data"), 400),
    # Parser differential: urlsplit kept the backslash in the host, requests
    # sent the POST to 169.254.169.254 / 127.0.0.1 (SSRF).
    (_body("https://169.254.169.254\\.fcm.googleapis.com/latest"), 400),
    (_body("https://127.0.0.1\\.fcm.googleapis.com/x"), 400),
    (_body("https://fcm.googleapis.com:8443/x"), 400),
    ({**_body(), "keys": {"p256dh": _b64(b"\x04" * 10), "auth": _b64(bytes(16))}}, 400),
    ({**_body(), "keys": {"p256dh": _b64(b"\x04" + bytes(64)), "auth": _b64(bytes(8))}}, 400),
    ({**_body(), "keys": {"p256dh": "!!not base64!!", "auth": _b64(bytes(16))}}, 400),
    ({"endpoint": ENDPOINT}, 422),
    ({**_body(), "endpoint": "https://fcm.googleapis.com/" + "x" * 2048}, 422),
])
def test_subscribe_validation(auth_client, push_enabled, db_path, body, status):
    resp = auth_client("operator").post("/api/push/subscribe", json=body)
    assert resp.status_code == status, resp.text
    assert _rows(db_path) == []


def test_subscribe_error_messages(auth_client, push_enabled):
    client = auth_client("operator")
    resp = client.post("/api/push/subscribe", json=_body("http://fcm.googleapis.com/x"))
    assert resp.json()["detail"] == "endpoint must be an https URL on a known push service"
    bad_keys = {**_body(), "keys": {"p256dh": "AAAA", "auth": "AAAA"}}
    assert client.post("/api/push/subscribe", json=bad_keys).json()["detail"] == (
        "invalid subscription keys")


def test_subscribe_when_disabled(auth_client, db_path):
    resp = auth_client("operator").post("/api/push/subscribe", json=_body())
    assert resp.status_code == 503
    assert resp.json()["detail"] == "push notifications are not configured"
    assert _rows(db_path) == []


def test_user_agent_is_truncated(auth_client, push_enabled, db_path):
    auth_client("operator").post("/api/push/subscribe", json=_body(),
                                 headers={"User-Agent": "x" * 400})
    assert len(_rows(db_path)[0]["user_agent"]) == 255


# --- DELETE /api/push/subscribe -----------------------------------------------------

def test_unsubscribe_removes_only_the_callers_row(auth_client, push_enabled, db_path):
    operator = auth_client("operator")
    operator.post("/api/push/subscribe", json=_body())
    other = "https://fcm.googleapis.com/fcm/send/supervisor-phone"
    auth_client("supervisor").post("/api/push/subscribe", json=_body(other))

    # Someone else's endpoint: 204, and that row is untouched.
    resp = operator.request("DELETE", "/api/push/subscribe", json={"endpoint": other})
    assert resp.status_code == 204
    assert [r["endpoint"] for r in _rows(db_path)] == [ENDPOINT, other]

    resp = operator.request("DELETE", "/api/push/subscribe", json={"endpoint": ENDPOINT})
    assert resp.status_code == 204
    assert [r["endpoint"] for r in _rows(db_path)] == [other]

    # Unknown endpoint: still 204.
    resp = operator.request("DELETE", "/api/push/subscribe", json={"endpoint": ENDPOINT})
    assert resp.status_code == 204


def test_unsubscribe_requires_auth(anon_client):
    resp = anon_client.request("DELETE", "/api/push/subscribe", json={"endpoint": ENDPOINT})
    assert resp.status_code == 401


def test_unsubscribe_works_when_push_is_disabled(auth_client, db_path, add_sub):
    conn = _db(db_path)
    add_sub(conn, USER_IDS["operator"], ENDPOINT)
    conn.close()
    resp = auth_client("operator").request("DELETE", "/api/push/subscribe",
                                           json={"endpoint": ENDPOINT})
    assert resp.status_code == 204
    assert _rows(db_path) == []


# --- POST /api/push/test ------------------------------------------------------------

def test_test_push_when_disabled(auth_client):
    assert auth_client("operator").post("/api/push/test").status_code == 503


def test_test_push_without_a_subscription(auth_client, push_enabled):
    resp = auth_client("operator").post("/api/push/test")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "no active push subscription for this user"


def test_test_push_sends_to_the_callers_devices_only(auth_client, push_enabled, db_path):
    operator = auth_client("operator")
    operator.post("/api/push/subscribe", json=_body())
    operator.post("/api/push/subscribe", json=_body("https://fcm.googleapis.com/fcm/send/tab"))
    auth_client("admin").post("/api/push/subscribe",
                              json=_body("https://fcm.googleapis.com/fcm/send/admin"))

    resp = operator.post("/api/push/test")
    assert resp.status_code == 200
    assert resp.json() == {"sent": 2, "failed": 0, "expired": 0}
    assert push_enabled.endpoints == [ENDPOINT, "https://fcm.googleapis.com/fcm/send/tab"]
    conn = _db(db_path)
    rows = conn.execute("SELECT * FROM notifications WHERE channel = 'push'").fetchall()
    conn.close()
    assert len(rows) == 2
    assert all(r["alert_id"] is None and r["device_incident_id"] is None for r in rows)
    assert all(r["recipient_email"] == "operator@maintainiq.local" for r in rows)


def test_test_push_requires_auth(anon_client):
    assert anon_client.post("/api/push/test").status_code == 401
