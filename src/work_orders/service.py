"""Work orders: tracked repair jobs, optionally raised from an alert
(design/2026-10-07-work-orders-escalation-design.md, decisions 2-6, 12-13).

A work order moves open -> assigned -> in_progress -> done and can be
cancelled from any non-terminal state. Every change appends one
work_order_events row, so the table is an audit trail of who did what.

RBAC lives here rather than in the routes, so the whole table in decision 2
is unit-tested without HTTP:

* anyone may raise an order from an alert; only admins and supervisors may
  raise free-standing orders, assign (or reassign), edit and cancel;
* the assignee, an admin or a supervisor may start and complete.

`actor` is the get_current_user dict (id, role, name).

Concurrency: every mutator holds the process-wide _LOCK, re-reads the row,
validates the edge and writes with a conditional UPDATE (WHERE status = the
status it read), so two racing transitions cannot both win. Creation from an
alert instead holds live._TRANSITION_LOCK, because it also acknowledges the
alert (an alert read-then-write); it never takes _LOCK as well, so the lock
order cannot deadlock. The one-active-order-per-alert rule is a partial
unique index, which makes duplicate creation race-free even across app
instances.
"""
import sqlite3
import threading
from datetime import datetime, timezone

from src.alerts import live
from src.feedback import service as feedback
from src.maintenance.records import MaintenanceError, _log_maintenance_locked
from src.prediction import health_epoch
from src.storage.db import table_exists

ACTIVE_STATUSES = ("open", "assigned", "in_progress")
TERMINAL_STATUSES = ("done", "cancelled")
STATUSES = ACTIVE_STATUSES + TERMINAL_STATUSES
SUPERVISING_ROLES = ("admin", "supervisor")
PRIORITIES = ("low", "medium", "high")
PRIORITY_BY_SEVERITY = {"low": "low", "medium": "medium", "high": "high"}
MAINTENANCE_TYPES = ("preventive", "corrective")
TITLE_MAX = 200

_LOCK = threading.Lock()

_ORDER_SELECT = """
    SELECT w.id, w.alert_id, w.machine_id, w.status, w.priority, w.title, w.description,
           w.assigned_to, ua.name AS assigned_to_name, w.created_by, uc.name AS created_by_name,
           w.due_at, w.created_at, w.updated_at, w.started_at, w.completed_at, w.cancelled_at,
           w.maintenance_record_id, w.notes
    FROM work_orders w
    LEFT JOIN users ua ON ua.id = w.assigned_to
    LEFT JOIN users uc ON uc.id = w.created_by
"""


class WorkOrderError(ValueError):
    """Invalid input (400)."""


class WorkOrderNotFound(WorkOrderError):
    """Unknown work order or alert (404)."""


class WorkOrderConflict(WorkOrderError):
    """Invalid transition, duplicate active order, or editing a finished one (409)."""


class WorkOrderForbidden(WorkOrderError):
    """Wrong role, or not the assignee (403)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Validation ------------------------------------------------------------------------

def _parse_timestamp(value: str, field: str) -> str:
    """ISO-8601, 'Z' accepted, normalised — the records._parse_timestamp rule."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except (AttributeError, ValueError):
        raise WorkOrderError(f"{field} is not a valid ISO-8601 timestamp: {value!r}") from None


def _optional_timestamp(value, field: str):
    return None if value in (None, "") else _parse_timestamp(value, field)


def _clean_title(title) -> str:
    cleaned = (title or "").strip()
    if not 1 <= len(cleaned) <= TITLE_MAX:
        raise WorkOrderError(f"title must be 1-{TITLE_MAX} characters")
    return cleaned


def _check_priority(priority) -> str:
    if priority not in PRIORITIES:
        raise WorkOrderError(f"invalid priority: {priority}")
    return priority


