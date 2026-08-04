"""Alert -> email fan-out.

Hooked into the live incremental alert path (src/alerts/live.py) — batch
alerts from src/alerts/generation.py are historical seed data, not something
a human needs paging for.

No machine -> technician assignment exists in this system (out of scope per
.claude/plan.md), so every active admin/supervisor gets paged; operators are
excluded since they're the ones expected to act on the dashboard, not to be
paged externally.
"""
import smtplib
from datetime import datetime, timezone

from src.notifications.email import send_email

_SUBJECT_BY_SEVERITY = {
    "low": "MaintainIQ: {machine_id} is degrading",
    "medium": "MaintainIQ ALERT: {machine_id} is faulty",
    "high": "MaintainIQ CRITICAL: {machine_id} needs attention",
}


def _recipients(conn) -> list:
    cur = conn.execute(
        "SELECT email, role FROM users WHERE is_active = 1 AND role IN ('admin', 'supervisor')"
    )
    return [dict(row) for row in cur.fetchall()]


def _compose(alert: dict) -> tuple:
    template = _SUBJECT_BY_SEVERITY.get(alert["severity"], "MaintainIQ: {machine_id} status change")
    subject = template.format(machine_id=alert["machine_id"])
    body = (
        f"Machine: {alert['machine_id']}\n"
        f"Health state: {alert['health_state']}\n"
        f"Severity: {alert['severity']}\n"
        f"Probable cause: {alert.get('probable_cause') or 'unknown'}\n"
        f"Opened at: {alert['opened_at']}\n"
        f"\n{alert.get('message') or ''}\n"
    )
    return subject, body


def notify_alert(conn, alert: dict) -> int:
    """Email every active admin/supervisor about `alert` (a dict shaped like
    an `alerts` row). Returns the number of notifications actually delivered.

    Delivery failures (e.g. Mailpit unreachable) are logged as a 'failed'
    notifications row rather than raised — a demo email hiccup should not
    take down the alert/realtime path that triggered it.
    """
    subject, body = _compose(alert)
    now = datetime.now(timezone.utc).isoformat()
    sent = 0

    for recipient in _recipients(conn):
        status = "sent"
        try:
            send_email(recipient["email"], subject, body)
            sent += 1
        except (OSError, smtplib.SMTPException):
            status = "failed"

        conn.execute(
            """INSERT INTO notifications
               (alert_id, recipient_email, recipient_role, subject, body, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (alert.get("id"), recipient["email"], recipient["role"], subject, body, status, now),
        )

    conn.commit()
    return sent
