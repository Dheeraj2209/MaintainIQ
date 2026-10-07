"""Outgoing Web Push (VAPID) — the second paging channel beside email.

design/2026-10-07-mobile-operator-pwa-design.md, decisions 1, 4-6. dispatch
calls push_to_roles after its email loop has committed; this module never
decides *who* is paged, only how a page reaches a browser.

* Settings are read from env on every call, like src/notifications/email.py,
  so tests can monkeypatch them and keys added to .env need no restart. Push
  is off unless VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are both set and
  valid; a bad value is logged by name (never by value) and push stays off.
* Sends are synchronous, one per subscription, with a short timeout — the
  same model as the SMTP send beside it. Every caller of dispatch already runs
  on a worker thread; push_to_roles refuses to run on an event-loop thread so
  a future async caller fails loudly instead of stalling the loop.
* A push endpoint is a URL the server POSTs to and every signed-in role can
  register one, so endpoints must be https on a known push-service host
  (endpoint_allowed). send_push re-checks it, so a row inserted by hand
  cannot bypass the guard.
* One notifications row (channel = 'push') per subscription attempted. A
  404/410 from the push service means the browser dropped the subscription:
  the row is deactivated, not deleted, so dead devices stay visible.

Generate keys once with:

    python -m src.notifications.push --generate-vapid
"""
import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from src.storage.db import table_exists

logger = logging.getLogger(__name__)

# Per-request timeout (seconds). Pages are rare, so sequential sends are fine.
PUSH_TIMEOUT_S = 5
# A page older than a shift is noise: the push service drops it after 12 h.
PUSH_TTL_S = 43200
# Suffixes matched on a dot boundary: Chrome/Edge/Android (FCM), Firefox
# (Mozilla autopush), Windows (WNS) and Safari/iOS (web.push.apple.com).
DEFAULT_ALLOWED_HOSTS = ("fcm.googleapis.com", "android.googleapis.com",
                         "updates.push.services.mozilla.com", "push.services.mozilla.com",
                         "notify.windows.com", "push.apple.com")

_B64URL = re.compile(r"[A-Za-z0-9_-]+")
_BODY_MAX = 120
_ZERO = {"sent": 0, "failed": 0, "expired": 0}


@dataclass(frozen=True)
class PushSettings:
    public_key: str
    private_key: str
    subject: str


def _b64decode(value: str) -> bytes:
    """Decode base64url (trailing padding tolerated); raises ValueError on
    anything else."""
    value = value.strip().rstrip("=")
    # b64decode silently drops characters outside the alphabet; refuse them.
    if not _B64URL.fullmatch(value):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def valid_public_key(value: str) -> bool:
    """An uncompressed P-256 point: 65 bytes starting 0x04 (also the shape
    of a browser subscription's p256dh key)."""
    try:
        raw = _b64decode(value)
    except ValueError:
        return False
    return len(raw) == 65 and raw[0] == 0x04


def valid_auth_secret(value: str) -> bool:
    """A browser subscription's auth secret: 16 bytes."""
    try:
        return len(_b64decode(value)) == 16
    except ValueError:
        return False


def _valid_private_key(value: str) -> bool:
    # py-vapid accepts a raw 32-byte scalar or a DER key; try it exactly as
    # pywebpush will at send time.
    from py_vapid import Vapid

    try:
        Vapid.from_string(private_key=value)
    except Exception:
        return False
    return True


def settings_from_env() -> PushSettings | None:
    """The VAPID settings, or None when push is disabled (decision 5)."""
    public = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    private = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not public or not private:
        logger.info("Web Push disabled: VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are not both set")
        return None
    if not valid_public_key(public):
        logger.error("Web Push disabled: VAPID_PUBLIC_KEY is not a base64url P-256 public key")
        return None
    if not _valid_private_key(private):
        logger.error("Web Push disabled: VAPID_PRIVATE_KEY is not a valid VAPID private key")
        return None
    subject = os.environ.get("VAPID_SUBJECT", "").strip()
    if not subject:
        subject = "mailto:" + os.environ.get("SMTP_FROM", "alerts@maintainiq.local")
    if not subject.startswith(("mailto:", "https://")):
        logger.error("Web Push disabled: VAPID_SUBJECT must start with mailto: or https://")
        return None
    return PushSettings(public_key=public, private_key=private, subject=subject)


def allowed_hosts() -> tuple[str, ...]:
    """PUSH_ALLOWED_HOSTS (comma-separated) replaces the default list."""
    configured = os.environ.get("PUSH_ALLOWED_HOSTS", "")
    hosts = tuple(h.strip().lower().strip(".") for h in configured.split(",") if h.strip())
    return hosts or DEFAULT_ALLOWED_HOSTS


