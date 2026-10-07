"""Alert -> email + Web Push fan-out.

Hooked into the live incremental alert path (src/alerts/live.py) — batch
alerts from src/alerts/generation.py are historical seed data, not something
a human needs paging for.

No machine -> technician assignment exists in this system (out of scope per
.claude/plan.md), so pages go to everyone in a role. Two channels
(design/2026-10-07-mobile-operator-pwa-design.md, decision 2):

    trigger                              email               push
    alert opened / severity up (lvl 0)   admin, supervisor   admin, supervisor, operator
    ladder level 1                       supervisor          supervisor
    ladder level 2                       admin               admin
    sensor-node silence                  admin, supervisor   admin, supervisor, operator
    resolutions, acks, closes            -                   -

Operators are pushed (not emailed) at level 0 because the person next to the
machine has a phone, not a dashboard; they are not on the ladder, which
exists to reach someone above whoever ignored the first page. Push goes out
after the emails are committed, through src/notifications/push.py, and is a
no-op until VAPID keys are configured. Every attempt is one notifications
row; `channel` says which ('email' rows rely on the column DEFAULT).

Sensor-node silence (design/2026-10-06-device-health-design.md) pages the same
roles through notify_device_incident; those log rows carry
device_incident_id instead of alert_id.

Paging ladder (design/2026-10-07-work-orders-escalation-design.md): the
email sent when an alert opens or its severity rises is level 0. While the
alert stays open, unacknowledged and without an active work order, the
background job in src/alerts/paging.py re-pages through notify_alert with
`roles` and `page_level`: supervisors at level 1, admins at level 2. Those
emails carry an "[Unacknowledged — page n]" subject prefix.
"""
import logging
import smtplib
from datetime import datetime, timezone

from src.notifications import push
from src.notifications.email import send_email

logger = logging.getLogger(__name__)

_SUBJECT_BY_SEVERITY = {
    "low": "MaintainIQ: {machine_id} is degrading",
    "medium": "MaintainIQ ALERT: {machine_id} is faulty",
    "high": "MaintainIQ CRITICAL: {machine_id} needs attention",
}


# Who gets paged unless a caller says otherwise.
DEFAULT_PAGED_ROLES = ("admin", "supervisor")
# Who gets a push at level 0 and for device silence (decision 2 of the PWA design).
DEFAULT_PUSHED_ROLES = ("admin", "supervisor", "operator")


def _push_audience(roles, page_level: int, push_roles) -> tuple:
    """Explicit push_roles win; otherwise level 0 adds operators and a
    ladder level pushes exactly the roles it emails."""
    if push_roles is not None:
        return tuple(push_roles)
    return DEFAULT_PUSHED_ROLES if page_level == 0 else tuple(roles)


def _recipients(conn, roles=DEFAULT_PAGED_ROLES) -> list:
    roles = tuple(roles)
    if not roles:
        return []
    placeholders = ", ".join("?" for _ in roles)
    cur = conn.execute(
        f"SELECT email, role FROM users WHERE is_active = 1 AND role IN ({placeholders})",
        roles,
    )
    return [dict(row) for row in cur.fetchall()]


def _send_all(recipients: list, subject: str, body: str) -> list:
    """Send every email first and return [(recipient, 'sent'|'failed')].

    No SQLite write happens in here: the caller logs all the rows afterwards
    in one short transaction (as push._push does). Inserting a row after each
    send instead held the database write lock across every later SMTP
    connect/send (up to SMTP_TIMEOUT each), so a slow mail relay made acks,
    closes, ingest and the watchdog fail with 'database is locked'. The
    connection must also not be mid-transaction when this runs."""
    outcomes = []
    for recipient in recipients:
        try:
            send_email(recipient["email"], subject, body)
            outcomes.append((recipient, "sent"))
        except (OSError, smtplib.SMTPException):
            outcomes.append((recipient, "failed"))
    return outcomes


# Highest paging level (src/alerts/paging.MAX_PAGE_LEVEL), for the body text.
_MAX_PAGE_LEVEL = 2


