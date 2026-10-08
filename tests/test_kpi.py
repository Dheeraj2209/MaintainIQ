"""Tests for src/kpi/calculations.py (M4)."""
from src.kpi import calculations as kpi
from src.maintenance import records as maintenance


def test_machine_health_all_machines_sorted_by_risk(conn):
    health = kpi.machine_health_kpis(conn)
    assert [h["machine_id"] for h in health] == ["m1", "m2"]  # m1 critical -> higher risk
    assert health[0]["risk_score"] >= health[1]["risk_score"]


def test_machine_health_reports_latest_state(conn):
    m1 = kpi.machine_health_kpis(conn, "m1")[0]
    assert m1["health_state"] == "critical"
    assert m1["risk_score"] == 100.0  # critical caps risk
    assert m1["abnormal_event_count"] == 2
    assert m1["open_alert_count"] == 1
    assert m1["probable_cause"] == "bearing_wear"


def test_healthy_machine_low_risk(conn):
    m2 = kpi.machine_health_kpis(conn, "m2")[0]
    assert m2["health_state"] == "healthy"
    assert m2["risk_score"] == 0.0
    assert m2["open_alert_count"] == 0


def test_vibration_severity_from_latest_reading(conn):
    m1 = kpi.machine_health_kpis(conn, "m1")[0]
    # latest m1 reading: kurt 6.0 (>=5) and features_json band 0.5 (>=0.3) -> high
    assert m1["vibration_severity"] == "high"


def test_maintenance_kpis_avg_resolution_hours(conn):
    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    # one resolved alert spanning 5 minutes -> 0.083h
    assert m1["avg_alert_resolution_hours"] == round(5 / 60, 2)
    assert m1["unresolved_alert_count"] == 1
    assert m1["due_for_inspection"] is True  # never serviced + open alert


def test_maintenance_due_flips_after_service_but_open_alert_keeps_it_due(conn):
    maintenance.log_maintenance(conn, "m1", "2026-07-20T10:00:00Z", "svc", "t")
    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    assert m1["completed_maintenance_count"] == 1
    # still due because an alert is open
    assert m1["due_for_inspection"] is True


def test_prediction_kpis_shape():
    pred = kpi.prediction_kpis()
    # No conn: falls back to the committed XJTU-SY evaluation report, never the
    # retired NASA-IMS models/evaluation_report.json.
    assert pred["status"] == "available"
    assert pred["metrics_source"] == "models/xjtu_rul_evaluation.json"
    assert pred["failure_detection"]["f1"] is not None
    assert pred["root_cause_accuracy"]["status"] == "not_applicable"
    reason = pred["root_cause_accuracy"]["reason"]
    # Platform runs on XJTU-SY now; the stale "IMS dataset" copy must be gone.
    assert "XJTU-SY" in reason
    assert "IMS" not in reason


# --- Model-quality KPIs describe the ACTIVE (XJTU-SY RUL) model ------------

_XJTU_METRICS = {
    "dataset": "XJTU-SY",
    "validation": "leave-one-bearing-out",
    "bearing_count": 15,
    "prognostic_horizon_minutes": 120.0,
    "created_at": "2030-01-01T00:00:00+00:00",
    "failure_detection": {
        "precision": 0.72, "recall": 0.63, "f1": 0.68, "average_precision": 0.62,
        "roc_auc": 0.81, "false_alarm_count": 400, "missed_failure_window_count": 607,
    },
    "within_horizon_rul": {"mae_minutes": 30.8, "rmse_minutes": 37.8, "error_90_minutes": 62.8},
}


def _register(conn, version, *, deployed_at, active=1, metrics=_XJTU_METRICS,
              algorithm="ExtraTreesRegressor", artifact="models/xjtu_rul_model.joblib"):
    import json
    conn.execute(
        """INSERT INTO model_registry (model_version, artifact_path, algorithm,
               metrics_json, deployed_at, is_active) VALUES (?, ?, ?, ?, ?, ?)""",
        (version, artifact, algorithm,
         json.dumps(metrics) if metrics is not None else None, deployed_at, active))
    conn.commit()


def test_prediction_kpis_reports_active_registry_model(conn):
    _register(conn, "xjtu-rul-old", deployed_at="2029-01-01T00:00:00+00:00", active=0,
              metrics={**_XJTU_METRICS, "failure_detection": {"f1": 0.1}})
    _register(conn, "xjtu-rul-new", deployed_at="2030-01-02T00:00:00+00:00")
    pred = kpi.prediction_kpis(conn)
    assert pred["status"] == "available"
    assert pred["model_version"] == "xjtu-rul-new"
    assert pred["algorithm"] == "ExtraTreesRegressor"
    assert pred["winning_model"] == "ExtraTreesRegressor"
    assert pred["metrics_source"] == "model_registry"
    assert pred["failure_detection"] == {
        "precision": 0.72, "recall": 0.63, "f1": 0.68, "roc_auc": 0.81, "average_precision": 0.62}
    assert pred["false_alarm_count"] == 400
    assert pred["missed_failure_window_count"] == 607
    assert pred["rul_mae_minutes"] == 30.8
    assert pred["rul_rmse_minutes"] == 37.8
    assert pred["rul_error_90_minutes"] == 62.8
    assert pred["prognostic_horizon_minutes"] == 120.0
    assert pred["validation"] == "leave-one-bearing-out"
    assert pred["bearing_count"] == 15
    assert pred["evaluated_at"] == "2030-01-01T00:00:00+00:00"
    # Legacy classifier-only keys have no XJTU meaning: present but null.
    for key in ("accuracy", "mean_confidence", "suggested_confidence_threshold",
                "missed_fault_count"):
        assert key in pred and pred[key] is None