# A plain DNS name: dot-separated labels of letters, digits and hyphens.
_DNS_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


def endpoint_allowed(endpoint: str) -> bool:
    """https, no userinfo, no explicit port, and a host on the allowlist
    (decision 6).

    The host must be read the same way by every parser that will see the
    URL. urlsplit treats a backslash as part of the host, while
    requests/urllib3 (which pywebpush POSTs through) end the authority at it,
    so 'https://169.254.169.254\\.fcm.googleapis.com/x' looked allowlisted
    here yet was sent to 169.254.169.254. So: refuse backslashes, whitespace,
    control and non-ASCII characters anywhere, accept only a plain DNS name
    as the host (no '%', IP literals need not apply), and require urllib3's
    own parse to agree on scheme, host, port and userinfo."""
    if not isinstance(endpoint, str) or not endpoint or not endpoint.isascii():
        return False
    if any(ch == "\\" or ch <= " " or ch == "\x7f" for ch in endpoint):
        return False
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or port is not None or "@" in parts.netloc:
        return False
    host = (parts.hostname or "").lower()
    if not host or parts.netloc.lower() != host or not _DNS_HOST.match(host):
        return False
    try:
        from urllib3.util import parse_url

        seen = parse_url(endpoint)
    except Exception:
        return False
    if (seen.scheme, (seen.host or "").lower(), seen.port, seen.auth) != ("https", host, None, None):
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts())


def _redact(endpoint: str) -> str:
    # Endpoints are bearer capabilities: never log one in full.
    return endpoint[:40] + "…"


# --- payloads (kept well under the ~4 KB Web Push limit) ----------------------------

def _truncate(text: str) -> str:
    return text if len(text) <= _BODY_MAX else text[:_BODY_MAX - 1] + "…"


def alert_payload(alert: dict, page_level: int = 0) -> dict:
    """The push for an alert page. The title is the email subject, so both
    channels read the same (including the "[Unacknowledged — page n]"
    prefix on ladder pages)."""
    from src.notifications.dispatch import _compose

    alert_id = alert.get("id")
    return {
        "v": 1,
        "kind": "alert",
        "title": _compose(alert, page_level)[0],
        "body": _truncate(f"{alert['health_state']} · probable cause "
                          f"{alert.get('probable_cause') or 'unknown'}"),
        "tag": f"alert-{alert_id}",
        "url": f"/m/alerts/{alert_id}",
        "alert_id": alert_id,
        "severity": alert.get("severity"),
        "page_level": page_level,
    }


def device_payload(incident: dict) -> dict:
    """The push for a sensor-node silence incident."""
    from src.notifications.dispatch import _compose_device

    machine = incident.get("machine_id")
    return {
        "v": 1,
        "kind": "device",
        "title": _compose_device(incident)[0],
        "body": _truncate(f"Last heard {incident.get('last_seen_at') or 'unknown'}"),
        "tag": f"device-{incident.get('id')}",
        "url": f"/m/machines/{machine}" if machine else "/devices",
        "device_incident_id": incident.get("id"),
    }


def test_payload(user: dict) -> dict:
    """The push sent by POST /api/push/test. Carries nothing about the user."""
    return {
        "v": 1,
        "kind": "test",
        "title": "MaintainIQ test notification",
        "body": "Push notifications work on this device.",
        "tag": "test",
        "url": "/m/settings",
    }


test_payload.__test__ = False  # not a pytest test, despite the name


# --- sending ------------------------------------------------------------------------

def _webpush(**kwargs):
    """The one call into pywebpush; tests replace this seam."""
    from pywebpush import webpush

    return webpush(**kwargs)


def _urgency(payload: dict) -> str:
    if payload.get("kind") == "device" or payload.get("severity") == "high":
        return "high"
    return "normal"


