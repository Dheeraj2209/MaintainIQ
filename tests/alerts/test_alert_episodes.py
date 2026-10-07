"""Alert episodes per health episode (plan 2026-10-07-ratchet-maintenance-reset,
D7): with a health_episode, live.apply_reading drops a stale reading, stamps
alerts.health_episode, and does not re-open after a human close in the same
episode unless the severity rises. pipeline.handle_prediction passes the
episode only when the result says the ratchet is on.

m2 has no alert and no machine_health_state row in the conftest seed, so its
episode is 0.
"""
import pytest

from src.alerts import live
from src.prediction import health_epoch, pipeline

T = "2026-10-07T12:00:00+00:00"
SRC = "xjtu_rul"


def _rows(conn, machine_id="m2"):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM alerts WHERE machine_id = ? ORDER BY id", (machine_id,))]


def _human_close(conn, alert_id, user_id=3):
    with live._TRANSITION_LOCK:
        assert live._resolve_locked(conn, alert_id, user_id, T)
        conn.commit()


def _open_and_close(conn, state="critical", episode=0):
    _, alert = live.apply_reading(conn, "m2", state, None, SRC, T, health_episode=episode)
    _human_close(conn, alert["id"])
    return alert


def test_stale_episode_is_dropped(conn):
    health_epoch.reset_machine_health(conn, "m2", reason="test")  # episode 1
    assert live.apply_reading(conn, "m2", "critical", None, SRC, T, health_episode=0) is None
    assert _rows(conn) == []


def test_new_alert_stores_its_health_episode(conn):
    event, alert = live.apply_reading(conn, "m2", "critical", None, SRC, T, health_episode=0)
    assert event == "alert_created"
    assert alert["health_episode"] == 0
    assert _rows(conn)[0]["health_episode"] == 0
    assert live.get_alert(conn, alert["id"])["health_episode"] == 0


def test_human_close_suppresses_same_or_lower_severity_in_the_episode(conn):
    _open_and_close(conn, "critical")
    assert live.apply_reading(conn, "m2", "critical", None, SRC, T, health_episode=0) is None
    assert live.apply_reading(conn, "m2", "faulty", None, SRC, T, health_episode=0) is None
    assert len(_rows(conn)) == 1


def test_higher_severity_after_a_close_reopens(conn):
    _open_and_close(conn, "faulty")
    event, alert = live.apply_reading(conn, "m2", "critical", None, SRC, T, health_episode=0)
    assert event == "alert_created" and alert["severity"] == "high"
    assert len(_rows(conn)) == 2


def test_reset_starts_a_new_episode_that_creates(conn):
    _open_and_close(conn, "critical")
    health_epoch.reset_machine_health(conn, "m2", reason="test")
    event, alert = live.apply_reading(conn, "m2", "critical", None, SRC, T, health_episode=1)
    assert event == "alert_created" and alert["health_episode"] == 1


def test_newest_resolved_system_resolve_does_not_suppress(conn):
    # R1-6: human close at critical, then a same-episode system resolve (an
    # alert opened another way, resolved by a healthy reading), then critical.
    _open_and_close(conn, "critical")
    conn.execute(
        "INSERT INTO alerts (machine_id, opened_at, severity, health_state, status, source, "
        "created_at, health_episode) VALUES ('m2', ?, 'high', 'critical', 'open', ?, ?, 0)",
        (T, SRC, T))
    conn.commit()
    event, _ = live.apply_reading(conn, "m2", "healthy", None, SRC, T, health_episode=0)
    assert event == "alert_resolved"
    event, _ = live.apply_reading(conn, "m2", "critical", None, SRC, T, health_episode=0)
    assert event == "alert_created"


def test_without_an_episode_the_state_machine_is_legacy(conn):
    _open_and_close(conn, "critical")
    event, alert = live.apply_reading(conn, "m2", "critical", None, SRC, T)
    assert event == "alert_created" and alert["health_episode"] is None


def test_demo_readings_ignore_episodes(conn):
    health_epoch.reset_machine_health(conn, "m2", reason="test")
    event, _ = live.apply_reading(conn, "m2", "critical", None, live.DEMO_SOURCE, T, health_episode=0)
    assert event == "alert_created"


# --- pipeline.handle_prediction -----------------------------------------------------

def _result(*, ratchet, episode=0, state="critical"):
    return {"machine_id": "m2", "health_state": state, "model_version": "v1", "warnings": [],
            "health_ratchet": ratchet, "health_episode": episode}


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "notify_alert", lambda conn, alert: sent.append(alert) or 1)
    return sent


@pytest.fixture
def spy(monkeypatch):
    calls = []
    real = pipeline.apply_reading

    def wrapped(*args, **kwargs):
        calls.append(kwargs.get("health_episode"))
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "apply_reading", wrapped)
    return calls


def test_pipeline_with_ratchet_off_is_legacy(conn, emails, spy):
    _open_and_close(conn, "critical")
    out = pipeline.handle_prediction(conn, _result(ratchet=False), source=SRC, broadcast=lambda e: None)
    assert out["event"] == "alert_created"
    assert len(emails) == 1
    assert spy == [None]


def test_pipeline_with_ratchet_on_suppresses_after_a_close(conn, emails, spy):
    _open_and_close(conn, "critical")
    out = pipeline.handle_prediction(conn, _result(ratchet=True), source=SRC, broadcast=lambda e: None)
    assert out["event"] is None
    assert emails == []
    assert spy == [0]
