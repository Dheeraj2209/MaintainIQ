"""src/feedback/accuracy.py: real-world accuracy from operator feedback
(design/2026-10-07-prediction-feedback-design.md, decisions 9-11).

Alerts, predictions and feedback rows are inserted directly on m2 so every
number is exact. The conftest's seeded alerts on m1 carry no feedback and so
never count.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.feedback import accuracy, export

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
_TOP_KEYS = {
    "status", "reason", "model_version", "period", "horizon_minutes", "feedback_count",
    "labelled_count", "outcome_counts", "precision", "false_alarm_rate", "lead_time",
    "rul_error", "root_cause", "missed_failures", "by_model_version", "offline",
    "exportable_episode_count",
}


def _iso(minutes=0.0, base=T0):
    return (base + timedelta(minutes=minutes)).isoformat()


def _prediction(conn, *, kind="point_estimate", rul=None, horizon=120.0, version="v1"):
    return conn.execute(
        "INSERT INTO predictions (machine_id, timestamp, health_state, source, predicted_rul_minutes, "
        "rul_estimate_kind, prognostic_horizon_minutes, model_version) "
        "VALUES ('m2', ?, 'critical', 'xjtu_rul', ?, ?, ?, ?)",
        (_iso(), rul, kind, horizon, version),
    ).lastrowid


def _labelled(conn, outcome, *, failure_after=None, cause=None, probable="bearing_wear",
              version="v1", source="xjtu_rul", prediction_id=None, created_at=None,
              opened_at=None):
    opened = opened_at or _iso()
    alert_id = conn.execute(
        "INSERT INTO alerts (machine_id, opened_at, resolved_at, severity, health_state, probable_cause, "
        "status, source, created_at, prediction_id, model_version) "
        "VALUES ('m2', ?, ?, 'high', 'critical', ?, 'resolved', ?, ?, ?, ?)",
        (opened, opened, probable, source, created_at or _iso(), prediction_id, version),
    ).lastrowid
    failure_at = None
    if failure_after is not None:
        failure_at = (datetime.fromisoformat(opened) + timedelta(minutes=failure_after)).isoformat()
    conn.execute(
        "INSERT INTO alert_feedback (alert_id, outcome, actual_cause, actual_failure_at, "
        "recorded_by, recorded_at) VALUES (?, ?, ?, ?, 3, ?)",
        (alert_id, outcome, cause, failure_at, _iso()),
    )
    conn.commit()
    return alert_id


def test_nothing_labelled_is_not_applicable_with_every_key(conn):
    result = accuracy.real_world_accuracy(conn)
    assert set(result) == _TOP_KEYS
    assert result["status"] == "not_applicable"
    assert result["reason"] == "no labelled alerts yet"
    assert result["precision"] is None and result["false_alarm_rate"] is None
    assert result["feedback_count"] == 0 and result["by_model_version"] == []

    _labelled(conn, "unknown")
    result = accuracy.real_world_accuracy(conn)
    assert result["status"] == "not_applicable"
    assert result["feedback_count"] == 1 and result["labelled_count"] == 0
    assert result["outcome_counts"]["unknown"] == 1
    assert set(result["lead_time"]) >= {"count", "mean_minutes", "median_minutes"}
    assert result["missed_failures"]["status"] == "not_applicable"


def test_missing_table_is_not_applicable(conn):
    conn.execute("DROP TABLE alert_feedback")
    result = accuracy.real_world_accuracy(conn)
    assert result["status"] == "not_applicable"
    assert result["reason"] == "alert_feedback table not present"
    assert set(result) == _TOP_KEYS
    assert accuracy.real_world_kpi(conn)["status"] == "not_applicable"
    assert accuracy.root_cause_kpi(conn) is None


def test_precision_and_false_alarm_rate(conn):
    _labelled(conn, "confirmed_failure")
    _labelled(conn, "confirmed_failure")
    _labelled(conn, "maintenance_prevented")
    _labelled(conn, "false_alarm")
    _labelled(conn, "unknown")
    result = accuracy.real_world_accuracy(conn)
    assert result["status"] == "available" and result["reason"] is None
    assert result["feedback_count"] == 5 and result["labelled_count"] == 4
    assert result["precision"] == 0.75 and result["false_alarm_rate"] == 0.25
    assert result["outcome_counts"] == {"confirmed_failure": 2, "maintenance_prevented": 1,
                                        "false_alarm": 1, "unknown": 1}


def test_demo_alerts_are_excluded(conn):
    _labelled(conn, "confirmed_failure")
    _labelled(conn, "false_alarm", source="demo")
    result = accuracy.real_world_accuracy(conn)
    assert result["labelled_count"] == 1 and result["precision"] == 1.0


def test_lead_time_buckets_and_horizon(conn):
    _labelled(conn, "confirmed_failure", failure_after=70)
    _labelled(conn, "confirmed_failure", failure_after=120)
    lead = accuracy.real_world_accuracy(conn)["lead_time"]
    assert lead["count"] == 2 and lead["mean_minutes"] == 95.0 and lead["median_minutes"] == 95.0
    assert (lead["min_minutes"], lead["max_minutes"]) == (70.0, 120.0)
    assert (lead["within_horizon_count"], lead["early_count"], lead["late_count"]) == (2, 0, 0)

    _labelled(conn, "confirmed_failure", failure_after=130)
    _labelled(conn, "confirmed_failure", failure_after=-10)
    short = _prediction(conn, horizon=60.0)
    _labelled(conn, "confirmed_failure", failure_after=90, prediction_id=short)
    lead = accuracy.real_world_accuracy(conn)["lead_time"]
    assert (lead["within_horizon_count"], lead["early_count"], lead["late_count"]) == (2, 2, 1)


def test_rul_error_uses_point_estimates_only(conn):
    point = _prediction(conn, kind="point_estimate", rul=100.0)
    _labelled(conn, "confirmed_failure", failure_after=120, prediction_id=point)
    bound = _prediction(conn, kind="lower_bound", rul=120.0)
    _labelled(conn, "confirmed_failure", failure_after=300, prediction_id=bound)
    broken = _prediction(conn, kind="lower_bound", rul=120.0)
    _labelled(conn, "confirmed_failure", failure_after=50, prediction_id=broken)

    rul = accuracy.real_world_accuracy(conn)["rul_error"]
    assert rul["point_estimate_count"] == 1
    assert rul["mae_minutes"] == 20.0 and rul["median_abs_error_minutes"] == 20.0
    assert rul["lower_bound_count"] == 2 and rul["lower_bound_respected_count"] == 1


def test_root_cause_accuracy(conn):
    _labelled(conn, "confirmed_failure", cause="bearing_wear", probable="bearing_wear")   # hit
    _labelled(conn, "confirmed_failure", cause="other", probable="bearing_wear")          # miss
    _labelled(conn, "confirmed_failure", cause="unknown", probable="bearing_wear")        # excluded
    _labelled(conn, "confirmed_failure", cause=None, probable="bearing_wear")             # excluded
    _labelled(conn, "false_alarm", cause="sensor_or_data_quality_issue",
              probable="sensor_or_data_quality_issue")                                     # hit
    _labelled(conn, "unknown", cause="imbalance", probable="bearing_wear")                # miss, counts
    rc = accuracy.real_world_accuracy(conn)["root_cause"]
    assert rc == {"labelled_count": 4, "correct_count": 2, "accuracy": 0.5}
    kpi = accuracy.root_cause_kpi(conn)
    assert kpi["status"] == "available" and kpi["accuracy"] == 0.5
    assert kpi["labelled_count"] == 4 and kpi["correct_count"] == 2
    assert kpi["source"] == "operator_feedback"


def test_by_model_version_and_filter(conn):
    _labelled(conn, "confirmed_failure", version="v1", created_at=_iso(1))
    _labelled(conn, "false_alarm", version="v1", created_at=_iso(2))
    _labelled(conn, "confirmed_failure", version="v2", created_at=_iso(10))
    _labelled(conn, "false_alarm", version=None, created_at=_iso(20))

    result = accuracy.real_world_accuracy(conn)
    groups = result["by_model_version"]
    assert [g["model_version"] for g in groups] == ["v2", "v1", None]
    v1 = groups[1]
    assert (v1["feedback_count"], v1["labelled_count"], v1["precision"], v1["false_alarm_rate"]) == (
        2, 2, 0.5, 0.5)
    assert groups[2]["precision"] == 0.0

    only_v2 = accuracy.real_world_accuracy(conn, model_version="v2")
    assert only_v2["model_version"] == "v2"
    assert only_v2["labelled_count"] == 1 and only_v2["precision"] == 1.0
    assert [g["model_version"] for g in only_v2["by_model_version"]] == ["v2"]


def test_period_filters_on_created_at_not_opened_at(conn):
    _labelled(conn, "false_alarm", created_at=_iso(-60 * 24 * 10), opened_at=_iso(0))
    _labelled(conn, "confirmed_failure", created_at=_iso(0),
              opened_at="2003-10-22T12:00:00+00:00")
    result = accuracy.real_world_accuracy(conn, period_start=_iso(-60), period_end=_iso(60))
    assert result["labelled_count"] == 1 and result["precision"] == 1.0
    assert result["period"] == {"start": _iso(-60), "end": _iso(60)}


def test_unparseable_period_raises(conn):
    with pytest.raises(ValueError):
        accuracy.real_world_accuracy(conn, period_start="last tuesday")


def _register(conn, version, metrics_json, *, active=1, deployed="2026-10-01T00:00:00+00:00"):
    conn.execute(
        "INSERT INTO model_registry (model_version, artifact_path, metrics_json, deployed_at, is_active) "
        "VALUES (?, 'models/x.joblib', ?, ?, ?)", (version, metrics_json, deployed, active))
    conn.commit()


def test_offline_comparison(conn):
    assert accuracy.offline_metrics(conn) is None
    report = {"failure_detection": {"precision": 0.71, "recall": 0.83, "f1": 0.7,
                                    "false_alarm_count": 400, "missed_failure_window_count": 120}}
    _register(conn, "v1", json.dumps(report))
    _register(conn, "v0", json.dumps({"failure_detection": {"precision": 0.5, "recall": 0.5,
                                                            "false_alarm_count": 1,
                                                            "missed_failure_window_count": 2}}),
              active=0)
    assert accuracy.offline_metrics(conn) == {
        "model_version": "v1", "precision": 0.71, "recall": 0.83,
        "false_alarm_count": 400, "missed_failure_window_count": 120}
    assert accuracy.offline_metrics(conn, "v0")["precision"] == 0.5
    assert accuracy.real_world_accuracy(conn)["offline"]["model_version"] == "v1"


@pytest.mark.parametrize("metrics_json", [None, "{}", "not json", '{"failure_detection": 3}'])
def test_offline_is_null_without_usable_metrics(conn, metrics_json):
    _register(conn, "v1", metrics_json)
    assert accuracy.offline_metrics(conn) is None


def test_default_horizon_matches_training():
    from src.training.xjtu_rul import PROGNOSTIC_HORIZON_MINUTES

    assert accuracy.DEFAULT_HORIZON_MINUTES == PROGNOSTIC_HORIZON_MINUTES


def test_exportable_episode_count_matches_export(conn):
    _labelled(conn, "confirmed_failure", failure_after=30)
    _labelled(conn, "confirmed_failure")
    _labelled(conn, "confirmed_failure", failure_after=30, source="demo")
    result = accuracy.real_world_accuracy(conn)
    assert result["exportable_episode_count"] == export.count_episodes(conn) == 1


def test_real_world_kpi_compact_block(conn):
    assert accuracy.real_world_kpi(conn)["status"] == "not_applicable"
    point = _prediction(conn, rul=100.0)
    _labelled(conn, "confirmed_failure", failure_after=120, prediction_id=point)
    _labelled(conn, "false_alarm")
    kpi = accuracy.real_world_kpi(conn)
    assert kpi == {"status": "available", "reason": None, "labelled_count": 2, "precision": 0.5,
                   "false_alarm_rate": 0.5, "median_lead_minutes": 120.0, "rul_mae_minutes": 20.0}
