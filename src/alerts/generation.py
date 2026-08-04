"""Alert generation from a machine's routed prediction stream (M3).

Turns a per-reading stream of (health_state, source, probable_cause) into a
smaller set of alert episodes, with duplicate-alert suppression for
unresolved issues on the same machine.

Suppression design (documented decision — not specified further than "for
unresolved issues on the same machine" in IMPLEMENTATION_PLAN.md): each
machine has at most one OPEN alert at a time. A new abnormal reading while
an alert is already open does not create a second row; it only escalates
the existing alert's severity if the new reading is worse (e.g.
faulty -> critical), and refreshes its probable cause. The open alert is
closed (status='resolved') the moment the machine reports 'healthy' again.
This keeps one alert per fault episode instead of one per abnormal reading
(23k readings would otherwise produce thousands of alert rows for what is
really a handful of degrade-to-recover episodes per machine) — the tradeoff
is that within one open episode, an *improving* severity (e.g.
critical -> degrading) is not reflected until the episode fully resolves;
only escalation is tracked live. Good enough for an MVP; a real system would
likely want the alert's severity to always reflect the latest reading.
"""
from datetime import datetime, timezone

SEVERITY_BY_STATE = {
    "degrading": "low",
    "faulty": "medium",
    "critical": "high",
}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}
ABNORMAL_STATES = set(SEVERITY_BY_STATE)


def format_alert_message(machine_id: str, health_state: str, probable_cause) -> str:
    cause = probable_cause if probable_cause else "unknown"
    return f"Machine {machine_id} is {health_state} (probable cause: {cause})"


def generate_alerts(long_df) -> list:
    """long_df must already have health_state (post-routing), probable_cause,
    and a `source` column, sorted or sortable by (machine_id, timestamp).

    Returns a list of alert dicts ready for src/storage/db.py's
    insert_alerts.
    """
    now = datetime.now(timezone.utc).isoformat()
    alerts = []
    open_alert_by_machine = {}

    for _, group in long_df.groupby("machine_id"):
        ordered = group.sort_values("timestamp")
        machine_id = ordered["machine_id"].iloc[0]

        for row in ordered.itertuples():
            state = row.health_state
            timestamp = row.timestamp.isoformat()
            open_alert = open_alert_by_machine.get(machine_id)

            if state in ABNORMAL_STATES:
                severity = SEVERITY_BY_STATE[state]
                if open_alert is None:
                    open_alert = {
                        "machine_id": machine_id,
                        "opened_at": timestamp,
                        "resolved_at": None,
                        "severity": severity,
                        "health_state": state,
                        "probable_cause": row.probable_cause,
                        "message": format_alert_message(machine_id, state, row.probable_cause),
                        "status": "open",
                        "source": row.source,
                        "created_at": now,
                    }
                    alerts.append(open_alert)
                    open_alert_by_machine[machine_id] = open_alert
                elif SEVERITY_RANK[severity] > SEVERITY_RANK[open_alert["severity"]]:
                    open_alert["severity"] = severity
                    open_alert["health_state"] = state
                    open_alert["probable_cause"] = row.probable_cause
                    open_alert["message"] = format_alert_message(machine_id, state, row.probable_cause)
            elif open_alert is not None:
                open_alert["resolved_at"] = timestamp
                open_alert["status"] = "resolved"
                open_alert_by_machine[machine_id] = None

    return alerts
