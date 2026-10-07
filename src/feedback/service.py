"""Closing alerts and recording what actually happened
(design/2026-10-07-prediction-feedback-design.md, decisions 4-7).

One alert_feedback row per alert holds the outcome a person recorded:
confirmed_failure, maintenance_prevented, false_alarm or unknown, plus the
actual cause, an optional failure time, notes and the work order that
handled it. Any signed-in role may create it; replacing it is limited to its
recorder, an admin or a supervisor.

There is no 'closed' alert status. Closing an open alert is a human resolve
(status='resolved', resolved_at, closed_by) that also acknowledges it, done
in the same transaction as the feedback upsert. Paging stops because the
ladder only looks at open alerts. Closing an alert that is already resolved
(an auto-resolve or a racing close) is not an error: the feedback is still
recorded and the caller learns closed=False.

Health episodes (docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md,
D7, D12): with the health ratchet on, a human close keeps the machine's held
level, and later abnormal readings of the same episode open a fresh alert
only if they are more severe. A repair reset starts a new episode. So does a
false-alarm re-arm: closing (or recording feedback on a no-longer-open) real
alert of the machine's current episode with outcome false_alarm or cause
sensor_or_data_quality_issue bumps the episode and drops the held level to
healthy, in the same transaction, keeping the epoch and baseline. The next
abnormal reading then opens and pages one new alert. Other outcomes keep the
held level and the suppression until a repair resets it. An open alert never
re-arms: that would show the machine healthy while its alert is still open.
Nor does any alert while another real alert is open, or any but the episode's
newest real alert: a stale edit to an older alert keeps the newer hold.

`actor` is the get_current_user dict (id, role, name).

Concurrency: both mutators hold live._TRANSITION_LOCK for their whole
read-then-write, like every other alert transition, run every statement on
`conn` and commit once. Any failure rolls the transaction back, so a refused
upsert leaves an open alert open. They never take work_orders.service._LOCK,
so the lock order cannot deadlock.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from src.alerts import live
from src.telemetry.device_health import parse_iso

OUTCOMES = ("confirmed_failure", "maintenance_prevented", "false_alarm", "unknown")
TRUE_POSITIVE_OUTCOMES = ("confirmed_failure", "maintenance_prevented")
# The rule-based classifier's labels (src/root_cause/rule_based.py) plus
# 'other', which the classifier can never emit.
CAUSES = ("bearing_wear", "imbalance", "sensor_or_data_quality_issue", "unknown", "other")
EDITOR_ROLES = ("admin", "supervisor")
NOTES_MAX = 2000
# A failure time a little ahead of the server clock is a skewed phone, not a
# prediction of the future.
FUTURE_SKEW = timedelta(minutes=5)

_FEEDBACK_SELECT = """
    SELECT f.id, f.alert_id, f.outcome, f.actual_cause, f.actual_failure_at, f.notes,
           f.work_order_id, f.recorded_by, u.name AS recorded_by_name, f.recorded_at,
           f.updated_by, f.updated_at
    FROM alert_feedback f
    LEFT JOIN users u ON u.id = f.recorded_by
"""


class FeedbackError(ValueError):
    """Invalid input (400)."""


class FeedbackNotFound(FeedbackError):
    """Unknown alert (404)."""


class FeedbackForbidden(FeedbackError):
    """Replacing someone else's feedback without an editor role (403)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Validation ------------------------------------------------------------------------

