"""src/feedback/service.py: closing alerts and recording what actually happened
(design/2026-10-07-prediction-feedback-design.md, decisions 4-7).

Seed: alert 1 is resolved/low and alert 2 open/high, both on m1. Demo users
are admin (1), supervisor (2), operator (3); a second operator (4) is added
here for the permission rules.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.alerts import live
from src.alerts.paging import AlertPager, PagingPolicy
from src.feedback import service
from src.feedback.service import FeedbackError, FeedbackForbidden, FeedbackNotFound

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
NOW = T0.isoformat()

ADMIN = {"id": 1, "role": "admin", "name": "Ava Admin"}
SUPERVISOR = {"id": 2, "role": "supervisor", "name": "Sam Supervisor"}
OPERATOR = {"id": 3, "role": "operator", "name": "Otis Operator"}
OTHER_OPERATOR = {"id": 4, "role": "operator", "name": "Olga Operator"}


@pytest.fixture
def conn(conn):
    conn.execute(
        "INSERT INTO users (id, email, name, hashed_password, role, is_active, created_at) "
        "VALUES (4, 'olga@maintainiq.local', 'Olga Operator', 'x', 'operator', 1, ?)", (NOW,))
    conn.execute("UPDATE alerts SET created_at = ? WHERE id = 2", (NOW,))
    conn.commit()
    return conn


def _alert(conn, alert_id):
    return dict(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())


def _feedback_rows(conn, alert_id):
    return [dict(r) for r in conn.execute("SELECT * FROM alert_feedback WHERE alert_id = ?", (alert_id,))]


def _work_order(conn, *, machine_id="m1", alert_id=None):
    cur = conn.execute(
        "INSERT INTO work_orders (alert_id, machine_id, status, priority, title, created_at, updated_at) "
        "VALUES (?, ?, 'done', 'high', 't', ?, ?)", (alert_id, machine_id, NOW, NOW))
    conn.commit()
    return cur.lastrowid


# --- close_alert -----------------------------------------------------------------------

def test_close_open_alert_resolves_acknowledges_and_records(conn):
    alert, feedback, closed = service.close_alert(
        conn, 2, OPERATOR, outcome="maintenance_prevented", actual_cause="bearing_wear",
        notes="replaced bearing", now=NOW)

    assert closed is True
    row = _alert(conn, 2)
    assert (row["status"], row["resolved_at"], row["closed_by"]) == ("resolved", NOW, 3)
    assert (row["acknowledged_at"], row["acknowledged_by"]) == (NOW, 3)
    assert alert["status"] == "resolved" and alert["closed_by"] == 3
    rows = _feedback_rows(conn, 2)
    assert len(rows) == 1 and rows[0]["recorded_by"] == 3 and rows[0]["recorded_at"] == NOW
    assert feedback["outcome"] == "maintenance_prevented"
    assert feedback["actual_cause"] == "bearing_wear"
    assert feedback["notes"] == "replaced bearing"
    assert feedback["recorded_by_name"] == "Otis Operator"
    assert feedback["updated_by"] is None


def test_close_keeps_an_existing_acknowledgement(conn):
    live.acknowledge_alert(conn, 2, 2, now="2026-10-07T11:00:00+00:00")
    service.close_alert(conn, 2, OPERATOR, outcome="false_alarm", now=NOW)
    row = _alert(conn, 2)
    assert (row["acknowledged_at"], row["acknowledged_by"]) == ("2026-10-07T11:00:00+00:00", 2)


def test_close_already_resolved_alert_only_records_feedback(conn):
    before = _alert(conn, 1)
    alert, feedback, closed = service.close_alert(conn, 1, OPERATOR, outcome="false_alarm", now=NOW)
    assert closed is False
    after = _alert(conn, 1)
    assert (after["status"], after["resolved_at"], after["closed_by"]) == (
        "resolved", before["resolved_at"], None)
    assert feedback["outcome"] == "false_alarm"
    assert len(_feedback_rows(conn, 1)) == 1


def test_abnormal_reading_after_close_opens_a_fresh_alert(conn):
    service.close_alert(conn, 2, OPERATOR, outcome="unknown", now=NOW)
    event, alert = live.apply_reading(conn, "m1", "critical", "bearing_wear", "xjtu_rul",
                                      "2026-10-07T12:01:00+00:00")
    assert event == "alert_created"
    assert alert["id"] != 2 and alert["status"] == "open"
    assert _alert(conn, 2)["status"] == "resolved"


def test_close_stops_paging(conn):
    service.close_alert(conn, 2, OPERATOR, outcome="false_alarm", now=NOW)
    sent = []
    pager = AlertPager(policy=PagingPolicy(),
                       notify=lambda c, a, *, roles, page_level: sent.append(a["id"]) or 1)
    assert pager.tick(conn, T0 + timedelta(minutes=60)) == []
    assert sent == []


def test_close_refused_by_permissions_leaves_the_alert_open(conn):
    service.record_feedback(conn, 2, SUPERVISOR, outcome="unknown", now=NOW)
    with pytest.raises(FeedbackForbidden):
        service.close_alert(conn, 2, OTHER_OPERATOR, outcome="false_alarm", now=NOW)
    row = _alert(conn, 2)
    assert row["status"] == "open" and row["closed_by"] is None and row["acknowledged_at"] is None
    assert _feedback_rows(conn, 2)[0]["outcome"] == "unknown"


# --- record_feedback -----------------------------------------------------------------------

def test_record_feedback_leaves_status_and_supports_edits(conn):
    alert, feedback, created = service.record_feedback(conn, 2, OPERATOR, outcome="unknown", now=NOW)
    assert created is True and alert["status"] == "open"
    assert _alert(conn, 2)["status"] == "open"

    later = (T0 + timedelta(minutes=5)).isoformat()
    _, edited, created = service.record_feedback(
        conn, 2, OPERATOR, outcome="false_alarm", actual_cause="other", now=later)
    assert created is False
    assert edited["outcome"] == "false_alarm" and edited["actual_cause"] == "other"
    assert (edited["recorded_by"], edited["recorded_at"]) == (3, NOW)
    assert (edited["updated_by"], edited["updated_at"]) == (3, later)
    assert edited["id"] == feedback["id"]


def test_put_is_a_full_replacement(conn):
    service.record_feedback(conn, 1, OPERATOR, outcome="false_alarm",
                            actual_cause="imbalance", notes="n", now=NOW)
    _, edited, _ = service.record_feedback(conn, 1, OPERATOR, outcome="unknown", now=NOW)
    assert edited["actual_cause"] is None and edited["notes"] is None


def test_only_recorder_admin_or_supervisor_may_replace(conn):
    service.record_feedback(conn, 2, OPERATOR, outcome="unknown", now=NOW)
    with pytest.raises(FeedbackForbidden, match="only the recorder, an admin or a supervisor"):
        service.record_feedback(conn, 2, OTHER_OPERATOR, outcome="false_alarm", now=NOW)
    _, fb, _ = service.record_feedback(conn, 2, SUPERVISOR, outcome="false_alarm", now=NOW)
    assert fb["updated_by"] == 2 and fb["recorded_by"] == 3
    _, fb, _ = service.record_feedback(conn, 2, ADMIN, outcome="maintenance_prevented", now=NOW)
    assert fb["updated_by"] == 1 and fb["outcome"] == "maintenance_prevented"


# --- validation ----------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs,message", [
    ({"outcome": "false_alarm", "actual_failure_at": "2026-10-07T11:00:00Z"},
     "actual_failure_at only applies to outcome confirmed_failure"),
    ({"outcome": "confirmed_failure", "actual_failure_at": "yesterday"},
     "actual_failure_at is not a valid ISO-8601 timestamp: 'yesterday'"),
    ({"outcome": "confirmed_failure", "actual_failure_at": "2026-10-07T12:10:00Z"},
     "actual_failure_at is in the future"),
    ({"outcome": "bogus"}, "invalid outcome"),
    ({"outcome": "false_alarm", "actual_cause": "gremlins"}, "invalid actual_cause"),
    ({"outcome": "false_alarm", "work_order_id": 999}, "unknown work order: 999"),
])
def test_validation_errors(conn, kwargs, message):
    with pytest.raises(FeedbackError, match=message):
        service.record_feedback(conn, 2, OPERATOR, now=NOW, **kwargs)
    assert _feedback_rows(conn, 2) == []


def test_failure_time_within_the_skew_is_accepted(conn):
    _, fb, _ = service.record_feedback(conn, 2, OPERATOR, outcome="confirmed_failure",
                                       actual_failure_at="2026-10-07T12:05:00Z", now=NOW)
    assert fb["actual_failure_at"] == "2026-10-07T12:05:00+00:00"


def test_naive_failure_time_is_treated_as_utc(conn):
    _, fb, _ = service.record_feedback(conn, 2, OPERATOR, outcome="confirmed_failure",
                                       actual_failure_at="2026-10-07T09:30:00", now=NOW)
    assert fb["actual_failure_at"] == "2026-10-07T09:30:00+00:00"


def test_work_order_must_match_machine_and_alert(conn):
    other_machine = _work_order(conn, machine_id="m2")
    with pytest.raises(FeedbackError, match=f"work order {other_machine} is for machine m2, not m1"):
        service.record_feedback(conn, 2, OPERATOR, outcome="false_alarm",
                                work_order_id=other_machine, now=NOW)
    other_alert = _work_order(conn, alert_id=1)
    with pytest.raises(FeedbackError, match=f"work order {other_alert} belongs to alert 1"):
        service.record_feedback(conn, 2, OPERATOR, outcome="false_alarm",
                                work_order_id=other_alert, now=NOW)
    mine = _work_order(conn, alert_id=2)
    free = _work_order(conn)
    _, fb, _ = service.record_feedback(conn, 2, OPERATOR, outcome="false_alarm", work_order_id=mine, now=NOW)
    assert fb["work_order_id"] == mine
    _, fb, _ = service.record_feedback(conn, 2, OPERATOR, outcome="false_alarm", work_order_id=free, now=NOW)
    assert fb["work_order_id"] == free


def test_unknown_alert_raises_not_found(conn):
    with pytest.raises(FeedbackNotFound, match="unknown alert: 99"):
        service.close_alert(conn, 99, OPERATOR, outcome="unknown", now=NOW)
    with pytest.raises(FeedbackNotFound, match="unknown alert: 99"):
        service.record_feedback(conn, 99, OPERATOR, outcome="unknown", now=NOW)
    with pytest.raises(FeedbackNotFound, match="unknown alert: 99"):
        service.get_feedback(conn, 99)


def test_get_feedback(conn):
    assert service.get_feedback(conn, 1) is None
    service.record_feedback(conn, 1, OPERATOR, outcome="false_alarm", now=NOW)
    assert service.get_feedback(conn, 1)["outcome"] == "false_alarm"


# --- concurrency -------------------------------------------------------------------------------

def _thread_conn(db_path):
    import sqlite3

    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def test_close_waits_for_the_transition_lock(conn, db_path):
    done = threading.Event()
    other = _thread_conn(db_path)

    def worker():
        service.close_alert(other, 2, OPERATOR, outcome="false_alarm", now=NOW)
        done.set()

    with live._TRANSITION_LOCK:
        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.2)
        assert not done.is_set()
    t.join(5)
    assert done.is_set()
    other.close()


def test_racing_closes_close_once(conn, db_path):
    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker(actor):
        c = _thread_conn(db_path)
        try:
            barrier.wait()
            results.append(service.close_alert(c, 2, actor, outcome="false_alarm", now=NOW)[2])
        except Exception as exc:  # surfaced below
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(a,)) for a in (ADMIN, SUPERVISOR)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == []
    assert sorted(results) == [False, True]
    assert len(_feedback_rows(conn, 2)) == 1
    assert _alert(conn, 2)["closed_by"] in (1, 2)
