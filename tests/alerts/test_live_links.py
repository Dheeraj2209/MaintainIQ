"""Alert -> prediction/reading/model links on live.apply_reading
(design/2026-10-07-prediction-feedback-design.md, decision 3) and the
feedback embedded in API alert rows (decision 15).

m2 has no alert in the conftest seed, so it is used for fresh episodes.
"""
import json

from src.alerts import live


def _row(conn, alert_id):
    return dict(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())


def test_apply_reading_stores_the_opening_links(conn):
    event, alert = live.apply_reading(
        conn, "m2", "degrading", "imbalance", "xjtu_rul", "2026-10-07T12:00:00+00:00",
        prediction_id=7, reading_id=3, model_version="v1",
    )
    assert event == "alert_created"
    assert (alert["prediction_id"], alert["reading_id"], alert["model_version"]) == (7, 3, "v1")
    assert alert["closed_by"] is None
    row = _row(conn, alert["id"])
    assert (row["prediction_id"], row["reading_id"], row["model_version"], row["closed_by"]) == (
        7, 3, "v1", None)


def test_escalation_keeps_the_opening_links(conn):
    _, alert = live.apply_reading(
        conn, "m2", "degrading", "imbalance", "xjtu_rul", "2026-10-07T12:00:00+00:00",
        prediction_id=7, reading_id=3, model_version="v1",
    )
    event, escalated = live.apply_reading(
        conn, "m2", "critical", "bearing_wear", "xjtu_rul", "2026-10-07T12:10:00+00:00",
        prediction_id=8, reading_id=4, model_version="v2",
    )
    assert event == "alert_escalated"
    row = _row(conn, alert["id"])
    assert (row["prediction_id"], row["reading_id"], row["model_version"]) == (7, 3, "v1")
    assert (row["severity"], row["probable_cause"]) == ("high", "bearing_wear")
    assert escalated["prediction_id"] == 7


def test_apply_reading_without_links_stores_nulls(conn):
    _, alert = live.apply_reading(conn, "m2", "critical", None, "demo", "2026-10-07T12:00:00+00:00")
    row = _row(conn, alert["id"])
    assert all(row[c] is None for c in ("prediction_id", "reading_id", "model_version", "closed_by"))


def test_get_alert_round_trips_the_new_columns(conn):
    conn.execute("UPDATE alerts SET prediction_id = 11, reading_id = 2, model_version = 'v9', "
                 "closed_by = 3 WHERE id = 1")
    conn.commit()
    alert = live.get_alert(conn, 1)
    assert (alert["prediction_id"], alert["reading_id"], alert["model_version"], alert["closed_by"]) == (
        11, 2, "v9", 3)


def test_api_alert_parses_embedded_feedback(conn):
    conn.execute(
        "INSERT INTO alert_feedback (alert_id, outcome, actual_cause, notes, recorded_by, recorded_at) "
        "VALUES (1, 'false_alarm', 'sensor_or_data_quality_issue', 'loose mount', 3, '2026-10-07T12:00:00+00:00')"
    )
    conn.commit()
    rows = {r["id"]: r for r in conn.execute(f"SELECT {live.API_ALERT_COLUMNS} FROM alerts")}

    with_feedback = live.api_alert(rows[1])
    assert "feedback_json" not in with_feedback
    fb = with_feedback["feedback"]
    assert fb["outcome"] == "false_alarm"
    assert fb["actual_cause"] == "sensor_or_data_quality_issue"
    assert fb["recorded_by"] == 3 and fb["recorded_by_name"] == "Otis Operator"
    assert fb["alert_id"] == 1 and fb["notes"] == "loose mount"
    assert fb["updated_at"] is None

    assert live.api_alert(rows[2])["feedback"] is None
    json.dumps(with_feedback)  # plain JSON-serialisable dict