def _validate(*, outcome, actual_cause, actual_failure_at, notes, now: str) -> dict:
    """Everything that needs no DB access; returns the values to store."""
    if outcome not in OUTCOMES:
        raise FeedbackError(f"invalid outcome: {outcome!r}")
    if actual_cause is not None and actual_cause not in CAUSES:
        raise FeedbackError(f"invalid actual_cause: {actual_cause!r}")
    if notes is not None and len(notes) > NOTES_MAX:
        raise FeedbackError(f"notes must be at most {NOTES_MAX} characters")

    failure_at = None
    if actual_failure_at not in (None, ""):
        parsed = parse_iso(actual_failure_at)
        if parsed is None:
            raise FeedbackError(
                f"actual_failure_at is not a valid ISO-8601 timestamp: {actual_failure_at!r}")
        if outcome != "confirmed_failure":
            raise FeedbackError("actual_failure_at only applies to outcome confirmed_failure")
        if parsed > parse_iso(now) + FUTURE_SKEW:
            raise FeedbackError("actual_failure_at is in the future")
        # May precede opened_at: the alert came late. That is measured, not refused.
        failure_at = parsed.isoformat()

    return {
        "outcome": outcome,
        "actual_cause": actual_cause,
        "actual_failure_at": failure_at,
        "notes": notes,
    }


def _check_work_order(conn, work_order_id, alert: dict) -> None:
    if work_order_id is None:
        return
    row = conn.execute(
        "SELECT machine_id, alert_id FROM work_orders WHERE id = ?", (work_order_id,)
    ).fetchone()
    if row is None:
        raise FeedbackError(f"unknown work order: {work_order_id}")
    if row["machine_id"] != alert["machine_id"]:
        raise FeedbackError(
            f"work order {work_order_id} is for machine {row['machine_id']}, not {alert['machine_id']}")
    if row["alert_id"] is not None and row["alert_id"] != alert["id"]:
        raise FeedbackError(f"work order {work_order_id} belongs to alert {row['alert_id']}")


def _locked_alert(conn, alert_id: int) -> dict:
    alert = live.get_alert(conn, alert_id)
    if alert is None:
        raise FeedbackNotFound(f"unknown alert: {alert_id}")
    return alert


# --- Reads --------------------------------------------------------------------------------

def _feedback_row(conn, alert_id: int):
    row = conn.execute(_FEEDBACK_SELECT + " WHERE f.alert_id = ?", (alert_id,)).fetchone()
    return dict(row) if row is not None else None


def get_feedback(conn, alert_id: int) -> dict | None:
    """The alert's feedback, or None if nobody has recorded any."""
    if live.get_alert(conn, alert_id) is None:
        raise FeedbackNotFound(f"unknown alert: {alert_id}")
    return _feedback_row(conn, alert_id)


# --- Writes -------------------------------------------------------------------------------

def _upsert(conn, alert_id: int, actor: dict, values: dict, work_order_id, now: str) -> bool:
    """Insert or replace the alert's feedback (decision 6). Returns whether
    it was created. Does not commit."""
    existing = conn.execute(
        "SELECT recorded_by FROM alert_feedback WHERE alert_id = ?", (alert_id,)
    ).fetchone()
    try:
        if existing is None:
            conn.execute(
                """INSERT INTO alert_feedback
                       (alert_id, outcome, actual_cause, actual_failure_at, notes, work_order_id,
                        recorded_by, recorded_at)
                   VALUES (:alert_id, :outcome, :actual_cause, :actual_failure_at, :notes,
                           :work_order_id, :actor, :now)""",
                {**values, "alert_id": alert_id, "work_order_id": work_order_id,
                 "actor": actor["id"], "now": now},
            )
            return True
        if existing["recorded_by"] != actor.get("id") and actor.get("role") not in EDITOR_ROLES:
            raise FeedbackForbidden("only the recorder, an admin or a supervisor may change this feedback")
        # A full replacement: omitted optional fields become NULL. recorded_*
        # never change; updated_* say who edited last.
        conn.execute(
            """UPDATE alert_feedback
               SET outcome = :outcome, actual_cause = :actual_cause,
                   actual_failure_at = :actual_failure_at, notes = :notes,
                   work_order_id = :work_order_id, updated_by = :actor, updated_at = :now
               WHERE alert_id = :alert_id""",
            {**values, "alert_id": alert_id, "work_order_id": work_order_id,
             "actor": actor["id"], "now": now},
        )
        return False
    except sqlite3.IntegrityError as exc:
        # A CHECK backstop; _validate should already have refused the values.
        raise FeedbackError(f"invalid feedback: {exc}") from None


