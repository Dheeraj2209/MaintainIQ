import json

import pytest

from src.prediction import rul_store


def _result(**overrides):
    base = {
        "machine_id": "m1",
        "predicted_rul_minutes": 42.0,
        "predicted_rul_hours": 0.7,
        "rul_estimate_kind": "point_estimate",
        "prognostic_horizon_minutes": 120.0,
        "failure_within_horizon_probability": 0.83,
        "prediction_interval_90_minutes": [30.0, 54.0],
        "health_state": "faulty",
        "model_version": "test-model",
        "history_snapshots": 61,
        "out_of_distribution": True,
        "warnings": ["warming_up: 3/60 snapshots", "rul_lower_bound: ..."],
    }
    base.update(overrides)
    return base


def test_persist_prediction_maps_all_columns(conn):
    row_id = rul_store.persist_prediction(conn, _result(), reading_id=7)
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (row_id,)).fetchone()
    assert row["machine_id"] == "m1"
    assert row["reading_id"] == 7
    assert row["source"] == "xjtu_rul"
    assert row["health_state"] == "faulty"
    assert row["predicted_rul_minutes"] == 42.0
    assert row["rul_estimate_kind"] == "point_estimate"
    assert row["failure_within_horizon_probability"] == 0.83
    assert row["prognostic_horizon_minutes"] == 120.0
    assert row["prediction_interval_low"] == 30.0
    assert row["prediction_interval_high"] == 54.0
    assert row["model_version"] == "test-model"
    assert row["out_of_distribution"] == 1
    assert row["history_snapshots"] == 61
    assert json.loads(row["warnings_json"]) == _result()["warnings"]
    assert row["timestamp"]  # set to a non-empty ISO timestamp


def test_persist_prediction_handles_lower_bound_open_interval(conn):
    row_id = rul_store.persist_prediction(
        conn, _result(prediction_interval_90_minutes=[120.0, None], out_of_distribution=False)
    )
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (row_id,)).fetchone()
    assert row["prediction_interval_low"] == 120.0
    assert row["prediction_interval_high"] is None
    assert row["reading_id"] is None
    assert row["out_of_distribution"] == 0


def test_log_inference_success_row(conn):
    rul_store.log_inference(
        conn, machine_id="m1", model_version="test-model", latency_ms=12.5, result=_result()
    )
    row = conn.execute("SELECT * FROM model_inference_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "ok"
    assert row["error_message"] is None
    assert row["latency_ms"] == 12.5
    assert row["failure_probability"] == 0.83
    assert row["predicted_rul_minutes"] == 42.0
    assert row["out_of_distribution"] == 1
    assert row["warming_up"] == 1
    assert row["warnings_count"] == 2


def test_log_inference_error_row(conn):
    rul_store.log_inference(
        conn, machine_id="m1", model_version="test-model", latency_ms=3.0, error="bad input"
    )
    row = conn.execute("SELECT * FROM model_inference_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "error"
    assert row["error_message"] == "bad input"
    assert row["failure_probability"] is None
    assert row["predicted_rul_minutes"] is None
    assert row["warming_up"] == 0
    assert row["warnings_count"] == 0


def test_register_active_model_upserts_and_is_idempotent(conn):
    rul_store.register_active_model(
        conn, model_version="v1", artifact_path="models/xjtu_rul_model.joblib", algorithm="extratrees"
    )
    rul_store.register_active_model(
        conn, model_version="v1", artifact_path="models/xjtu_rul_model.joblib", algorithm="extratrees"
    )
    rows = conn.execute("SELECT * FROM model_registry").fetchall()
    assert len(rows) == 1
    assert rows[0]["artifact_path"] == "models/xjtu_rul_model.joblib"
    assert rows[0]["is_active"] == 1
    assert rows[0]["deployed_at"]


def test_register_active_model_deactivates_previous(conn):
    rul_store.register_active_model(conn, model_version="v1", artifact_path="a")
    rul_store.register_active_model(conn, model_version="v2", artifact_path="b")
    active = conn.execute("SELECT model_version FROM model_registry WHERE is_active = 1").fetchall()
    assert [r["model_version"] for r in active] == ["v2"]