def _check_assignee(conn, user_id: int) -> None:
    row = conn.execute("SELECT is_active FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not row["is_active"]:
        raise WorkOrderError(f"unknown or inactive user: {user_id}")


def _check_machine(conn, machine_id: str) -> None:
    if conn.execute("SELECT 1 FROM machines WHERE machine_id = ?", (machine_id,)).fetchone() is None:
        raise WorkOrderError(f"unknown machine_id: {machine_id!r}")


def _is_supervising(actor: dict) -> bool:
    return actor.get("role") in SUPERVISING_ROLES


def _require_supervising(actor: dict, what: str) -> None:
    if not _is_supervising(actor):
        raise WorkOrderForbidden(f"only an admin or supervisor may {what}")


def _require_assignee_or_supervising(actor: dict, order: dict, what: str) -> None:
    if not (_is_supervising(actor) or order["assigned_to"] == actor.get("id")):
        raise WorkOrderForbidden(f"only the assignee, an admin or a supervisor may {what}")


def _invalid(status: str, action: str) -> WorkOrderConflict:
    return WorkOrderConflict(f"invalid transition: {status} -> {action}")


# --- Reads --------------------------------------------------------------------------------

def _row(conn, wo_id: int) -> dict:
    row = conn.execute(_ORDER_SELECT + " WHERE w.id = ?", (wo_id,)).fetchone()
    if row is None:
        raise WorkOrderNotFound(f"unknown work order: {wo_id}")
    return dict(row)


def get_work_order(conn, wo_id: int, *, with_events: bool = False) -> dict:
    order = _row(conn, wo_id)
    if with_events:
        order["events"] = [dict(r) for r in conn.execute(
            """SELECT e.id, e.work_order_id, e.event, e.from_status, e.to_status,
                      e.user_id, u.name AS user_name, e.assigned_to, a.name AS assigned_to_name,
                      e.note, e.created_at
               FROM work_order_events e
               LEFT JOIN users u ON u.id = e.user_id
               LEFT JOIN users a ON a.id = e.assigned_to
               WHERE e.work_order_id = ? ORDER BY e.id""",
            (wo_id,),
        ).fetchall()]
        alert = None
        if order["alert_id"] is not None:
            row = conn.execute(
                f"SELECT {live.API_ALERT_COLUMNS} FROM alerts WHERE id = ?", (order["alert_id"],)
            ).fetchone()
            alert = live.api_alert(row) if row is not None else None
        order["alert"] = alert
        # What completing it as corrective work would do with reset_health
        # omitted (plan D2, before the date guard), so the drawer's checkbox
        # starts from the server's decision rather than a client guess.
        order["resets_health_by_default"] = _resets_by_default(conn, order, "corrective")
    return order


def list_work_orders(conn, *, status=None, machine_id=None, assigned_to=None,
                     alert_id=None, limit: int = 200) -> list[dict]:
    """Newest activity first (updated_at DESC, id DESC). `status` is one of
    the five statuses or 'active' (open/assigned/in_progress)."""
    clauses, params = [], []
    if status:
        if status == "active":
            clauses.append(f"w.status IN ({', '.join('?' for _ in ACTIVE_STATUSES)})")
            params.extend(ACTIVE_STATUSES)
        elif status in STATUSES:
            clauses.append("w.status = ?")
            params.append(status)
        else:
            raise WorkOrderError(f"invalid status: {status}")
    for column, value in (("machine_id", machine_id), ("assigned_to", assigned_to),
                          ("alert_id", alert_id)):
        if value is not None:
            clauses.append(f"w.{column} = ?")
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        _ORDER_SELECT + where + " ORDER BY w.updated_at DESC, w.id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_assignees(conn) -> list[dict]:
    """Active users who can be given an order, without their emails
    (decision 12: GET /api/users is admin-only)."""
    rows = conn.execute(
        "SELECT id, name, role FROM users WHERE is_active = 1 ORDER BY role, name"
    ).fetchall()
    return [dict(r) for r in rows]


# --- Writes -------------------------------------------------------------------------------

def _event(conn, wo_id: int, event: str, *, from_status, to_status, user_id, now,
           assigned_to=None, note=None) -> None:
    conn.execute(
        """INSERT INTO work_order_events
               (work_order_id, event, from_status, to_status, user_id, assigned_to, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (wo_id, event, from_status, to_status, user_id, assigned_to, note, now),
    )


def _insert(conn, *, alert_id, machine_id, title, description, priority, assigned_to,
            due_at, actor, now, note) -> int:
    """INSERT the order plus its created (and, if pre-assigned, assigned)
    events. Does not commit."""
    status = "assigned" if assigned_to is not None else "open"
    cur = conn.execute(
        """INSERT INTO work_orders
               (alert_id, machine_id, status, priority, title, description, assigned_to,
                created_by, due_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (alert_id, machine_id, status, priority, title, description, assigned_to,
         actor["id"], due_at, now, now),
    )
    wo_id = cur.lastrowid
    _event(conn, wo_id, "created", from_status=None, to_status="open", user_id=actor["id"],
           now=now, note=note)
    if assigned_to is not None:
        _event(conn, wo_id, "assigned", from_status="open", to_status="assigned",
               user_id=actor["id"], assigned_to=assigned_to, now=now)
    return wo_id


def create_from_alert(conn, alert_id: int, actor: dict, *, title=None, description=None,
                      priority=None, assigned_to=None, due_at=None, now=None):
    """Raise a work order for alert_id and acknowledge the alert if it is
    not yet (decision 4), in one transaction under live._TRANSITION_LOCK.
    Returns (work_order, acknowledged_alert) — the second is None unless
    this call set the acknowledgement. Resolved alerts are allowed."""
    now = now or _now()
    if assigned_to is not None:
        _require_supervising(actor, "assign a work order")
        _check_assignee(conn, assigned_to)
    if title is not None:
        title = _clean_title(title)
    if priority is not None:
        _check_priority(priority)
    due_at = _optional_timestamp(due_at, "due_at")

    with live._TRANSITION_LOCK:
        alert = live.get_alert(conn, alert_id)
        if alert is None:
            raise WorkOrderNotFound(f"unknown alert: {alert_id}")
        if title is None:
            title = _clean_title(
                alert["message"] or f"{alert['machine_id']}: {alert['health_state']} alert")
        try:
            wo_id = _insert(
                conn, alert_id=alert_id, machine_id=alert["machine_id"], title=title,
                description=description,
                priority=priority or PRIORITY_BY_SEVERITY.get(alert["severity"], "medium"),
                assigned_to=assigned_to, due_at=due_at, actor=actor, now=now,
                note=f"from alert #{alert_id}",
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            existing = conn.execute(
                f"""SELECT id FROM work_orders WHERE alert_id = ?
                    AND status IN ({', '.join('?' for _ in ACTIVE_STATUSES)})""",
                (alert_id, *ACTIVE_STATUSES),
            ).fetchone()
            ref = f"#{existing['id']}" if existing is not None else "(unknown)"
            raise WorkOrderConflict(
                f"alert {alert_id} already has an active work order: {ref}") from None
        acknowledged, changed = live._acknowledge_locked(conn, alert_id, actor["id"], now)
        conn.commit()

    return get_work_order(conn, wo_id), (acknowledged if changed else None)


def create(conn, actor: dict, *, machine_id: str, title: str, description=None,
           priority: str = "medium", assigned_to=None, due_at=None, now=None) -> dict:
    """A free-standing order (no alert); admins and supervisors only."""
    _require_supervising(actor, "create a work order without an alert")
    now = now or _now()
    _check_machine(conn, machine_id)
    title = _clean_title(title)
    _check_priority(priority)
    if assigned_to is not None:
        _check_assignee(conn, assigned_to)
    due_at = _optional_timestamp(due_at, "due_at")
    with _LOCK:
        wo_id = _insert(conn, alert_id=None, machine_id=machine_id, title=title,
                        description=description, priority=priority, assigned_to=assigned_to,
                        due_at=due_at, actor=actor, now=now, note=None)
        conn.commit()
    return get_work_order(conn, wo_id)


def _transition(conn, order: dict, *, to_status: str, event: str, actor: dict, now: str,
                sets: dict, assigned_to=None, note=None, commit: bool = True) -> None:
    """Conditional UPDATE from order's current status, plus one event; commits
    unless commit=False. Raises WorkOrderConflict (after a rollback of the
    whole transaction) if the status changed under us."""
    columns = {**sets, "status": to_status, "updated_at": now}
    assignments = ", ".join(f"{name} = ?" for name in columns)
    cur = conn.execute(
        f"UPDATE work_orders SET {assignments} WHERE id = ? AND status = ?",
        (*columns.values(), order["id"], order["status"]),
    )
    if cur.rowcount != 1:
        conn.rollback()
        raise WorkOrderConflict(f"work order {order['id']} changed concurrently; reload and retry")
    _event(conn, order["id"], event, from_status=order["status"], to_status=to_status,
           user_id=actor["id"], assigned_to=assigned_to, note=note, now=now)
    if commit:
        conn.commit()


_UNSET = object()


def edit(conn, wo_id: int, actor: dict, *, title=None, description=_UNSET, priority=None,
         due_at=_UNSET, now=None):
    """Returns (order, changed). title / priority None mean "not given" (both
    are required); an explicit None (or "" for due_at) clears description /
    due_at. Fields equal to their current value are dropped, and an edit that
    changes nothing writes no event and does not bump updated_at."""
    _require_supervising(actor, "edit a work order")
    now = now or _now()
    wanted = {}
    if title is not None:
        wanted["title"] = _clean_title(title)
    if description is not _UNSET:
        wanted["description"] = description
    if priority is not None:
        wanted["priority"] = _check_priority(priority)
    if due_at is not _UNSET:
        wanted["due_at"] = _optional_timestamp(due_at, "due_at")
    with _LOCK:
        order = _row(conn, wo_id)
        if order["status"] not in ACTIVE_STATUSES:
            raise _invalid(order["status"], "edit")
        sets = {name: value for name, value in wanted.items() if order[name] != value}
        if not sets:
            return get_work_order(conn, wo_id), False
        _transition(conn, order, to_status=order["status"], event="edited", actor=actor,
                    now=now, sets=sets)
    return get_work_order(conn, wo_id), True


def assign(conn, wo_id: int, actor: dict, assigned_to: int, *, note=None, now=None) -> dict:
    """Assign or reassign. open -> assigned; assigned and in_progress keep
    their status (a reassignment event has from_status == to_status)."""
    _require_supervising(actor, "assign a work order")
    now = now or _now()
    _check_assignee(conn, assigned_to)
    with _LOCK:
        order = _row(conn, wo_id)
        if order["status"] not in ACTIVE_STATUSES:
            raise _invalid(order["status"], "assign")
        to_status = "assigned" if order["status"] == "open" else order["status"]
        _transition(conn, order, to_status=to_status, event="assigned", actor=actor, now=now,
                    sets={"assigned_to": assigned_to}, assigned_to=assigned_to, note=note)
    return get_work_order(conn, wo_id)


def start(conn, wo_id: int, actor: dict, *, now=None) -> dict:
    now = now or _now()
    with _LOCK:
        order = _row(conn, wo_id)
        if order["status"] != "assigned":
            raise _invalid(order["status"], "start")
        _require_assignee_or_supervising(actor, order, "start this work order")
        _transition(conn, order, to_status="in_progress", event="started", actor=actor, now=now,
                    sets={"started_at": now})
    return get_work_order(conn, wo_id)


def _resets_by_default(conn, order: dict, maintenance_type: str) -> bool:
    """Plan D2: completing a corrective order resets the machine's health by
    default only if the order's alert is a real alert of the machine's
    current episode that is open or was closed by a person. Free-standing
    orders, preventive work and orders for an older episode's alert do not.
    An open real alert with no episode (opened while the ratchet kill switch
    was on) counts as the current episode: a reset resolves every open real
    alert and an open alert never re-arms, so it is the current fault.
    Called under live._TRANSITION_LOCK."""
    if maintenance_type != "corrective" or order["alert_id"] is None:
        return False
    row = health_epoch.current(conn, order["machine_id"])
    if row is None:
        return False
    alert = conn.execute(
        "SELECT status, closed_by, source, health_episode FROM alerts WHERE id = ?",
        (order["alert_id"],),
    ).fetchone()
    if alert is None or alert["source"] == live.DEMO_SOURCE:
        return False
    if alert["status"] == "open":
        return alert["health_episode"] in (None, row.episode)
    return alert["health_episode"] == row.episode and alert["closed_by"] is not None


def _default_feedback(conn, order: dict, actor: dict, now: str) -> None:
    """Plan D11: a completion that reset the machine records the order's
    alert as 'maintenance_prevented' (editable later), unless someone already
    recorded an outcome. Does not commit."""
    if not table_exists(conn, "alert_feedback"):
        return
    if conn.execute("SELECT 1 FROM alert_feedback WHERE alert_id = ?",
                    (order["alert_id"],)).fetchone() is not None:
        return
    feedback._upsert(conn, order["alert_id"], actor,
                     {"outcome": "maintenance_prevented", "actual_cause": None,
                      "actual_failure_at": None, "notes": None},
                     order["id"], now)


def complete(conn, wo_id: int, actor: dict, *, notes=None, performed_at=None,
             maintenance_type: str = "corrective", reset_health=None, now=None) -> dict:
    """in_progress -> done, writing a maintenance_records row and linking it
    (decision 6).

    One transaction under _LOCK -> live._TRANSITION_LOCK: the record, the
    health reset it may carry (reset_health None means the plan-D2 default,
    see _resets_by_default), the default feedback (D11) and the transition
    commit together. If any of them fails nothing is written, the order stays
    in_progress and a retry is clean. The alerts the reset resolved are
    announced after the commit, outside both locks."""
    now = now or _now()
    if maintenance_type not in MAINTENANCE_TYPES:
        raise WorkOrderError(f"invalid maintenance_type: {maintenance_type}")
    performed_at = _parse_timestamp(performed_at, "performed_at") if performed_at else now
    with _LOCK:
        order = _row(conn, wo_id)
        if order["status"] != "in_progress":
            raise _invalid(order["status"], "complete")
        _require_assignee_or_supervising(actor, order, "complete this work order")
        description = f"Work order #{order['id']}: {order['title']}"
        if notes:
            description += f" — {notes}"
        with live._TRANSITION_LOCK:
            try:
                record, resolved = _log_maintenance_locked(
                    conn, order["machine_id"], performed_at, description,
                    order["assigned_to_name"] or actor.get("name"),
                    alert_id=order["alert_id"], type=maintenance_type,
                    reset_health=reset_health, now=now,
                    default_reset=_resets_by_default(conn, order, maintenance_type),
                )
                if record["resets_health"] and order["alert_id"] is not None:
                    _default_feedback(conn, order, actor, now)
                sets = {"completed_at": now, "maintenance_record_id": record["id"]}
                if notes is not None:
                    sets["notes"] = notes
                _transition(conn, order, to_status="done", event="completed", actor=actor,
                            now=now, sets=sets, note=notes, commit=False)
                conn.commit()
            except MaintenanceError as exc:
                conn.rollback()
                raise WorkOrderError(str(exc)) from None
            except Exception:
                conn.rollback()
                raise
    health_epoch.announce_resolved(conn, resolved, at=now)
    return get_work_order(conn, wo_id)


def cancel(conn, wo_id: int, actor: dict, *, reason=None, now=None) -> dict:
    _require_supervising(actor, "cancel a work order")
    now = now or _now()
    with _LOCK:
        order = _row(conn, wo_id)
        if order["status"] not in ACTIVE_STATUSES:
            raise _invalid(order["status"], "cancel")
        _transition(conn, order, to_status="cancelled", event="cancelled", actor=actor, now=now,
                    sets={"cancelled_at": now}, note=reason)
    return get_work_order(conn, wo_id)
