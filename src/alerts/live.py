"""Incremental counterpart to generate_alerts (src/alerts/generation.py) for
one machine's one new reading at a time, instead of a full dataframe replay.
Mirrors the exact same open/escalate/resolve state machine and its
documented one-open-alert-per-machine, escalate-only-mid-episode semantics —
see generation.py's module docstring for why.

apply_reading is a read-then-write (find the open alert, then insert or
update it) and is called from several threads at once — the live MQTT ingest
worker, replay threads, and the /demo route — each on its own SQLite
connection. Without serialisation two of them can both see "no open alert"
and both insert one, breaking the one-open-alert-per-machine invariant. A
process-wide lock around the whole transition is enough: every caller lives
in this process, and a transition is a couple of indexed statements.

The same lock serialises every other alert read-then-write: acknowledgement
(acknowledge_alert below, used by the ack route and by work-order creation),
the paging ladder's compare-and-set (src/alerts/paging.py), and closing an
alert or recording its outcome (src/feedback/service.py, which resolves with
_resolve_locked below inside its own transaction).

An alert opened through the prediction pipeline also records the
prediction, reading and model version that opened it
(design/2026-10-07-prediction-feedback-design.md, decision 3). Those links
are written on INSERT only: an escalation updates severity and cause but
keeps the opening prediction, so "opening prediction" and opened_at always
describe the same instant.

Demo and real episodes are kept apart: "one open alert per machine" is per
kind, demo (source 'demo', the admin /demo routes) or real (everything
else). A shared episode let a synthetic reading escalate or resolve a real
alert (overwriting the severity and cause that root-cause accuracy scores),
and let a real failure that escalated a demo alert stay labelled 'demo' —
dropped from field accuracy, the retraining export and similar incidents,
which all key on alerts.source. With separate slots alerts.source always
says which kind of reading drove the whole episode.
"""
import json
import threading
from datetime import datetime, timezone

from src.alerts.generation import SEVERITY_BY_STATE, SEVERITY_RANK, format_alert_message

ABNORMAL_STATES = set(SEVERITY_BY_STATE)

DEMO_SOURCE = "demo"

_ALERT_COLUMNS = (
    "id, machine_id, opened_at, resolved_at, severity, health_state, "
    "probable_cause, message, status, source, created_at, "
    "acknowledged_at, acknowledged_by, page_level, last_paged_at, "
    "prediction_id, reading_id, model_version, closed_by, health_episode"
)


# The API's alert shape (schemas.Alert) for list/detail routes: the row plus
# the id of its active work order, if any (work-orders design, "API"), plus
# its recorded outcome as one JSON object (feedback design, decision 15) —
# one statement, no N+1. Read rows through api_alert() to decode it.
API_ALERT_COLUMNS = (
    _ALERT_COLUMNS + ", (SELECT w.id FROM work_orders w WHERE w.alert_id = alerts.id"
    " AND w.status IN ('open','assigned','in_progress')) AS active_work_order_id"
    ", (SELECT json_object('id', f.id, 'alert_id', f.alert_id, 'outcome', f.outcome,"
    " 'actual_cause', f.actual_cause, 'actual_failure_at', f.actual_failure_at,"
    " 'notes', f.notes, 'work_order_id', f.work_order_id,"
    " 'recorded_by', f.recorded_by, 'recorded_by_name', u.name, 'recorded_at', f.recorded_at,"
    " 'updated_by', f.updated_by, 'updated_at', f.updated_at)"
    " FROM alert_feedback f LEFT JOIN users u ON u.id = f.recorded_by"
    " WHERE f.alert_id = alerts.id) AS feedback_json"
)


def api_alert(row) -> dict:
    """An API_ALERT_COLUMNS row as the schemas.Alert dict: feedback_json
    decoded into `feedback` (None when the alert has no recorded outcome)."""
    alert = dict(row)
    raw = alert.pop("feedback_json", None)
    alert["feedback"] = json.loads(raw) if raw else None
    return alert


def _open_alert(conn, machine_id: str, *, demo: bool):
    """machine_id's open alert of the given kind (demo or real), if any."""
    row = conn.execute(
        f"""SELECT {_ALERT_COLUMNS} FROM alerts
            WHERE machine_id = ? AND status = 'open'
              AND (COALESCE(source, '') = '{DEMO_SOURCE}') = ?
            ORDER BY id DESC LIMIT 1""",
        (machine_id, 1 if demo else 0),
    ).fetchone()
    return dict(row) if row is not None else None


_TRANSITION_LOCK = threading.Lock()


def apply_reading(conn, machine_id: str, health_state: str, probable_cause, source: str, timestamp: str,
                  *, prediction_id=None, reading_id=None, model_version=None, health_episode=None):
    """Apply one new (health_state, probable_cause) reading to machine_id's
    alert state. Returns (event_type, alert_dict) where event_type is
    "alert_created" | "alert_escalated" | "alert_resolved", or None if this
    reading did not change alert state (e.g. a second abnormal reading that
    isn't worse than the already-open alert).

    prediction_id / reading_id / model_version are stored on a newly opened
    alert only; callers without a stored prediction (the demo routes) omit
    them and the columns stay NULL.

    health_episode (plan 2026-10-07-ratchet-maintenance-reset, D7) is given
    only with the health ratchet on, and only matters for real readings. A
    reading from an episode the machine has left (a reset or false-alarm
    re-arm overtook it) is dropped. A new alert stores its episode, and an
    abnormal reading with no open alert opens nothing when the newest
    resolved real alert of the same episode was closed by a human at the
    same or a higher severity. With health_episode None this is the legacy
    state machine."""
    with _TRANSITION_LOCK:
        return _apply_reading(conn, machine_id, health_state, probable_cause, source, timestamp,
                              prediction_id=prediction_id, reading_id=reading_id,
                              model_version=model_version, health_episode=health_episode)


