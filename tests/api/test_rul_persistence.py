import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_db
from src.api.routes import predictions as predictions_route


class _AlwaysLateClassifier:
    def predict_proba(self, X):
        return np.tile([0.0, 1.0], (len(X), 1))


class _ThirtyMinuteRegressor:
    def predict(self, X):
        return np.full(len(X), 30.0)


def _make_predictor(tmp_path):
    import joblib
    from src.prediction.rul_realtime import RealTimeRULPredictor

    path = tmp_path / "rul.joblib"
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
    return RealTimeRULPredictor(path)


@pytest.fixture
def rul_client(db_path, tmp_path):
    predictor = _make_predictor(tmp_path)

    def _override_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[predictions_route.get_predictor] = lambda: predictor
    try:
        with TestClient(app) as client:
            from src.auth.seed import DEMO_USERS
            email, _n, password, _r = DEMO_USERS[0]
            assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(predictions_route.get_predictor, None)


def _payload(**overrides):
    body = {
        "machine_id": "m1",
        "horizontal": list(np.sin(2 * np.pi * 1000 * np.arange(256) / 25_600.0)),
        "vertical": list(np.cos(2 * np.pi * 1000 * np.arange(256) / 25_600.0)),
        "sample_rate_hz": 25_600.0,
        "speed_rpm": 2100.0,
        "load_kn": 12.0,
    }
    body.update(overrides)
    return body


def _count(db_path, table, machine_id="m1"):
    c = sqlite3.connect(db_path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table} WHERE machine_id = ?", (machine_id,)).fetchone()[0]
    finally:
        c.close()


def test_predict_writes_one_prediction_and_one_inference_log(rul_client, db_path):
    resp = rul_client.post("/api/predictions/rul", json=_payload())
    assert resp.status_code == 200, resp.text
    assert _count(db_path, "predictions") == 1
    assert _count(db_path, "model_inference_log") == 1
    log = sqlite3.connect(db_path).execute(
        "SELECT status, model_version, latency_ms FROM model_inference_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert log[0] == "ok"
    assert log[1] == "test-model"
    assert log[2] >= 0.0


def test_predict_registers_active_model(rul_client, db_path):
    rul_client.post("/api/predictions/rul", json=_payload())
    reg = sqlite3.connect(db_path).execute(
        "SELECT model_version, artifact_path, is_active FROM model_registry"
    ).fetchall()
    assert len(reg) == 1
    assert reg[0][0] == "test-model"
    assert not reg[0][1].startswith("/") and ":" not in reg[0][1].split("/")[0]  # relative, not absolute
    assert reg[0][2] == 1


def test_bad_input_logs_error_and_returns_422(rul_client, db_path):
    resp = rul_client.post("/api/predictions/rul", json=_payload(speed_rpm=0.0))
    assert resp.status_code == 422
    # Pydantic rejects speed_rpm<=0 before the route body, so no error row is
    # written. Use a value that passes validation but fails inside predict():
    # an empty machine_id is blocked by schema too, so drive the predictor error
    # via a sample_rate the predictor accepts but classifier rejects is not
    # possible here; assert the HTTP contract only.


def test_predictor_value_error_is_logged(db_path, tmp_path, monkeypatch):
    # Force predict() to raise ValueError to exercise the error path + error log.
    predictor = _make_predictor(tmp_path)

    def _boom(*a, **k):
        raise ValueError("live feature vector is missing trained features")

    monkeypatch.setattr(predictor, "predict", _boom)

    def _override_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[predictions_route.get_predictor] = lambda: predictor
    try:
        with TestClient(app) as client:
            from src.auth.seed import DEMO_USERS
            email, _n, password, _r = DEMO_USERS[0]
            client.post("/api/auth/login", json={"email": email, "password": password})
            resp = client.post("/api/predictions/rul", json=_payload())
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(predictions_route.get_predictor, None)

    log = sqlite3.connect(db_path).execute(
        "SELECT status, error_message FROM model_inference_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert log[0] == "error"
    assert "missing trained features" in log[1]
    assert _count(db_path, "predictions") == 0
