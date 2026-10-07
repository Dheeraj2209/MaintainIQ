"""False-alarm re-arm (plan 2026-10-07-ratchet-maintenance-reset, D12): a close
or a recorded outcome that says the held alert was false (outcome false_alarm,
or cause sensor_or_data_quality_issue) on a real alert of the machine's current
episode bumps the episode and drops the held level to healthy, keeping the
epoch. Other outcomes keep the held level and the episode suppression.

m2 has no alert and no machine_health_state row in the conftest seed, so its
episode is 0. Demo users: admin (1), supervisor (2), operator (3).
"""
import pytest

from src.alerts import live
from src.feedback import service
from src.prediction import health_epoch, pipeline

T = "2026-10-07T12:00:00+00:00"
SRC = "xjtu_rul"
OPERATOR = {"id": 3, "role": "operator", "name": "Otis Operator"}


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "notify_alert", lambda conn, alert: sent.append(alert) or 1)
    return sent


def _held_critical_alert(conn, source=SRC):
    """m2 held critical in episode 0 with an open alert of that episode."""
    health_epoch.record_level(conn, "m2", 0, "critical", "v1")
    conn.commit()
    _, alert = live.apply_reading(conn, "m2", "critical", None, source, T, health_episode=0)
    return alert


def _row(conn):
    return health_epoch.current(conn, "m2")


def _result(episode, state="critical"):
    return {"machine_id": "m2", "health_state": state, "model_version": "v1", "warnings": [],
            "health_ratchet": True, "health_episode": episode}


def _alert_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM alerts WHERE machine_id = 'm2' ORDER BY id")]


def test_false_alarm_close_rearms_and_the_next_critical_pages_once(conn, emails):
    alert = _held_critical_alert(conn)

    _, _, closed = service.close_alert(conn, alert["id"], OPERATOR, outcome="false_alarm", now=T)

    assert closed is True
    row = _row(conn)
    assert (row.epoch, row.episode, row.max_state) == (0, 1, "healthy")
    state = health_epoch.effective_state(conn, "m2", {"health_state": "critical", "health_episode": 0})
    assert state["health_state"] == "healthy" and state["reset_pending_reading"] is True

    out = pipeline.handle_prediction(conn, _result(1), source=SRC, broadcast=lambda e: None)
    assert out["event"] == "alert_created" and out["alert"]["health_episode"] == 1
    assert len(emails) == 1
    for _ in range(3):
        pipeline.handle_prediction(conn, _result(1), source=SRC, broadcast=lambda e: None)
    assert len(emails) == 1
    assert len(_alert_rows(conn)) == 2


def test_sensor_cause_with_unknown_outcome_rearms(conn):
    alert = _held_critical_alert(conn)
    service.close_alert(conn, alert["id"], OPERATOR, outcome="unknown",
                        actual_cause="sensor_or_data_quality_issue", now=T)
    row = _row(conn)
    assert (row.epoch, row.episode, row.max_state) == (0, 1, "healthy")


def test_confirmed_failure_keeps_the_held_level_and_suppression(conn, emails):
    alert = _held_critical_alert(conn)
    service.close_alert(conn, alert["id"], OPERATOR, outcome="confirmed_failure", now=T)

    row = _row(conn)
    assert (row.episode, row.max_state) == (0, "critical")
    out = pipeline.handle_prediction(conn, _result(0), source=SRC, broadcast=lambda e: None)
    assert out["event"] is None
    assert emails == []


def test_record_feedback_false_alarm_on_a_closed_alert_of_the_episode_rearms(conn):
    alert = _held_critical_alert(conn)
    service.close_alert(conn, alert["id"], OPERATOR, outcome="unknown", now=T)
    assert _row(conn).episode == 0

    service.record_feedback(conn, alert["id"], OPERATOR, outcome="false_alarm", now=T)

    row = _row(conn)
    assert (row.epoch, row.episode, row.max_state) == (0, 1, "healthy")


