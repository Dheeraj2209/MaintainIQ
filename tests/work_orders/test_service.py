"""src/work_orders/service.py: the work-order state machine, its RBAC and the
alert acknowledgement / maintenance-record side effects
(design/2026-10-07-work-orders-escalation-design.md, decisions 2-6, 13).

Uses the seeded conftest DB: alert 1 is resolved/low on m1, alert 2 is
open/high on m1 ("m1 critical"), and the three demo users are admin (1),
supervisor (2) and operator (3). Fresh alerts are opened on m2.
"""
import pytest

from src.maintenance.records import MaintenanceError
from src.work_orders import service
from src.work_orders.service import (
    WorkOrderConflict,
    WorkOrderError,
    WorkOrderForbidden,
    WorkOrderNotFound,
)

NOW = "2026-10-07T12:00:00+00:00"
LATER = "2026-10-07T13:00:00+00:00"


def actor(conn, role):
    row = conn.execute("SELECT id, email, name, role FROM users WHERE role = ?", (role,)).fetchone()
    return dict(row)


def _alert(conn, alert_id):
    return dict(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())


def _events(conn, wo_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM work_order_events WHERE work_order_id = ? ORDER BY id", (wo_id,))]


def _fresh_alert(conn, machine_id="m2", *, acknowledged_by=None):
    cur = conn.execute(
        """INSERT INTO alerts (machine_id, opened_at, severity, health_state, probable_cause,
                               message, status, source, created_at, acknowledged_at, acknowledged_by)
           VALUES (?, ?, 'medium', 'faulty', 'imbalance', ?, 'open', 'ml', ?, ?, ?)""",
        (machine_id, NOW, f"{machine_id} faulty", NOW,
         NOW if acknowledged_by else None, acknowledged_by),
    )
    conn.commit()
    return cur.lastrowid


# --- create_from_alert ---------------------------------------------------------------

def test_create_from_alert_opens_order_and_acknowledges(conn):
    op = actor(conn, "operator")
    wo, acked = service.create_from_alert(conn, 2, op, now=NOW)

    assert wo["status"] == "open"
    assert wo["priority"] == "high"
    assert wo["title"] == "m1 critical"
    assert wo["alert_id"] == 2 and wo["machine_id"] == "m1"
    assert wo["created_by"] == op["id"]
    assert wo["created_by_name"] == "Otis Operator"
    events = _events(conn, wo["id"])
    assert [e["event"] for e in events] == ["created"]
    assert events[0]["note"] == "from alert #2"
    assert events[0]["to_status"] == "open"

    alert = _alert(conn, 2)
    assert alert["acknowledged_at"] == NOW
    assert alert["acknowledged_by"] == op["id"]
    assert acked is not None and acked["acknowledged_at"] == NOW


def test_create_from_alert_keeps_existing_acknowledgement(conn):
    conn.execute("UPDATE alerts SET acknowledged_at = 'earlier', acknowledged_by = 1 WHERE id = 2")
    conn.commit()
    wo, acked = service.create_from_alert(conn, 2, actor(conn, "operator"), now=NOW)
    assert acked is None
    alert = _alert(conn, 2)
    assert (alert["acknowledged_at"], alert["acknowledged_by"]) == ("earlier", 1)
    assert wo["status"] == "open"


def test_duplicate_active_order_conflicts_without_touching_ack(conn):
    sup = actor(conn, "supervisor")
    alert_id = _fresh_alert(conn)
    first, _ = service.create_from_alert(conn, alert_id, sup, now=NOW)
    # Clear the ack to prove the failed second call does not re-acknowledge.
    conn.execute("UPDATE alerts SET acknowledged_at = NULL, acknowledged_by = NULL WHERE id = ?",
                 (alert_id,))
    conn.commit()

    with pytest.raises(WorkOrderConflict, match=f"#{first['id']}"):
        service.create_from_alert(conn, alert_id, actor(conn, "operator"), now=LATER)
    alert = _alert(conn, alert_id)
    assert alert["acknowledged_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM work_orders WHERE alert_id = ?",
                        (alert_id,)).fetchone()[0] == 1


def test_new_order_allowed_after_cancel(conn):
    sup = actor(conn, "supervisor")
    first, _ = service.create_from_alert(conn, 2, sup, now=NOW)
    service.cancel(conn, first["id"], sup, reason="dup", now=NOW)
    second, _ = service.create_from_alert(conn, 2, sup, now=LATER)
    assert second["id"] != first["id"]


def test_create_from_resolved_alert_and_unknown_alert(conn):
    wo, _ = service.create_from_alert(conn, 1, actor(conn, "operator"), now=NOW)
    assert wo["alert_id"] == 1 and wo["priority"] == "low"
    with pytest.raises(WorkOrderNotFound):
        service.create_from_alert(conn, 9999, actor(conn, "operator"), now=NOW)


def test_default_title_without_message(conn):
    alert_id = _fresh_alert(conn)
    conn.execute("UPDATE alerts SET message = NULL WHERE id = ?", (alert_id,))
    conn.commit()
    wo, _ = service.create_from_alert(conn, alert_id, actor(conn, "operator"), now=NOW)
    assert wo["title"] == "m2: faulty alert"
    assert wo["priority"] == "medium"


def test_assigned_to_on_create_needs_supervising_role(conn):
    op = actor(conn, "operator")
    with pytest.raises(WorkOrderForbidden):
        service.create_from_alert(conn, 2, op, assigned_to=op["id"], now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0] == 0

    wo, _ = service.create_from_alert(conn, 2, actor(conn, "supervisor"), assigned_to=op["id"], now=NOW)
    assert wo["status"] == "assigned"
    assert wo["assigned_to"] == op["id"] and wo["assigned_to_name"] == "Otis Operator"
    assert [e["event"] for e in _events(conn, wo["id"])] == ["created", "assigned"]


# --- free-standing create --------------------------------------------------------------

def test_free_standing_create_rbac_and_validation(conn):
    sup = actor(conn, "supervisor")
    with pytest.raises(WorkOrderForbidden):
        service.create(conn, actor(conn, "operator"), machine_id="m2", title="Grease", now=NOW)
    with pytest.raises(WorkOrderError, match="unknown machine_id"):
        service.create(conn, sup, machine_id="ghost", title="Grease", now=NOW)
    with pytest.raises(WorkOrderError, match="unknown or inactive user"):
        service.create(conn, sup, machine_id="m2", title="Grease", assigned_to=999, now=NOW)
    conn.execute("UPDATE users SET is_active = 0 WHERE role = 'operator'")
    conn.commit()
    with pytest.raises(WorkOrderError, match="unknown or inactive user"):
        service.create(conn, sup, machine_id="m2", title="Grease", assigned_to=3, now=NOW)
    with pytest.raises(WorkOrderError):
        service.create(conn, sup, machine_id="m2", title="   ", now=NOW)
    with pytest.raises(WorkOrderError):
        service.create(conn, sup, machine_id="m2", title="x" * 201, now=NOW)
    with pytest.raises(WorkOrderError, match="due_at"):
        service.create(conn, sup, machine_id="m2", title="Grease", due_at="tomorrow", now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0] == 0

    wo = service.create(conn, sup, machine_id="m2", title=" Grease ", due_at="2026-10-09T08:00:00Z",
                        now=NOW)
    assert wo["alert_id"] is None and wo["title"] == "Grease" and wo["priority"] == "medium"
    assert wo["due_at"] == "2026-10-09T08:00:00+00:00"
    assert _events(conn, wo["id"])[0]["note"] is None


# --- lifecycle ----------------------------------------------------------------------------

def test_full_happy_path_writes_maintenance_record(conn):
    sup, op = actor(conn, "supervisor"), actor(conn, "operator")
    wo, _ = service.create_from_alert(conn, 2, sup, now=NOW)
    wo = service.assign(conn, wo["id"], sup, op["id"], note="you're up", now=NOW)
    assert wo["status"] == "assigned"
    wo = service.start(conn, wo["id"], op, now=NOW)
    assert wo["status"] == "in_progress" and wo["started_at"] == NOW
    wo = service.complete(conn, wo["id"], op, notes="replaced bearing", now=LATER)

    assert wo["status"] == "done" and wo["completed_at"] == LATER
    assert wo["notes"] == "replaced bearing"
    events = _events(conn, wo["id"])
    assert [(e["event"], e["from_status"], e["to_status"]) for e in events] == [
        ("created", None, "open"),
        ("assigned", "open", "assigned"),
        ("started", "assigned", "in_progress"),
        ("completed", "in_progress", "done"),
    ]
    assert events[1]["assigned_to"] == op["id"] and events[1]["note"] == "you're up"

    rec = dict(conn.execute("SELECT * FROM maintenance_records WHERE id = ?",
                            (wo["maintenance_record_id"],)).fetchone())
    assert rec["type"] == "corrective"
    assert rec["alert_id"] == 2
    assert rec["technician"] == "Otis Operator"
    assert rec["description"].startswith(f"Work order #{wo['id']}: m1 critical")
    assert "replaced bearing" in rec["description"]
    assert rec["performed_at"] == LATER


def test_complete_preventive_with_explicit_performed_at(conn):
    adm = actor(conn, "admin")
    wo = service.create(conn, adm, machine_id="m2", title="Grease", assigned_to=adm["id"], now=NOW)
    service.start(conn, wo["id"], adm, now=NOW)
    wo = service.complete(conn, wo["id"], adm, performed_at="2026-10-07T09:30:00Z",
                          maintenance_type="preventive", now=LATER)
    rec = conn.execute("SELECT * FROM maintenance_records WHERE id = ?",
                       (wo["maintenance_record_id"],)).fetchone()
    assert rec["type"] == "preventive"
    assert rec["performed_at"] == "2026-10-07T09:30:00+00:00"
    assert rec["alert_id"] is None
    assert rec["description"] == f"Work order #{wo['id']}: Grease"


def test_technician_falls_back_to_completer_when_unassigned_name_missing(conn):
    sup = actor(conn, "supervisor")
    wo = service.create(conn, sup, machine_id="m2", title="Grease", assigned_to=3, now=NOW)
    service.start(conn, wo["id"], sup, now=NOW)
    conn.execute("DELETE FROM users WHERE id = 3")
    conn.commit()
    wo = service.complete(conn, wo["id"], sup, now=LATER)
    rec = conn.execute("SELECT technician FROM maintenance_records WHERE id = ?",
                       (wo["maintenance_record_id"],)).fetchone()
    assert rec["technician"] == "Sam Supervisor"


def test_invalid_edges_conflict(conn):
    sup = actor(conn, "supervisor")
    wo = service.create(conn, sup, machine_id="m2", title="t", now=NOW)
    with pytest.raises(WorkOrderConflict, match="open -> start"):
        service.start(conn, wo["id"], sup, now=NOW)
    service.assign(conn, wo["id"], sup, sup["id"], now=NOW)
    with pytest.raises(WorkOrderConflict, match="assigned -> complete"):
        service.complete(conn, wo["id"], sup, now=NOW)
    service.start(conn, wo["id"], sup, now=NOW)
    service.complete(conn, wo["id"], sup, now=NOW)
    for action in (
        lambda: service.start(conn, wo["id"], sup, now=NOW),
        lambda: service.complete(conn, wo["id"], sup, now=NOW),
        lambda: service.assign(conn, wo["id"], sup, sup["id"], now=NOW),
        lambda: service.cancel(conn, wo["id"], sup, now=NOW),
        lambda: service.edit(conn, wo["id"], sup, title="new", now=NOW),
    ):
        with pytest.raises(WorkOrderConflict):
            action()

    other = service.create(conn, sup, machine_id="m2", title="t", now=NOW)
    service.cancel(conn, other["id"], sup, reason="not needed", now=NOW)
    with pytest.raises(WorkOrderConflict):
        service.cancel(conn, other["id"], sup, now=NOW)
    with pytest.raises(WorkOrderConflict):
        service.edit(conn, other["id"], sup, priority="high", now=NOW)
    cancelled = service.get_work_order(conn, other["id"], with_events=True)
    assert cancelled["status"] == "cancelled" and cancelled["cancelled_at"] == NOW
    assert cancelled["events"][-1]["note"] == "not needed"


def test_noop_edit_writes_no_event(conn):
    sup = actor(conn, "supervisor")
    wo = service.create(conn, sup, machine_id="m2", title="t", description="d",
                        priority="high", now=NOW)
    for fields in ({}, {"title": "t", "priority": "high", "description": "d"}):
        same, changed = service.edit(conn, wo["id"], sup, now=LATER, **fields)
        assert not changed
        assert same["updated_at"] == NOW
    assert [e["event"] for e in _events(conn, wo["id"])] == ["created"]


def test_edit_null_clears_description_and_due_at(conn):
    sup = actor(conn, "supervisor")
    wo = service.create(conn, sup, machine_id="m2", title="t", description="d",
                        due_at="2026-10-08T00:00:00+00:00", now=NOW)
    edited, changed = service.edit(conn, wo["id"], sup, description=None, due_at=None, now=LATER)
    assert changed
    assert edited["description"] is None and edited["due_at"] is None
    # title / priority null mean "not given" (both are NOT NULL columns)
    kept, changed = service.edit(conn, wo["id"], sup, title=None, priority=None, now=LATER)
    assert not changed and kept["title"] == "t"


def test_rbac(conn):
    adm, sup, op = actor(conn, "admin"), actor(conn, "supervisor"), actor(conn, "operator")
    wo = service.create(conn, sup, machine_id="m2", title="t", assigned_to=sup["id"], now=NOW)
    with pytest.raises(WorkOrderForbidden):
        service.start(conn, wo["id"], op, now=NOW)
    service.start(conn, wo["id"], adm, now=NOW)  # admin, not the assignee: allowed
    with pytest.raises(WorkOrderForbidden):
        service.complete(conn, wo["id"], op, now=NOW)
    with pytest.raises(WorkOrderForbidden):
        service.assign(conn, wo["id"], op, op["id"], now=NOW)
    with pytest.raises(WorkOrderForbidden):
        service.cancel(conn, wo["id"], op, now=NOW)
    with pytest.raises(WorkOrderForbidden):
        service.edit(conn, wo["id"], op, title="x", now=NOW)
    service.complete(conn, wo["id"], adm, now=NOW)


def test_unknown_order_not_found(conn):
    sup = actor(conn, "supervisor")
    with pytest.raises(WorkOrderNotFound):
        service.get_work_order(conn, 999)
    with pytest.raises(WorkOrderNotFound):
        service.start(conn, 999, sup, now=NOW)


def test_reassign_in_progress_keeps_status(conn):
    sup, op = actor(conn, "supervisor"), actor(conn, "operator")
    wo = service.create(conn, sup, machine_id="m2", title="t", assigned_to=sup["id"], now=NOW)
    service.start(conn, wo["id"], sup, now=NOW)
    wo = service.assign(conn, wo["id"], sup, op["id"], now=LATER)
    assert wo["status"] == "in_progress" and wo["assigned_to"] == op["id"]
    last = _events(conn, wo["id"])[-1]
    assert last["event"] == "assigned"
    assert last["from_status"] == last["to_status"] == "in_progress"
    assert last["assigned_to"] == op["id"]


def test_edit_updates_fields_and_logs_event(conn):
    sup = actor(conn, "supervisor")
    wo = service.create(conn, sup, machine_id="m2", title="t", now=NOW)
    wo, changed = service.edit(conn, wo["id"], sup, title="new", priority="high", description="d",
                              due_at="2026-10-08T00:00:00Z", now=LATER)
    assert changed
    assert (wo["title"], wo["priority"], wo["description"]) == ("new", "high", "d")
    assert wo["due_at"] == "2026-10-08T00:00:00+00:00"
    assert wo["updated_at"] == LATER
    assert _events(conn, wo["id"])[-1]["event"] == "edited"
    with pytest.raises(WorkOrderError):
        service.edit(conn, wo["id"], sup, priority="urgent", now=LATER)


def test_list_filters_and_ordering(conn):
    sup, op = actor(conn, "supervisor"), actor(conn, "operator")
    a, _ = service.create_from_alert(conn, 2, sup, now="2026-10-07T10:00:00+00:00")
    b = service.create(conn, sup, machine_id="m2", title="b", assigned_to=op["id"],
                       now="2026-10-07T11:00:00+00:00")
    c = service.create(conn, sup, machine_id="m2", title="c", now="2026-10-07T12:00:00+00:00")
    service.cancel(conn, c["id"], sup, now="2026-10-07T12:30:00+00:00")

    ids = lambda rows: [r["id"] for r in rows]
    assert ids(service.list_work_orders(conn)) == [c["id"], b["id"], a["id"]]
    assert ids(service.list_work_orders(conn, status="active")) == [b["id"], a["id"]]
    assert ids(service.list_work_orders(conn, status="cancelled")) == [c["id"]]
    assert ids(service.list_work_orders(conn, assigned_to=op["id"])) == [b["id"]]
    assert ids(service.list_work_orders(conn, machine_id="m1")) == [a["id"]]
    assert ids(service.list_work_orders(conn, alert_id=2)) == [a["id"]]
    assert ids(service.list_work_orders(conn, limit=1)) == [c["id"]]
    row = service.list_work_orders(conn, assigned_to=op["id"])[0]
    assert row["assigned_to_name"] == "Otis Operator"
    assert row["created_by_name"] == "Sam Supervisor"
    with pytest.raises(WorkOrderError, match="invalid status"):
        service.list_work_orders(conn, status="bogus")


def test_get_with_events_includes_linked_alert(conn):
    wo, _ = service.create_from_alert(conn, 2, actor(conn, "operator"), now=NOW)
    detail = service.get_work_order(conn, wo["id"], with_events=True)
    assert [e["event"] for e in detail["events"]] == ["created"]
    assert detail["events"][0]["user_name"] == "Otis Operator"
    assert detail["alert"]["id"] == 2
    assert detail["alert"]["page_level"] == 0
    assert detail["alert"]["active_work_order_id"] == wo["id"]


def test_list_assignees_active_only_without_email(conn):
    conn.execute("UPDATE users SET is_active = 0 WHERE role = 'operator'")
    conn.commit()
    rows = service.list_assignees(conn)
    assert rows == [
        {"id": 1, "name": "Ava Admin", "role": "admin"},
        {"id": 2, "name": "Sam Supervisor", "role": "supervisor"},
    ]


def test_failed_maintenance_write_keeps_order_in_progress(conn, monkeypatch):
    sup = actor(conn, "supervisor")
    wo = service.create(conn, sup, machine_id="m2", title="t", assigned_to=sup["id"], now=NOW)
    service.start(conn, wo["id"], sup, now=NOW)

    def boom(*args, **kwargs):
        raise MaintenanceError("bad")

    monkeypatch.setattr(service, "log_maintenance", boom)
    with pytest.raises(WorkOrderError, match="bad"):
        service.complete(conn, wo["id"], sup, now=LATER)
    assert service.get_work_order(conn, wo["id"])["status"] == "in_progress"
    assert [e["event"] for e in _events(conn, wo["id"])][-1] == "started"


def test_complete_rejects_bad_performed_at(conn):
    sup = actor(conn, "supervisor")
    wo = service.create(conn, sup, machine_id="m2", title="t", assigned_to=sup["id"], now=NOW)
    service.start(conn, wo["id"], sup, now=NOW)
    with pytest.raises(WorkOrderError):
        service.complete(conn, wo["id"], sup, performed_at="yesterday", now=LATER)
    assert service.get_work_order(conn, wo["id"])["status"] == "in_progress"
    assert conn.execute("SELECT COUNT(*) FROM maintenance_records").fetchone()[0] == 0