def send_push(subscription: dict, payload: dict, settings: PushSettings) -> str:
    """Push `payload` to one subscription row. Returns 'sent', 'failed' or
    'expired' (404/410: the browser dropped it). Never raises."""
    endpoint = subscription["endpoint"]
    if not endpoint_allowed(endpoint):
        logger.error("Refusing push to %s: host not allowed", _redact(endpoint))
        return "failed"
    try:
        _webpush(
            subscription_info={"endpoint": endpoint,
                               "keys": {"p256dh": subscription["p256dh"],
                                        "auth": subscription["auth"]}},
            data=json.dumps(payload, separators=(",", ":")),
            vapid_private_key=settings.private_key,
            # A fresh dict per call: pywebpush writes aud/exp into it.
            vapid_claims={"sub": settings.subject},
            ttl=PUSH_TTL_S,
            timeout=PUSH_TIMEOUT_S,
            headers={"Urgency": _urgency(payload)},
        )
    except Exception as exc:
        from pywebpush import WebPushException

        status = exc.status_code if isinstance(exc, WebPushException) else None
        if status in (404, 410):
            logger.info("Push subscription %s expired (%s)", _redact(endpoint), status)
            return "expired"
        logger.warning("Push to %s failed: %s%s", _redact(endpoint), type(exc).__name__,
                       f" (HTTP {status})" if status else "")
        return "failed"
    return "sent"


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _schema_ready(conn) -> bool:
    if not table_exists(conn, "push_subscriptions"):
        return False
    return any(row[1] == "channel" for row in conn.execute("PRAGMA table_info(notifications)"))


def _push(conn, where: str, params: tuple, payload: dict, *, alert_id=None,
          device_incident_id=None) -> dict:
    if _on_event_loop():
        logger.error("Web Push called on the event loop thread; not sending (use a worker thread)")
        return dict(_ZERO)
    settings = settings_from_env()
    if settings is None:
        return dict(_ZERO)
    if not _schema_ready(conn):
        logger.debug("Web Push skipped: the database predates migration 7")
        return dict(_ZERO)

    targets = conn.execute(
        f"""SELECT s.id, s.endpoint, s.p256dh, s.auth, u.email, u.role
            FROM push_subscriptions s JOIN users u ON u.id = s.user_id
            WHERE s.is_active = 1 AND u.is_active = 1 AND {where}
            ORDER BY s.id""",
        params,
    ).fetchall()

    # Send everything first, then log in one short transaction: sqlite3 opens
    # a transaction at the first INSERT, and holding that write lock across
    # network sends (up to PUSH_TIMEOUT_S each) would lock out every other
    # writer — acknowledges, the MQTT ingest worker, the paging tick.
    outcomes = []
    for target in targets:
        result = send_push(dict(target), payload, settings)
        outcomes.append((target, result, datetime.now(timezone.utc).isoformat()))

    counts = dict(_ZERO)
    for target, result, now in outcomes:
        counts[result] += 1
        conn.execute(
            """INSERT INTO notifications
               (alert_id, device_incident_id, recipient_email, recipient_role, subject, body,
                status, channel, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'push', ?)""",
            (alert_id, device_incident_id, target["email"], target["role"], payload["title"],
             payload["body"], "sent" if result == "sent" else "failed", now),
        )
        if result == "sent":
            conn.execute("UPDATE push_subscriptions SET last_used_at = ? WHERE id = ?",
                         (now, target["id"]))
        elif result == "expired":
            conn.execute(
                "UPDATE push_subscriptions SET is_active = 0, deactivated_at = ?, updated_at = ? "
                "WHERE id = ?",
                (now, now, target["id"]),
            )
    conn.commit()
    return counts


def push_to_roles(conn, roles, payload: dict, *, alert_id=None, device_incident_id=None) -> dict:
    """Push to every active subscription of every active user in `roles`.
    Returns {"sent", "failed", "expired"} counts; zeros when push is off."""
    roles = tuple(roles)
    if not roles:
        return dict(_ZERO)
    placeholders = ", ".join("?" for _ in roles)
    return _push(conn, f"u.role IN ({placeholders})", roles, payload, alert_id=alert_id,
                 device_incident_id=device_incident_id)


def push_to_user(conn, user_id: int, payload: dict) -> dict:
    """Push to one user's active subscriptions (POST /api/push/test)."""
    return _push(conn, "u.id = ?", (user_id,), payload)


# --- key generation -----------------------------------------------------------------

def generate_vapid_keys() -> tuple[str, str]:
    """A new P-256 key pair as (public, private): the raw uncompressed point
    and the raw 32-byte scalar, both unpadded base64url."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return _b64encode(public), _b64encode(private)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.notifications.push",
                                     description="Web Push helpers.")
    parser.add_argument("--generate-vapid", action="store_true",
                        help="print a new VAPID key pair as .env lines")
    args = parser.parse_args(argv)
    if not args.generate_vapid:
        parser.print_usage(sys.stderr)
        return 2
    public, private = generate_vapid_keys()
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
    print("VAPID_SUBJECT=mailto:you@example.com")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
