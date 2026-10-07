"""Demo and real readings drive separate alert episodes (cross-feature
finding: a shared episode mislabelled real failures as 'demo' and let
synthetic readings rewrite or resolve real alerts)."""
from src.alerts import live
from src.feedback import accuracy
from src.feedback import service as feedback_service

TS = "2026-10-07T12:00:00+00:00"


def _user(conn, role="supervisor"):
    return dict(conn.execute("SELECT id, email, name, role FROM users WHERE role = ?",
                             (role,)).fetchone())


def test_real_failure_after_a_demo_alert_is_its_own_real_alert(conn):
    kind, demo = live.apply_reading(conn, "m2", "degrading", "imbalance", "demo", TS)
    assert kind == "alert_created" and demo["source"] == "demo"

    kind, real = live.apply_reading(conn, "m2", "critical", "bearing_wear", "xjtu_rul", TS)
    assert kind == "alert_created"
    assert real["id"] != demo["id"] and real["source"] == "xjtu_rul"
    # The demo alert is untouched.
    assert live.get_alert(conn, demo["id"])["severity"] == "low"

    feedback_service.close_alert(conn, real["id"], _user(conn), now=TS,
                                 outcome="confirmed_failure", actual_cause="bearing_wear")
    result = accuracy.real_world_accuracy(conn)
    assert result["feedback_count"] >= 1


def test_demo_readings_never_escalate_or_resolve_a_real_alert(conn):
    # conftest: alert 2 is m1's open real ('ml') alert, severity high.
    before = live.get_alert(conn, 2)
    kind, demo = live.apply_reading(conn, "m1", "critical", "imbalance", "demo", TS)
    assert kind == "alert_created" and demo["id"] != 2
    live.apply_reading(conn, "m1", "healthy", None, "demo", TS)  # demo reset
    after = live.get_alert(conn, 2)
    assert (after["status"], after["severity"], after["probable_cause"]) == (
        "open", before["severity"], before["probable_cause"])
    assert live.get_alert(conn, demo["id"])["status"] == "resolved"


def test_same_kind_readings_still_share_one_episode(conn):
    _, first = live.apply_reading(conn, "m2", "degrading", "imbalance", "mqtt", TS)
    kind, escalated = live.apply_reading(conn, "m2", "critical", "bearing_wear", "xjtu_rul", TS)
    assert kind == "alert_escalated" and escalated["id"] == first["id"]
