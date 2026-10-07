"""Deliberate repairs reset the machine's health epoch (plan
2026-10-07-ratchet-maintenance-reset, D1, D2, D11).

- A free-standing maintenance record resets only with an explicit
  reset_health=True (admin/supervisor over HTTP), and never when it is dated
  before the current fault.
- Completing a corrective work order linked to a real alert of the machine's
  current episode resets by default, writes the default feedback
  'maintenance_prevented' and is one transaction with the record.

m2 has no alert and no machine_health_state row in the conftest seed, so its
episode is 0. Demo users: admin (1), supervisor (2), operator (3).
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.alerts import live
from src.feedback import service as feedback
from src.maintenance import records
from src.maintenance.records import MaintenanceError
from src.prediction import health_epoch, pipeline
from src.work_orders import service
from src.work_orders.service import WorkOrderConflict

T = "2026-10-07T12:00:00+00:00"          # the alert opens
PERFORMED = "2026-10-07T12:30:00+00:00"  # the repair
NOW = "2026-10-07T13:00:00+00:00"        # wall clock of the write
SRC = "xjtu_rul"
SUP = {"id": 2, "role": "supervisor", "name": "Sam Supervisor"}


def _open_alert(conn, machine_id="m2", at=T, episode=0):
    """machine_id held critical in `episode` with an open real alert of it."""
    health_epoch.record_level(conn, machine_id, episode, "critical", "v1")
    conn.commit()
    _, alert = live.apply_reading(conn, machine_id, "critical", None, SRC, at,
                                  health_episode=episode)
    return alert


def _alert(conn, alert_id):
    return dict(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())


def _records(conn, machine_id="m2"):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM maintenance_records WHERE machine_id = ? ORDER BY id", (machine_id,))]


def _feedback(conn, alert_id):
    row = conn.execute("SELECT * FROM alert_feedback WHERE alert_id = ?", (alert_id,)).fetchone()
    return dict(row) if row is not None else None


def _epoch(conn, machine_id="m2"):
    row = health_epoch.current(conn, machine_id)
    return row.epoch, row.episode


def _in_progress_order(conn, alert_id=None, machine_id="m2"):
    if alert_id is None:
        wo = service.create(conn, SUP, machine_id=machine_id, title="t", assigned_to=SUP["id"],
                            now=T)
    else:
        wo, _ = service.create_from_alert(conn, alert_id, SUP, assigned_to=SUP["id"], now=T)
    service.start(conn, wo["id"], SUP, now=T)
    return wo["id"]


@pytest.fixture
def announced(monkeypatch, db_path):
    """Every fan_out call, with the alert's status as another connection
    sees it at that moment (so the broadcast is after the commit)."""
    calls = []

    def spy(conn, applied, *, at, **kwargs):
        other = sqlite3.connect(db_path)
        try:
            status = other.execute("SELECT status FROM alerts WHERE id = ?",
                                   (applied[1]["id"],)).fetchone()[0]
        finally:
            other.close()
        calls.append((applied[0], applied[1]["id"], status))
        return {"event": None, "alert": None, "emails_sent": 0}

    monkeypatch.setattr(pipeline, "fan_out", spy)
    return calls


# --- log_maintenance ------------------------------------------------------------------

def test_record_without_reset_health_does_not_reset(conn):
    alert = _open_alert(conn)
    rec = records.log_maintenance(conn, "m2", PERFORMED, "fixed", "t", alert_id=alert["id"],
                                  type="corrective", now=NOW)
    assert rec["resets_health"] is False
    assert _records(conn)[0]["resets_health"] == 0
    assert _epoch(conn) == (0, 0)
    assert _alert(conn, alert["id"])["status"] == "open"


def test_explicit_reset_resets_atomically_and_resolves_at_now(conn, db_path):
    alert = _open_alert(conn)
    rec = records.log_maintenance(conn, "m2", PERFORMED, "replaced", "t", alert_id=alert["id"],
                                  type="corrective", reset_health=True, now=NOW)

    assert rec["resets_health"] is True
    assert rec["performed_at"] == PERFORMED
    other = sqlite3.connect(db_path)
    other.row_factory = sqlite3.Row
    try:
        (row,) = other.execute("SELECT resets_health, performed_at FROM maintenance_records "
                               "WHERE machine_id = 'm2'").fetchall()
        assert (row["resets_health"], row["performed_at"]) == (1, PERFORMED)
        row = health_epoch.current(other, "m2")
        assert (row.epoch, row.episode, row.max_state) == (1, 1, "healthy")
        resolved = other.execute("SELECT status, resolved_at, closed_by FROM alerts WHERE id = ?",
                                 (alert["id"],)).fetchone()
        assert tuple(resolved) == ("resolved", NOW, None)
    finally:
        other.close()
    assert records.get_history(conn, "m2")[0]["resets_health"] == 1


def test_explicit_reset_before_the_current_epoch_is_refused(conn):
    health_epoch.reset_machine_health(conn, "m2", reason="test", now=NOW)
    with pytest.raises(MaintenanceError, match="dated before the current fault"):
        records.log_maintenance(conn, "m2", PERFORMED, reset_health=True,
                                now="2026-10-07T14:00:00+00:00")
    assert _records(conn) == []
    assert _epoch(conn) == (1, 1)


def test_explicit_reset_before_the_alert_opened_is_refused(conn):
    alert = _open_alert(conn)
    with pytest.raises(MaintenanceError, match="dated before the current fault"):
        records.log_maintenance(conn, "m2", "2026-10-07T11:00:00+00:00", alert_id=alert["id"],
                                type="corrective", reset_health=True, now=NOW)
    assert _records(conn) == []
    assert _epoch(conn) == (0, 0)
    assert _alert(conn, alert["id"])["status"] == "open"


def test_explicit_reset_in_the_future_is_refused(conn):
    with pytest.raises(MaintenanceError, match="future"):
        records.log_maintenance(conn, "m2", "2026-10-07T13:10:00+00:00", reset_health=True,
                                now=NOW)
    assert _records(conn) == []
    assert _epoch(conn) == (0, 0)


def test_failed_reset_writes_no_record(conn, monkeypatch):
    alert = _open_alert(conn)

    def boom(*args, **kwargs):
        raise RuntimeError("reset failed")

    monkeypatch.setattr(health_epoch, "_reset_locked", boom)
    with pytest.raises(RuntimeError):
        records.log_maintenance(conn, "m2", PERFORMED, alert_id=alert["id"], reset_health=True,
                                now=NOW)
    assert _records(conn) == []
    assert _epoch(conn) == (0, 0)


def test_log_maintenance_announces_after_commit(conn, announced):
    alert = _open_alert(conn)
    records.log_maintenance(conn, "m2", PERFORMED, alert_id=alert["id"], reset_health=True,
                            now=NOW)
    assert announced == [("alert_resolved", alert["id"], "resolved")]


# --- work-order completion ------------------------------------------------------------

def test_completing_the_current_alerts_order_resets_and_records_feedback(conn, announced):
    alert = _open_alert(conn)
    wo_id = _in_progress_order(conn, alert["id"])

    order = service.complete(conn, wo_id, SUP, performed_at=PERFORMED, now=NOW)

    assert order["status"] == "done"
    (rec,) = _records(conn)
    assert order["maintenance_record_id"] == rec["id"]
    assert rec["resets_health"] == 1
    assert _epoch(conn) == (1, 1)
    assert _alert(conn, alert["id"])["status"] == "resolved"
    assert _alert(conn, alert["id"])["resolved_at"] == NOW
    fb = _feedback(conn, alert["id"])
    assert (fb["outcome"], fb["work_order_id"], fb["recorded_by"]) == (
        "maintenance_prevented", wo_id, SUP["id"])
    assert announced == [("alert_resolved", alert["id"], "resolved")]


def test_free_standing_order_does_not_reset(conn):
    alert = _open_alert(conn)
    wo_id = _in_progress_order(conn)
    service.complete(conn, wo_id, SUP, performed_at=PERFORMED, now=NOW)
    assert _records(conn)[0]["resets_health"] == 0
    assert _epoch(conn) == (0, 0)
    assert _alert(conn, alert["id"])["status"] == "open"


def test_preventive_completion_does_not_reset(conn):
    alert = _open_alert(conn)
    wo_id = _in_progress_order(conn, alert["id"])
    service.complete(conn, wo_id, SUP, performed_at=PERFORMED, maintenance_type="preventive",
                     now=NOW)
    assert _epoch(conn) == (0, 0)
    assert _alert(conn, alert["id"])["status"] == "open"
    assert _feedback(conn, alert["id"]) is None


def test_order_for_a_resolved_alert_without_an_episode_does_not_reset(conn):
    # The conftest's alert 2 on m1 predates episodes (health_episode NULL);
    # once resolved by a person it is history, not the current fault.
    conn.execute("UPDATE alerts SET status = 'resolved', resolved_at = ?, closed_by = 2 "
                 "WHERE id = 2", (T,))
    conn.commit()
    wo_id = _in_progress_order(conn, 2)
    service.complete(conn, wo_id, SUP, performed_at=PERFORMED, now=NOW)
    assert _epoch(conn, "m1") == (0, 0)


def test_an_open_alert_from_the_kill_switch_period_resets_once_the_ratchet_is_back(conn):
    # Ratchet off: the pipeline stamps no episode. An open real alert is
    # always the machine's current fault (a reset resolves open alerts and an
    # open alert never re-arms), so its order resets by default once the
    # ratchet is on again and the machine latches.
    _, alert = live.apply_reading(conn, "m2", "critical", None, SRC, T)
    assert alert["health_episode"] is None
    wo_id = _in_progress_order(conn, alert["id"])
    health_epoch.record_level(conn, "m2", 0, "critical", "v1")
    conn.commit()

    service.complete(conn, wo_id, SUP, performed_at=PERFORMED, now=NOW)

    assert _epoch(conn) == (1, 1)
    assert _alert(conn, alert["id"])["status"] == "resolved"


def test_order_for_an_older_episode_alert_does_not_reset(conn):
    alert = _open_alert(conn)
    with live._TRANSITION_LOCK:
        health_epoch._rearm_locked(conn, "m2", now=T)
        conn.commit()
    wo_id = _in_progress_order(conn, alert["id"])
    service.complete(conn, wo_id, SUP, performed_at=PERFORMED, now=NOW)
    assert _epoch(conn) == (0, 1)
    assert _alert(conn, alert["id"])["status"] == "open"


def test_backdated_completion_with_the_default_does_not_reset(conn):
    alert = _open_alert(conn)
    wo_id = _in_progress_order(conn, alert["id"])
    order = service.complete(conn, wo_id, SUP, performed_at="2026-10-07T11:00:00+00:00", now=NOW)
    assert order["status"] == "done"
    assert _records(conn)[0]["resets_health"] == 0
    assert _epoch(conn) == (0, 0)
    assert _alert(conn, alert["id"])["status"] == "open"


def test_explicit_false_on_completion_does_not_reset(conn):
    alert = _open_alert(conn)
    wo_id = _in_progress_order(conn, alert["id"])
    service.complete(conn, wo_id, SUP, performed_at=PERFORMED, reset_health=False, now=NOW)
    assert _epoch(conn) == (0, 0)
    assert _alert(conn, alert["id"])["status"] == "open"


def test_failed_transition_undoes_the_record_reset_and_feedback(conn, monkeypatch, announced):
    alert = _open_alert(conn)
    wo_id = _in_progress_order(conn, alert["id"])
    real = service._transition

    def conflict(*args, **kwargs):
        raise WorkOrderConflict("changed concurrently")

    monkeypatch.setattr(service, "_transition", conflict)
    with pytest.raises(WorkOrderConflict):
        service.complete(conn, wo_id, SUP, performed_at=PERFORMED, now=NOW)

    assert _records(conn) == []
    assert _epoch(conn) == (0, 0)
    assert _feedback(conn, alert["id"]) is None
    assert _alert(conn, alert["id"])["status"] == "open"
    assert service.get_work_order(conn, wo_id)["status"] == "in_progress"
    assert announced == []

    monkeypatch.setattr(service, "_transition", real)
    assert service.complete(conn, wo_id, SUP, performed_at=PERFORMED, now=NOW)["status"] == "done"
    assert len(_records(conn)) == 1
    assert _epoch(conn) == (1, 1)
    assert announced == [("alert_resolved", alert["id"], "resolved")]


def test_existing_feedback_is_not_overwritten(conn):
    alert = _open_alert(conn)
    feedback.record_feedback(conn, alert["id"], SUP, outcome="confirmed_failure", now=T)
    wo_id = _in_progress_order(conn, alert["id"])
    service.complete(conn, wo_id, SUP, performed_at=PERFORMED, now=NOW)
    assert _epoch(conn) == (1, 1)
    fb = _feedback(conn, alert["id"])
    assert (fb["outcome"], fb["work_order_id"]) == ("confirmed_failure", None)


# --- HTTP -----------------------------------------------------------------------------

def _wall(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _maintenance(**extra):
    return {"machine_id": "m2", "performed_at": _wall(1), "type": "corrective", **extra}


def test_post_maintenance_defaults_to_no_reset(auth_client, conn):
    resp = auth_client("operator").post("/api/maintenance", json=_maintenance())
    assert resp.status_code == 201, resp.text
    assert resp.json()["resets_health"] is False
    assert _epoch(conn) == (0, 0)


def test_post_maintenance_reset_needs_a_supervising_role(auth_client, conn):
    alert = _open_alert(conn, at=_wall(60))
    body = _maintenance(alert_id=alert["id"], reset_health=True)

    resp = auth_client("operator").post("/api/maintenance", json=body)
    assert resp.status_code == 403
    assert _records(conn) == []

    resp = auth_client("supervisor").post("/api/maintenance", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["resets_health"] is True
    assert _epoch(conn) == (1, 1)
    assert _alert(conn, alert["id"])["status"] == "resolved"


def test_complete_route_passes_reset_health(auth_client, conn):
    alert = _open_alert(conn, at=_wall(60))
    sup = auth_client("supervisor")
    wo = sup.post(f"/api/alerts/{alert['id']}/work-order",
                  json={"assigned_to": SUP["id"]}).json()
    assert sup.post(f"/api/work-orders/{wo['id']}/start").status_code == 200
    resp = sup.post(f"/api/work-orders/{wo['id']}/complete", json={"reset_health": False})
    assert resp.status_code == 200, resp.text
    assert _epoch(conn) == (0, 0)
    assert _alert(conn, alert["id"])["status"] == "open"
