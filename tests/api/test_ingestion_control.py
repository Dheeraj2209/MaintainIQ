import sqlite3
import time

import pytest

from src.ingestion.replay_service import ReplayService


class _FakePredictor:
    def __init__(self):
        self.calls = []

    def _predict_from_base(self, machine_id, base, *, sample_rate_hz, speed_rpm, load_kn):
        self.calls.append(machine_id)
        return {
            "machine_id": machine_id,
            "predicted_rul_minutes": 42.0,
            "predicted_rul_hours": 0.7,
            "rul_estimate_kind": "point_estimate",
            "prognostic_horizon_minutes": 720.0,
            "failure_within_horizon_probability": 0.9,
            "raw_failure_within_horizon_probability": 0.9,
            "warning_persistence_snapshots": 1,
            "prediction_interval_90_minutes": [30.0, 54.0],
            "health_state": "faulty",
            "model_version": "test-model-v1",
            "history_snapshots": len(self.calls),
            "out_of_distribution": False,
            "outside_training_features": [],
            "warnings": [],
        }


def _row_conn(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def replay_svc(db_path):
    """Override get_replay_service with a service bound to the temp DB and a
    fake predictor; always stop threads on teardown (no leaked loops)."""
    from src.api.app import app
    from src.api.routes.ingestion import get_replay_service

    pred = _FakePredictor()
    svc = ReplayService(
        predictor_provider=lambda: pred,
        connection_factory=lambda: _row_conn(db_path),
        base_interval_seconds=0.001,
    )
    app.dependency_overrides[get_replay_service] = lambda: svc
    yield svc
    svc.stop_all()
    app.dependency_overrides.pop(get_replay_service, None)


def test_operator_cannot_start(auth_client, replay_svc):
    client = auth_client("operator")
    resp = client.post("/api/ingestion/replay/start", json={"machine_id": "m1"})
    assert resp.status_code == 403


def test_operator_cannot_stop(auth_client, replay_svc):
    client = auth_client("operator")
    resp = client.post("/api/ingestion/replay/stop", json={"machine_id": "m1"})
    assert resp.status_code == 403


def test_supervisor_can_start_and_stop(auth_client, replay_svc):
    client = auth_client("supervisor")
    resp = client.post("/api/ingestion/replay/start", json={"machine_id": "m1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "started"
    resp = client.post("/api/ingestion/replay/stop", json={"machine_id": "m1"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


def test_status_visible_to_operator(auth_client, replay_svc):
    client = auth_client("operator")
    resp = client.get("/api/ingestion/replay/status")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_status_requires_auth(anon_client, replay_svc):
    resp = anon_client.get("/api/ingestion/replay/status")
    assert resp.status_code == 401


def test_start_unknown_machine_returns_404(auth_client, replay_svc):
    client = auth_client("supervisor")
    resp = client.post("/api/ingestion/replay/start", json={"machine_id": "ghost"})
    assert resp.status_code == 404


def test_replay_produces_predictions_and_status_advances(auth_client, replay_svc, db_path):
    supervisor = auth_client("supervisor")
    resp = supervisor.post(
        "/api/ingestion/replay/start",
        json={"machine_id": "m1", "speed_multiplier": 1.0},
    )
    assert resp.status_code == 200, resp.text

    # Poll status until the background replay drains the two seed readings.
    deadline = time.time() + 5.0
    status = {}
    while time.time() < deadline:
        status = supervisor.get("/api/ingestion/replay/status").json()
        m1 = status.get("m1", {})
        if m1.get("running") is False and m1.get("replayed") == 2:
            break
        time.sleep(0.02)

    supervisor.post("/api/ingestion/replay/stop", json={"machine_id": "m1"})
    assert status["m1"]["replayed"] == 2
    assert status["m1"]["cycle"] == 1

    # Replayed snapshots wrote predictions rows via the same persist path a live
    # /predictions/rul call uses (source 'xjtu_rul'), linked to the stored reading.
    conn = _row_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT source, model_version, reading_id FROM predictions "
            "WHERE machine_id='m1' AND source='xjtu_rul'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert all(r["source"] == "xjtu_rul" and r["reading_id"] is not None for r in rows)


def test_admin_can_start_and_stop(auth_client, replay_svc):
    client = auth_client("admin")
    resp = client.post("/api/ingestion/replay/start", json={"machine_id": "m1"})
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/ingestion/replay/stop", json={"machine_id": "m1"})
    assert resp.status_code == 200