def test_prediction_kpis_never_reads_legacy_report(conn, monkeypatch):
    _register(conn, "xjtu-rul-new", deployed_at="2030-01-02T00:00:00+00:00")
    assert not hasattr(kpi, "_EVAL_REPORT_PATH")
    pred = kpi.prediction_kpis(conn)
    assert pred["winning_model"] != "logistic_regression"


def test_prediction_kpis_multiple_active_rows_is_deterministic(conn):
    _register(conn, "xjtu-rul-a", deployed_at="2030-01-01T00:00:00+00:00")
    _register(conn, "xjtu-rul-c", deployed_at="2030-01-03T00:00:00+00:00")
    _register(conn, "xjtu-rul-b", deployed_at="2030-01-03T00:00:00+00:00")
    # Latest deployed_at wins; tie broken by model_version DESC.
    assert kpi.prediction_kpis(conn)["model_version"] == "xjtu-rul-c"


def test_prediction_kpis_registry_row_without_metrics_uses_matching_file(conn):
    # POST /api/predictions/rul re-registers the active model without metrics.
    _register(conn, "xjtu-rul-new", deployed_at="2030-01-02T00:00:00+00:00", metrics=None)
    pred = kpi.prediction_kpis(conn)
    assert pred["status"] == "available"
    assert pred["model_version"] == "xjtu-rul-new"
    assert pred["metrics_source"] == "models/xjtu_rul_evaluation.json"
    assert pred["failure_detection"]["f1"] is not None


def test_prediction_kpis_registry_row_without_metrics_other_artifact(conn):
    _register(conn, "other-v1", deployed_at="2030-01-02T00:00:00+00:00", metrics=None,
              artifact="models/some_other_model.joblib")
    pred = kpi.prediction_kpis(conn)
    assert pred["status"] == "not_applicable"
    assert pred["model_version"] == "other-v1"
    assert "other-v1" in pred["reason"]
    assert "real_world" in pred


def test_prediction_kpis_no_registry_row_falls_back_to_file(conn):
    pred = kpi.prediction_kpis(conn)
    assert pred["status"] == "available"
    assert pred["model_version"] is None
    assert pred["metrics_source"] == "models/xjtu_rul_evaluation.json"


def test_prediction_kpis_nothing_available(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(kpi, "_XJTU_EVAL_PATH", tmp_path / "missing.json")
    pred = kpi.prediction_kpis(conn)
    assert pred["status"] == "not_applicable"
    assert "reason" in pred and "real_world" in pred
    assert pred["model_version"] is None


def test_prediction_kpis_survives_db_without_model_registry(conn):
    conn.execute("DROP TABLE model_registry")
    conn.commit()
    pred = kpi.prediction_kpis(conn)
    assert pred["status"] == "available"
    assert pred["metrics_source"] == "models/xjtu_rul_evaluation.json"


def test_work_order_kpis_without_table(conn):
    conn.execute("DROP TABLE work_order_events")
    conn.execute("DROP TABLE work_orders")
    conn.commit()
    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    assert m1["open_work_order_count"] == 0
    assert m1["avg_work_order_completion_hours"] is None
    assert kpi.summary(conn)["open_work_order_count"] == 0


# --- Real-world accuracy from operator feedback (prediction-feedback design, decision 12)

def test_prediction_kpis_without_conn_has_not_applicable_real_world():
    pred = kpi.prediction_kpis()
    assert pred["real_world"]["status"] == "not_applicable"
    assert pred["root_cause_accuracy"]["status"] == "not_applicable"


def test_prediction_kpis_with_feedback(conn):
    conn.execute(
        "INSERT INTO alert_feedback (alert_id, outcome, actual_cause, recorded_by, recorded_at) "
        "VALUES (1, 'confirmed_failure', 'bearing_wear', 3, '2026-10-07T12:00:00+00:00')")
    conn.commit()
    pred = kpi.prediction_kpis(conn)
    rc = pred["root_cause_accuracy"]
    assert rc["status"] == "available" and rc["accuracy"] == 1.0 and rc["source"] == "operator_feedback"
    assert pred["real_world"]["precision"] == 1.0
    assert kpi.summary(conn)["prediction"]["real_world"]["labelled_count"] == 1


def test_prediction_kpis_no_root_cause_feedback_extends_reason(conn):
    reason = kpi.prediction_kpis(conn)["root_cause_accuracy"]["reason"]
    assert "XJTU-SY" in reason and "no operator feedback has labelled a root cause yet" in reason


def test_kpis_survive_a_db_without_alert_feedback(conn):
    conn.execute("DROP TABLE alert_feedback")
    assert kpi.summary(conn)["prediction"]["real_world"]["status"] == "not_applicable"


def test_kpi_route_still_validates(client):
    resp = client.get("/api/kpis")
    assert resp.status_code == 200, resp.text
    assert "real_world" in resp.json()["prediction"]
    assert "real_world" in client.get("/api/kpis/detail").json()["prediction"]