def _apply_reading(conn, machine_id: str, health_state: str, probable_cause, source: str, timestamp: str,
                   *, prediction_id=None, reading_id=None, model_version=None, health_episode=None):
    real = source != DEMO_SOURCE
    if not real:
        health_episode = None
    if health_episode is not None:
        row = conn.execute("SELECT episode FROM machine_health_state WHERE machine_id = ?",
                           (machine_id,)).fetchone()
        if health_episode != (row[0] if row is not None else 0):
            return None

    open_alert = _open_alert(conn, machine_id, demo=not real)
    now = datetime.now(timezone.utc).isoformat()

    if health_state in ABNORMAL_STATES:
        severity = SEVERITY_BY_STATE[health_state]

        if open_alert is None:
            if health_episode is not None and _closed_at_or_above(conn, machine_id, health_episode, severity):
                return None
            message = format_alert_message(machine_id, health_state, probable_cause)
            cur = conn.execute(
                """INSERT INTO alerts
                   (machine_id, opened_at, resolved_at, severity, health_state, probable_cause,
                    message, status, source, created_at, prediction_id, reading_id, model_version,
                    health_episode)
                   VALUES (?, ?, NULL, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)""",
                (machine_id, timestamp, severity, health_state, probable_cause, message, source, now,
                 prediction_id, reading_id, model_version, health_episode),
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
                "acknowledged_at": None,
                "acknowledged_by": None,
                "page_level": 0,
                "last_paged_at": None,
                "prediction_id": prediction_id,
                "reading_id": reading_id,
                "model_version": model_version,
                "closed_by": None,
                "health_episode": health_episode,
            }

        # Escalation keeps the opening prediction links (decision 3).
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


def _closed_at_or_above(conn, machine_id: str, health_episode: int, severity: str) -> bool:
    """Whether the newest resolved real alert of this episode was closed by a
    human at `severity` or above. A system-resolved newest alert (closed_by
    NULL) does not suppress: nobody decided about it."""
    last = conn.execute(
        f"""SELECT severity, closed_by FROM alerts
            WHERE machine_id = ? AND status = 'resolved' AND health_episode = ?
              AND COALESCE(source, '') != '{DEMO_SOURCE}'
            ORDER BY id DESC LIMIT 1""",
        (machine_id, health_episode),
    ).fetchone()
    return (last is not None and last["closed_by"] is not None
            and SEVERITY_RANK[severity] <= SEVERITY_RANK[last["severity"]])


def get_alert(conn, alert_id: int):
    """One alert row as a dict (the broadcast / API shape), or None."""
    row = conn.execute(f"SELECT {_ALERT_COLUMNS} FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return dict(row) if row is not None else None


def acknowledge_alert(conn, alert_id: int, user_id: int, *, now: str | None = None):
    """Acknowledge alert_id as user_id unless it already is. Returns
    (alert_dict, changed), or (None, False) for an unknown id. Idempotent: a
    second call leaves the first acknowledgement in place."""
    with _TRANSITION_LOCK:
        alert, changed = _acknowledge_locked(conn, alert_id, user_id, now)
        if changed:
            conn.commit()
        else:
            # The 0-row UPDATE still opened an implicit transaction holding
            # SQLite's RESERVED lock; end it now rather than at request teardown.
            conn.rollback()
        return alert, changed


def _acknowledge_locked(conn, alert_id: int, user_id: int, now: str | None):
    """The conditional UPDATE behind acknowledge_alert, without the lock and
    without committing — for callers that already hold _TRANSITION_LOCK and
    commit as part of their own transaction (work-order creation).
    threading.Lock is not re-entrant, so they must not call the wrapper."""
    now = now or datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ?
           WHERE id = ? AND acknowledged_at IS NULL""",
        (now, user_id, alert_id),
    )
    alert = get_alert(conn, alert_id)
    return alert, (cur.rowcount == 1 and alert is not None)


def _resolve_locked(conn, alert_id: int, user_id: int, now: str) -> bool:
    """A human close: resolve alert_id at `now` as user_id if it is still
    open. Returns whether this call resolved it. For callers that already
    hold _TRANSITION_LOCK; does not commit (the _acknowledge_locked contract).
    A later abnormal reading opens a fresh alert, since nothing is open;
    with the ratchet on, a later abnormal reading in the same episode opens a
    fresh alert only if it is more severe. A repair reset or a false-alarm
    re-arm starts a new episode."""
    cur = conn.execute(
        """UPDATE alerts SET status = 'resolved', resolved_at = ?, closed_by = ?
           WHERE id = ? AND status = 'open'""",
        (now, user_id, alert_id),
    )
    return cur.rowcount == 1
