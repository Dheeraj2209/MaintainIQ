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
    # reads the real models/evaluation_report.json committed in the repo
    assert pred["status"] == "available"
    assert "accuracy" in pred
    assert pred["root_cause_accuracy"]["status"] == "not_applicable"
    reason = pred["root_cause_accuracy"]["reason"]
    # Platform runs on XJTU-SY now; the stale "IMS dataset" copy must be gone.
    assert "XJTU-SY" in reason
    assert "IMS" not in reason


def test_operational_kpis_detect_documented_failure(conn):
    op = kpi.operational_kpis(conn)
    fdb = op["faults_detected_before_failure"]
    assert fdb["documented_failure_machines"] == 1
    assert fdb["detected_early"] == 1  # m1 had abnormal events
    assert op["breakdown_reduction"]["status"] == "not_applicable"


def test_system_kpis_all_not_applicable(conn):
    sys = kpi.system_kpis(conn)
    assert all(v["status"] == "not_applicable" for v in sys.values())


def test_summary_rollup(conn):
    s = kpi.summary(conn)
    assert s["machine_count"] == 2
    assert s["health_state_counts"] == {"critical": 1, "healthy": 1}
    assert s["open_alert_count"] == 1
    assert s["machines_due_for_inspection"] == 2


def test_maintenance_kpis_avg_acknowledgement_hours_none_when_unacknowledged(conn):
    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    assert m1["avg_alert_acknowledgement_hours"] is None  # neither seeded alert is acknowledged


def test_maintenance_kpis_avg_acknowledgement_hours_computed(conn):
    from datetime import datetime, timezone
    # Acknowledge the open alert (id 2, opened 2003-10-22T13:00:00+00:00) 30 minutes later.
    conn.execute(
        "UPDATE alerts SET acknowledged_at = ?, acknowledged_by = 1 WHERE id = 2",
        ("2003-10-22T13:30:00+00:00",),
    )
    conn.commit()
    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    assert m1["avg_alert_acknowledgement_hours"] == round(30 / 60, 2)



# --- Work orders (work-orders design, decision 14) --------------------------------------

def _wo(conn, machine_id, status, created_at="2026-10-07T10:00:00+00:00", completed_at=None):
    conn.execute(
        "INSERT INTO work_orders (machine_id, status, title, created_at, updated_at, completed_at) "
        "VALUES (?, ?, 't', ?, ?, ?)",
        (machine_id, status, created_at, created_at, completed_at),
    )
    conn.commit()


def test_work_order_kpis_per_machine_and_fleet(conn):
    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    assert m1["open_work_order_count"] == 0
    assert m1["avg_work_order_completion_hours"] is None

    _wo(conn, "m1", "open")
    _wo(conn, "m1", "in_progress")
    _wo(conn, "m1", "cancelled")
    _wo(conn, "m1", "done", completed_at="2026-10-07T12:00:00+00:00")
    _wo(conn, "m1", "done", completed_at="2026-10-07T14:00:00Z")
    _wo(conn, "m2", "assigned")

    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    assert m1["open_work_order_count"] == 2
    assert m1["avg_work_order_completion_hours"] == 3.0
    assert kpi.maintenance_kpis(conn, "m2")[0]["open_work_order_count"] == 1
    assert kpi.summary(conn)["open_work_order_count"] == 3


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