def _maybe_rearm_locked(conn, alert: dict, values: dict, now: str) -> None:
    """The false-alarm re-arm (plan D12), when the feedback says the held alert
    was false and the alert is a real, no-longer-open alert of the machine's
    current episode. The caller holds live._TRANSITION_LOCK and commits."""
    from src.prediction import health_epoch  # lazy, to avoid an import cycle (plan Task 7b)

    if (values["outcome"] not in health_epoch.REARM_OUTCOMES
            and values["actual_cause"] not in health_epoch.REARM_CAUSES):
        return
    episode = alert.get("health_episode")
    if alert.get("source") == live.DEMO_SOURCE or episode is None:
        return
    if live.get_alert(conn, alert["id"])["status"] == "open":
        return
    row = health_epoch.current(conn, alert["machine_id"])
    if row is None or row.episode != episode:
        return
    # Only the episode's newest real alert speaks for the held level, and
    # never while another real alert is still open: a stale edit to an older
    # alert must not drop a newer alert's hold.
    if live._open_alert(conn, alert["machine_id"], demo=False) is not None:
        return
    newest = conn.execute(
        f"""SELECT MAX(id) FROM alerts
            WHERE machine_id = ? AND health_episode = ?
              AND COALESCE(source, '') != '{live.DEMO_SOURCE}'""",
        (alert["machine_id"], episode),
    ).fetchone()[0]
    if newest != alert["id"]:
        return
    health_epoch._rearm_locked(conn, alert["machine_id"], now=now)


def record_feedback(conn, alert_id: int, actor: dict, *, outcome, actual_cause=None,
                    actual_failure_at=None, notes=None, work_order_id=None, now=None):
    """Create or replace alert_id's feedback without touching its status
    (decision 5); open and resolved alerts alike. Returns
    (alert, feedback, created)."""
    now = now or _now()
    values = _validate(outcome=outcome, actual_cause=actual_cause,
                       actual_failure_at=actual_failure_at, notes=notes, now=now)
    with live._TRANSITION_LOCK:
        try:
            alert = _locked_alert(conn, alert_id)
            _check_work_order(conn, work_order_id, alert)
            created = _upsert(conn, alert_id, actor, values, work_order_id, now)
            _maybe_rearm_locked(conn, alert, values, now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return live.get_alert(conn, alert_id), _feedback_row(conn, alert_id), created


def close_alert(conn, alert_id: int, actor: dict, *, outcome, actual_cause=None,
                actual_failure_at=None, notes=None, work_order_id=None, now=None):
    """Close alert_id as actor and record its outcome, in one transaction
    (decision 4). An open alert is resolved at `now` with closed_by = actor
    and acknowledged if nobody has; an already-resolved one only gets the
    feedback. Returns (alert, feedback, closed)."""
    now = now or _now()
    values = _validate(outcome=outcome, actual_cause=actual_cause,
                       actual_failure_at=actual_failure_at, notes=notes, now=now)
    with live._TRANSITION_LOCK:
        try:
            alert = _locked_alert(conn, alert_id)
            _check_work_order(conn, work_order_id, alert)
            closed = live._resolve_locked(conn, alert_id, actor["id"], now)
            if closed:
                # Whoever closed it has seen it; keeps time-to-acknowledge and
                # the paging ladder consistent (as work-order creation does).
                live._acknowledge_locked(conn, alert_id, actor["id"], now)
            _upsert(conn, alert_id, actor, values, work_order_id, now)
            _maybe_rearm_locked(conn, alert, values, now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return live.get_alert(conn, alert_id), _feedback_row(conn, alert_id), closed
