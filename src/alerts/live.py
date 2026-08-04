"""Incremental counterpart to generate_alerts (src/alerts/generation.py) for
one machine's one new reading at a time, instead of a full dataframe replay.
Mirrors the exact same open/escalate/resolve state machine and its
documented one-open-alert-per-machine, escalate-only-mid-episode semantics —
see generation.py's module docstring for why.
"""
from datetime import datetime, timezone

from src.alerts.generation import SEVERITY_BY_STATE, SEVERITY_RANK, format_alert_message

ABNORMAL_STATES = set(SEVERITY_BY_STATE)

_ALERT_COLUMNS = (
    "id, machine_id, opened_at, resolved_at, severity, health_state, "
    "probable_cause, message, status, source, created_at"
)


def _open_alert(conn, machine_id: str):
    row = conn.execute(
        f"SELECT {_ALERT_COLUMNS} FROM alerts WHERE machine_id = ? AND status = 'open'",
        (machine_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def apply_reading(conn, machine_id: str, health_state: str, probable_cause, source: str, timestamp: str):
    """Apply one new (health_state, probable_cause) reading to machine_id's
    alert state. Returns (event_type, alert_dict) where event_type is
    "alert_created" | "alert_escalated" | "alert_resolved", or None if this
    reading did not change alert state (e.g. a second abnormal reading that
    isn't worse than the already-open alert)."""
    open_alert = _open_alert(conn, machine_id)
    now = datetime.now(timezone.utc).isoformat()

    if health_state in ABNORMAL_STATES:
        severity = SEVERITY_BY_STATE[health_state]

        if open_alert is None:
            message = format_alert_message(machine_id, health_state, probable_cause)
            cur = conn.execute(
                """INSERT INTO alerts
                   (machine_id, opened_at, resolved_at, severity, health_state, probable_cause,
                    message, status, source, created_at)
                   VALUES (?, ?, NULL, ?, ?, ?, ?, 'open', ?, ?)""",
                (machine_id, timestamp, severity, health_state, probable_cause, message, source, now),
            )
            conn.commit()
            return "alert_created", {
                "id": cur.lastrowid,
                "machine_id": machine_id,
                "opened_at": timestamp,
                "resolved_at": None,
                "severity": severity,
                "health_state": health_state,
                "probable_cause": probable_cause,
                "message": message,
                "status": "open",
                "source": source,
                "created_at": now,
            }

        if SEVERITY_RANK[severity] > SEVERITY_RANK[open_alert["severity"]]:
            message = format_alert_message(machine_id, health_state, probable_cause)
            conn.execute(
                """UPDATE alerts SET severity = ?, health_state = ?, probable_cause = ?, message = ?
                   WHERE id = ?""",
                (severity, health_state, probable_cause, message, open_alert["id"]),
            )
            conn.commit()
            open_alert.update(severity=severity, health_state=health_state, probable_cause=probable_cause, message=message)
            return "alert_escalated", open_alert

        return None

    if open_alert is not None:
        conn.execute(
            "UPDATE alerts SET resolved_at = ?, status = 'resolved' WHERE id = ?",
            (timestamp, open_alert["id"]),
        )
        conn.commit()
        open_alert.update(resolved_at=timestamp, status="resolved")
        return "alert_resolved", open_alert

    return None
