"""Unit tests for report generators + renderers (Phase 6 Task 1)."""
import json
import sqlite3

import pytest

from src.reports import generators
from src.storage.db import init_schema


@pytest.fixture
def rconn():
    """A fully-controlled in-memory DB on the real schema (no shared seed)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _machine(conn, machine_id="m1"):
    conn.execute(
        "INSERT INTO machines (machine_id, dataset, is_documented_failure) "
        "VALUES (?, 'xjtu_sy', 1)",
        (machine_id,),
    )
    conn.commit()


def _prediction(conn, *, machine_id="m1", timestamp, health_state="degrading",
                rul=None, low=None, high=None, prob=None, ood=0, cause=None,
                model_version="v1", confidence=None):
    conn.execute(
        """INSERT INTO predictions
               (machine_id, reading_id, timestamp, health_state, confidence, source, probable_cause,
                created_at, predicted_rul_minutes, rul_estimate_kind,
                failure_within_horizon_probability, prediction_interval_low,
                prediction_interval_high, model_version, out_of_distribution)
           VALUES (?, ?, ?, ?, ?, 'xjtu_rul', ?, ?, ?, 'point_estimate', ?, ?, ?, ?, ?)""",
        (machine_id, None, timestamp, health_state, confidence, cause, timestamp, rul,
         prob, low, high, model_version, ood),
    )
    conn.commit()


def _alert(conn, *, machine_id="m1", opened_at, severity="high", health_state="critical",
           status="open", cause="bearing_wear", message="boom"):
    conn.execute(
        """INSERT INTO alerts
               (machine_id, opened_at, severity, health_state, probable_cause, message,
                status, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'ml', ?)""",
        (machine_id, opened_at, severity, health_state, cause, message, status, opened_at),
    )
    conn.commit()


def _inference(conn, *, machine_id="m1", timestamp, latency_ms, status="ok",
               ood=0, warming=0, model_version="v1"):
    conn.execute(
        """INSERT INTO model_inference_log
               (machine_id, timestamp, model_version, latency_ms, out_of_distribution,
                warming_up, warnings_count, status)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
        (machine_id, timestamp, model_version, latency_ms, ood, warming, status),
    )
    conn.commit()


# ---- machine_prognostic ----

def test_machine_prognostic_current_is_latest(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="degrading",
                rul=200.0, low=150.0, high=260.0, prob=0.4, cause="bearing_wear", confidence=0.7)
    _prediction(rconn, timestamp="2030-01-01T01:00:00+00:00", health_state="critical",
                rul=60.0, low=40.0, high=90.0, prob=0.9, cause="bearing_wear", confidence=0.95)
    s = generators.machine_prognostic(rconn, scope="m1")
    assert s["report_type"] == "machine_prognostic"
    assert s["scope"] == "m1"
    assert s["prediction_count"] == 2
    assert s["current"]["health_state"] == "critical"
    assert s["current"]["predicted_rul_minutes"] == 60.0
    assert s["current"]["prediction_interval_low"] == 40.0
    assert s["current"]["prediction_interval_high"] == 90.0
    assert s["current"]["out_of_distribution"] is False
    assert [p["health_state"] for p in s["health_state_trajectory"]] == ["degrading", "critical"]
    assert s["recommended_maintenance"]["within_minutes"] == 60.0
    assert s["recommended_maintenance"]["by_timestamp"] == "2030-01-01T02:00:00+00:00"


def test_machine_prognostic_no_predictions(rconn):
    _machine(rconn, "m9")
    s = generators.machine_prognostic(rconn, scope="m9")
    assert s["prediction_count"] == 0
    assert s["current"] is None
    assert s["recommended_maintenance"] == {"within_minutes": None, "by_timestamp": None}
    assert s["health_state_trajectory"] == []
    assert s["recent_alerts"] == []