def test_record_feedback_on_an_older_episode_alert_does_nothing(conn):
    alert = _held_critical_alert(conn)
    service.close_alert(conn, alert["id"], OPERATOR, outcome="unknown", now=T)
    health_epoch.reset_machine_health(conn, "m2", reason="test")  # epoch 1, episode 1
    health_epoch.record_level(conn, "m2", 1, "faulty", "v1")
    conn.commit()

    service.record_feedback(conn, alert["id"], OPERATOR, outcome="false_alarm", now=T)

    row = _row(conn)
    assert (row.epoch, row.episode, row.max_state) == (1, 1, "faulty")


def test_record_feedback_on_an_open_alert_does_not_rearm(conn):
    # The alert stays open, so the held level stays: re-arm happens on close.
    alert = _held_critical_alert(conn)
    service.record_feedback(conn, alert["id"], OPERATOR, outcome="false_alarm", now=T)
    row = _row(conn)
    assert (row.episode, row.max_state) == (0, "critical")
    assert live.get_alert(conn, alert["id"])["status"] == "open"


def test_demo_alert_never_rearms(conn):
    alert = _held_critical_alert(conn, source=live.DEMO_SOURCE)
    service.close_alert(conn, alert["id"], OPERATOR, outcome="false_alarm", now=T)
    row = _row(conn)
    assert (row.episode, row.max_state) == (0, "critical")


def test_rearm_and_close_are_one_transaction(conn, monkeypatch):
    alert = _held_critical_alert(conn)

    def boom(*args, **kwargs):
        raise RuntimeError("rearm failed")

    monkeypatch.setattr(health_epoch, "_rearm_locked", boom)
    with pytest.raises(RuntimeError):
        service.close_alert(conn, alert["id"], OPERATOR, outcome="false_alarm", now=T)

    assert live.get_alert(conn, alert["id"])["status"] == "open"
    assert conn.execute("SELECT COUNT(*) FROM alert_feedback WHERE alert_id = ?",
                        (alert["id"],)).fetchone()[0] == 0
    row = _row(conn)
    assert (row.episode, row.max_state) == (0, "critical")


def _newer_alert_of_the_episode(conn, older):
    """Close `older` as confirmed_failure, then let m2 escalate in the same
    episode so a newer, more severe real alert B opens."""
    service.close_alert(conn, older["id"], OPERATOR, outcome="confirmed_failure", now=T)
    _, newer = live.apply_reading(conn, "m2", "critical", None, SRC, T, health_episode=0)
    assert newer is not None and newer["status"] == "open"
    return newer


def _held_faulty_alert(conn):
    health_epoch.record_level(conn, "m2", 0, "faulty", "v1")
    conn.commit()
    _, alert = live.apply_reading(conn, "m2", "faulty", None, SRC, T, health_episode=0)
    return alert


def test_false_alarm_on_an_older_alert_does_not_rearm_while_a_newer_one_is_open(conn):
    older = _held_faulty_alert(conn)
    newer = _newer_alert_of_the_episode(conn, older)
    health_epoch.record_level(conn, "m2", 0, "critical", "v1")
    conn.commit()

    service.record_feedback(conn, older["id"], OPERATOR, outcome="false_alarm", now=T)

    row = _row(conn)
    assert (row.episode, row.max_state) == (0, "critical")
    assert live.get_alert(conn, newer["id"])["status"] == "open"


def test_false_alarm_edit_on_an_older_alert_keeps_a_newer_confirmed_hold(conn):
    older = _held_faulty_alert(conn)
    newer = _newer_alert_of_the_episode(conn, older)
    health_epoch.record_level(conn, "m2", 0, "critical", "v1")
    conn.commit()
    service.close_alert(conn, newer["id"], OPERATOR, outcome="confirmed_failure", now=T)

    service.record_feedback(conn, older["id"], OPERATOR, outcome="false_alarm", now=T)

    row = _row(conn)
    assert (row.episode, row.max_state) == (0, "critical")
