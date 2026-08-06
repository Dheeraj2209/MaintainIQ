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


import json as _json

import numpy as np

from src.ingestion.xjtu_sy import extract_snapshot_features
from src.prediction.rul_realtime import RealTimeRULPredictor
from src.storage.db import init_schema, insert_machines, insert_readings


class _AlwaysLate:
    def predict_proba(self, X):
        return np.tile([0.0, 1.0], (len(X), 1))


class _ThirtyMin:
    def predict(self, X):
        return np.full(len(X), 30.0)


def _fake_artifact(path):
    import joblib

    joblib.dump({
        "classifiers": [_AlwaysLate(), _AlwaysLate()],
        "regressor": _ThirtyMin(),
        "classifier_feature_columns": ["speed_rpm", "h_kurtosis"],
        "regressor_feature_columns": ["h_rms"],
        "feature_columns": ["h_rms"],
        "feature_bounds_99pct": {},
        "conformal_error_90_minutes": 10.0,
        "model_version": "test-model",
        "prognostic_horizon_minutes": 120.0,
        "failure_probability_threshold": 0.6,
        "probability_smoothing_window": 3,
        "warning_persistence_snapshots": 3,
        "baseline_window": 20,
        "sample_rate_hz": 25_600.0,
    }, path)
    return path


def _signal(cycle, n=256, sr=25_600.0):
    t = np.arange(n) / sr
    amp = 1.0 + 0.05 * cycle
    return amp * np.sin(2 * np.pi * 1000 * t), amp * np.cos(2 * np.pi * 1000 * t)


def _fresh_db(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "rehydrate.db")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    insert_machines(conn, [{"machine_id": "b1", "dataset": "xjtu_sy"}])
    return conn


def test_rehydrate_reproduces_next_prediction_health_state(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib")
    n_history = 8
    sr, speed, load = 25_600.0, 2100.0, 12.0

    # Warm predictor A: process n_history snapshots and persist each as a reading.
    conn = _fresh_db(tmp_path)
    warm = RealTimeRULPredictor(art)
    rows = []
    for cycle in range(n_history):
        h, v = _signal(cycle)
        warm.predict("b1", h, v, sr, speed, load)
        base = extract_snapshot_features(h, v, sr)
        rows.append({
            "machine_id": "b1", "timestamp": f"2020-01-01T00:{cycle:02d}:00+00:00",
            "cycle": cycle, "elapsed_minutes": float(cycle), "speed_rpm": speed, "load_kn": load,
            "sample_rate_hz": sr,
            "vibration_h_rms": base["h_rms"], "vibration_h_kurtosis": base["h_kurtosis"],
            "vibration_v_rms": base["v_rms"], "vibration_v_kurtosis": base["v_kurtosis"],
            "cross_axis_rms_ratio": base["cross_axis_rms_ratio"],
            "cross_axis_correlation": base["cross_axis_correlation"],
            "rul_minutes": None, "features_json": _json.dumps(base), "dataset": "xjtu_sy",
        })
    insert_readings(conn, rows)

    # Cold predictor B: rehydrate from the stored readings.
    cold = RealTimeRULPredictor(art)
    replayed = rul_store.rehydrate(cold, conn, "b1")
    assert replayed == n_history

    # Next snapshot fed to both must agree.
    h_next, v_next = _signal(n_history)
    warm_next = warm.predict("b1", h_next, v_next, sr, speed, load)
    cold_next = cold.predict("b1", h_next, v_next, sr, speed, load)
    assert cold_next["health_state"] == warm_next["health_state"]
    assert cold_next["predicted_rul_minutes"] == warm_next["predicted_rul_minutes"]
    assert cold_next["history_snapshots"] == warm_next["history_snapshots"]
    conn.close()


def test_rehydrate_skips_readings_without_feature_vector(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib")
    conn = _fresh_db(tmp_path)
    insert_readings(conn, [{
        "machine_id": "b1", "timestamp": "2020-01-01T00:00:00+00:00", "cycle": 0,
        "elapsed_minutes": 0.0, "speed_rpm": 2100.0, "load_kn": 12.0, "sample_rate_hz": 25_600.0,
        "vibration_h_rms": 0.1, "vibration_h_kurtosis": 3.0, "vibration_v_rms": 0.1,
        "vibration_v_kurtosis": 3.0, "cross_axis_rms_ratio": 1.0, "cross_axis_correlation": 0.0,
        "rul_minutes": None, "features_json": "{}", "dataset": "xjtu_sy",
    }])
    cold = RealTimeRULPredictor(art)
    assert rul_store.rehydrate(cold, conn, "b1") == 0
    conn.close()