def test_machine_prognostic_period_filters(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2029-12-01T00:00:00+00:00", health_state="healthy", rul=500.0)
    _prediction(rconn, timestamp="2030-01-15T00:00:00+00:00", health_state="critical", rul=30.0)
    s = generators.machine_prognostic(
        rconn, scope="m1",
        period_start="2030-01-01T00:00:00+00:00",
        period_end="2030-01-31T00:00:00+00:00",
    )
    assert s["prediction_count"] == 1
    assert s["current"]["health_state"] == "critical"


def test_machine_prognostic_alerts_included_and_escaped(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="critical", rul=30.0)
    _alert(rconn, opened_at="2030-01-01T00:05:00+00:00", message="pipe | in message")
    s = generators.machine_prognostic(rconn, scope="m1")
    assert len(s["recent_alerts"]) == 1
    assert s["recent_alerts"][0]["message"] == "pipe | in message"
    md = generators.render_markdown(s)
    assert "pipe \\| in message" in md


# ---- model_performance ----

def test_model_performance_counts_and_percentiles(rconn):
    for lat, st, ood, warm in [
        (10.0, "ok", 0, 0), (20.0, "ok", 1, 0), (30.0, "ok", 0, 1),
        (40.0, "ok", 1, 1), (50.0, "error", 0, 0),
    ]:
        _inference(rconn, timestamp="2030-01-01T00:00:00+00:00",
                   latency_ms=lat, status=st, ood=ood, warming=warm)
    s = generators.model_performance(rconn, scope="fleet")
    assert s["inference_count"] == 5
    assert s["error_count"] == 1
    assert s["error_rate"] == 0.2
    assert s["latency_p50_ms"] == 30.0
    assert s["latency_p95_ms"] == 50.0
    assert s["ood_rate"] == 0.5
    assert s["warming_up_rate"] == 0.5
    assert s["active_model"] is None


def test_model_performance_active_model_metrics(rconn):
    rconn.execute(
        """INSERT INTO model_registry
               (model_version, artifact_path, algorithm, trained_at, metrics_json,
                deployed_at, is_active)
           VALUES ('v1', 'models/x.joblib', 'ExtraTrees', '2030-01-01', ?, '2030-01-02', 1)""",
        (json.dumps({"rmse": 12.5}),),
    )
    rconn.commit()
    s = generators.model_performance(rconn, scope="fleet")
    assert s["inference_count"] == 0
    assert s["active_model"]["model_version"] == "v1"
    assert s["active_model"]["algorithm"] == "ExtraTrees"
    assert s["active_model"]["metrics"] == {"rmse": 12.5}


# ---- fleet_summary ----

def test_fleet_summary_distribution_and_at_risk(rconn):
    _machine(rconn, "m1")
    _machine(rconn, "m2")
    _machine(rconn, "m3")  # no prediction
    _prediction(rconn, machine_id="m1", timestamp="2030-01-01T00:00:00+00:00",
                health_state="critical", rul=30.0)
    _prediction(rconn, machine_id="m2", timestamp="2030-01-01T00:00:00+00:00",
                health_state="healthy", rul=500.0)
    _alert(rconn, machine_id="m1", opened_at="2030-01-01T00:05:00+00:00", status="open")
    _alert(rconn, machine_id="m1", opened_at="2030-01-01T00:06:00+00:00", status="resolved")
    rconn.execute(
        "INSERT INTO maintenance_records (machine_id, performed_at, created_at, type) "
        "VALUES ('m1', '2030-01-01T01:00:00+00:00', '2030-01-01T01:00:00+00:00', 'preventive')"
    )
    rconn.commit()
    s = generators.fleet_summary(rconn, scope="fleet")
    assert s["machine_count"] == 3
    assert s["health_distribution"] == {"critical": 1, "healthy": 1}
    assert s["top_at_risk"][0]["machine_id"] == "m1"
    assert s["top_at_risk"][0]["predicted_rul_minutes"] == 30.0
    assert s["alert_counts"] == {"total": 2, "open": 1, "resolved": 1}
    assert s["maintenance_counts"] == {"total": 1, "preventive": 1, "corrective": 0}


def test_fleet_summary_latest_prediction_per_machine(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, machine_id="m1", timestamp="2030-01-01T00:00:00+00:00",
                health_state="healthy", rul=500.0)
    _prediction(rconn, machine_id="m1", timestamp="2030-01-01T02:00:00+00:00",
                health_state="critical", rul=25.0)
    s = generators.fleet_summary(rconn, scope="fleet")
    assert s["health_distribution"] == {"critical": 1}
    assert s["top_at_risk"][0]["predicted_rul_minutes"] == 25.0


# ---- build_summary dispatch ----

def test_build_summary_dispatches_machine_prognostic(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="critical", rul=30.0)
    s = generators.build_summary(rconn, report_type="machine_prognostic", scope="m1")
    assert s["report_type"] == "machine_prognostic"
    assert s == generators.machine_prognostic(rconn, scope="m1")


def test_build_summary_dispatches_model_performance(rconn):
    s = generators.build_summary(rconn, report_type="model_performance", scope="fleet")
    assert s["report_type"] == "model_performance"


def test_build_summary_dispatches_fleet_summary(rconn):
    _machine(rconn, "m1")
    s = generators.build_summary(rconn, report_type="fleet_summary", scope="fleet")
    assert s["report_type"] == "fleet_summary"


def test_build_summary_rejects_unknown_type(rconn):
    with pytest.raises(ValueError):
        generators.build_summary(rconn, report_type="bogus", scope="fleet")


# ---- renderers ----

def test_render_json_roundtrips(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="critical", rul=30.0)
    s = generators.machine_prognostic(rconn, scope="m1")
    assert json.loads(generators.render_json(s)) == s


def test_render_markdown_headings_per_type(rconn):
    _machine(rconn, "m1")
    _prediction(rconn, timestamp="2030-01-01T00:00:00+00:00", health_state="critical", rul=30.0)
    s = generators.machine_prognostic(rconn, scope="m1")
    md = generators.render_markdown(s)
    assert md.startswith("# Machine Prognostic Report")
    assert "## Health-State Trajectory" in md


def test_md_cell_escapes_pipes_and_newlines():
    assert generators._md_cell("a|b") == "a\\|b"
    assert generators._md_cell("a\nb") == "a b"
    assert generators._md_cell(None) == ""


def test_render_rejects_unknown_format(rconn):
    _machine(rconn, "m1")
    s = generators.machine_prognostic(rconn, scope="m1")
    with pytest.raises(ValueError):
        generators.render("pdf", s)