def _compose(alert: dict, page_level: int = 0) -> tuple:
    template = _SUBJECT_BY_SEVERITY.get(alert["severity"], "MaintainIQ: {machine_id} status change")
    subject = template.format(machine_id=alert["machine_id"])
    paging = ""
    if page_level >= 1:
        subject = f"[Unacknowledged — page {page_level}] {subject}"
        paging = (
            f"Unacknowledged since {alert['opened_at']}. Paging level {page_level} of "
            f"{_MAX_PAGE_LEVEL}. Acknowledge it or create a work order on the Alerts page.\n"
        )
    body = paging + (
        f"Machine: {alert['machine_id']}\n"
        f"Health state: {alert['health_state']}\n"
        f"Severity: {alert['severity']}\n"
        f"Probable cause: {alert.get('probable_cause') or 'unknown'}\n"
        f"Opened at: {alert['opened_at']}\n"
        f"\n{alert.get('message') or ''}\n"
    )
    return subject, body


def notify_alert(conn, alert: dict, *, roles=DEFAULT_PAGED_ROLES, page_level: int = 0,
                 push_roles=None) -> int:
    """Email every active user in `roles` (default admins and supervisors)
    about `alert` (a dict shaped like an `alerts` row), then push to
    `push_roles` (see _push_audience). Returns the number of *emails*
    actually delivered; push outcomes live in the notifications rows.
    `page_level` > 0 marks a re-page from the paging ladder in the subject
    and body.

    Delivery failures (e.g. Mailpit unreachable) are logged as a 'failed'
    notifications row rather than raised — a demo email hiccup should not
    take down the alert/realtime path that triggered it.
    """
    subject, body = _compose(alert, page_level)
    now = datetime.now(timezone.utc).isoformat()
    outcomes = _send_all(_recipients(conn, roles), subject, body)
    conn.executemany(
        """INSERT INTO notifications
           (alert_id, recipient_email, recipient_role, subject, body, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(alert.get("id"), r["email"], r["role"], subject, body, status, now)
         for r, status in outcomes],
    )
    conn.commit()
    sent = sum(1 for _, status in outcomes if status == "sent")
    # After the commit: a push problem never undoes or blocks the emails.
    try:
        push.push_to_roles(conn, _push_audience(roles, page_level, push_roles),
                           push.alert_payload(alert, page_level), alert_id=alert.get("id"))
    except Exception:
        logger.exception("Push for alert %s failed", alert.get("id"))
    return sent


_DEVICE_KIND_SUBJECT = {
    "silent": "went silent",
    "lwt": "disconnected",
}
_DEVICE_KIND_EXPLAINED = {
    "silent": "No message for 3 heartbeat intervals (the node stopped reporting).",
    "lwt": "The broker reported the node offline (last will, or a clean shutdown).",
}


def _compose_device(incident: dict) -> tuple:
    kind = incident.get("kind")
    machine = incident.get("machine_id")
    subject = f"MaintainIQ DEVICE: {incident['device_id']} {_DEVICE_KIND_SUBJECT.get(kind, 'is offline')}"
    if machine:
        subject += f" ({machine})"
    body = (
        f"Sensor node: {incident['device_id']}\n"
        f"Machine: {machine or 'unassigned'}\n"
        f"What happened: {_DEVICE_KIND_EXPLAINED.get(kind, 'The node is offline.')}\n"
        f"Last heard: {incident.get('last_seen_at') or 'unknown'}\n"
        f"Opened at: {incident['opened_at']}\n"
        "\nAcknowledge on the Devices page.\n"
    )
    return subject, body


def notify_device_incident(conn, incident: dict, *, roles=DEFAULT_PAGED_ROLES,
                           push_roles=DEFAULT_PUSHED_ROLES) -> int:
    """Email every active user in `roles` that a sensor node went silent
    (`incident` is shaped like a device_incidents row), then push to
    `push_roles`. Returns the number of emails delivered; failures become
    'failed' log rows, as in notify_alert.

    Only called when an incident opens: recoveries are not emailed, just as
    resolved alerts are not."""
    subject, body = _compose_device(incident)
    now = datetime.now(timezone.utc).isoformat()
    outcomes = _send_all(_recipients(conn, roles), subject, body)
    conn.executemany(
        """INSERT INTO notifications
           (alert_id, device_incident_id, recipient_email, recipient_role, subject, body,
            status, created_at)
           VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)""",
        [(incident.get("id"), r["email"], r["role"], subject, body, status, now)
         for r, status in outcomes],
    )
    conn.commit()
    sent = sum(1 for _, status in outcomes if status == "sent")
    try:
        push.push_to_roles(conn, push_roles, push.device_payload(incident),
                           device_incident_id=incident.get("id"))
    except Exception:
        logger.exception("Push for device incident %s failed", incident.get("id"))
    return sent
