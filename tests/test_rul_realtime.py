import joblib
import numpy as np

from src.prediction.rul_realtime import RealTimeRULPredictor


class _AlwaysLateClassifier:
    def predict_proba(self, X):
        return np.tile([0.0, 1.0], (len(X), 1))


class _ThirtyMinuteRegressor:
    def predict(self, X):
        return np.full(len(X), 30.0)


def _artifact(path):
    joblib.dump({
        "classifiers": [_AlwaysLateClassifier(), _AlwaysLateClassifier()],
        "regressor": _ThirtyMinuteRegressor(),
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


def test_live_predictor_requires_persistence_and_retains_commissioning_baseline(tmp_path):
    path = tmp_path / "rul.joblib"
    _artifact(path)
    predictor = RealTimeRULPredictor(path)
    signal = np.sin(2 * np.pi * 1000 * np.arange(128) / 25_600.0)

    results = [
        predictor.predict("fan-1", signal, signal, 25_600.0, 2100.0, 12.0)
        for _ in range(65)
    ]

    assert results[0]["rul_estimate_kind"] == "lower_bound"
    assert results[1]["rul_estimate_kind"] == "lower_bound"
    assert results[2]["rul_estimate_kind"] == "point_estimate"
    assert results[-1]["history_snapshots"] == 65
    assert [row["cycle"] for row in predictor._baseline_history["fan-1"]] == list(range(20))
    assert predictor._history["fan-1"][0]["cycle"] == 5

    predictor.reset_machine("fan-1")
    assert "fan-1" not in predictor._baseline_history
    assert "fan-1" not in predictor._history
