"""POST /api/predictions/rul goes through health_epoch.predict_synced and
never fans out a stale result (plan Task 6, D4, D6)."""
import sqlite3

import pytest

from src.prediction import health_epoch, pipeline, rul_store
from src.prediction.rul_realtime import StaleEpoch
from tests.api.test_rul_persistence import _payload, rul_client  # noqa: F401  (fixture)


@pytest.fixture
def synced_spy(monkeypatch):
    calls = []
    real = health_epoch.predict_synced

    def spy(predictor, conn, machine_id, call):
        calls.append(machine_id)
        return real(predictor, conn, machine_id, call)

    monkeypatch.setattr(health_epoch, "predict_synced", spy)
    return calls


@pytest.fixture
def fan_out_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "handle_prediction",
                        lambda conn, result, **kw: calls.append(result) or {})
    return calls


def _rows(db_path, sql):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_route_predicts_through_predict_synced(rul_client, db_path, synced_spy, fan_out_spy):
    resp = rul_client.post("/api/predictions/rul", json=_payload(machine_id="m2"))
    assert resp.status_code == 200, resp.text
    assert synced_spy == ["m2"]
    assert resp.json()["history_snapshots"] == 1
    (row,) = _rows(db_path, "SELECT health_epoch, health_episode FROM predictions")
    assert (row["health_epoch"], row["health_episode"]) == (0, 0)
    assert len(fan_out_spy) == 1


def test_route_returns_409_when_the_result_is_stale(rul_client, db_path, monkeypatch, fan_out_spy):
    monkeypatch.setattr(rul_store, "persist_prediction", lambda conn, result, reading_id=None: None)
    resp = rul_client.post("/api/predictions/rul", json=_payload(machine_id="m2"))
    assert resp.status_code == 409
    assert "reset" in resp.json()["detail"]
    assert fan_out_spy == []
    # The inference still happened, so it is still logged.
    assert [r["status"] for r in _rows(db_path, "SELECT status FROM model_inference_log")] == ["ok"]


def test_route_returns_409_on_stale_epoch(rul_client, db_path, monkeypatch, fan_out_spy):
    def stale(predictor, conn, machine_id, call):
        raise StaleEpoch(machine_id)

    monkeypatch.setattr(health_epoch, "predict_synced", stale)
    resp = rul_client.post("/api/predictions/rul", json=_payload(machine_id="m2"))
    assert resp.status_code == 409
    assert fan_out_spy == []
    assert _rows(db_path, "SELECT id FROM predictions") == []


# --- DELETE /api/predictions/rul/{machine_id}/state (plan Task 10) ---------

def _login(client, role):
    from src.auth.seed import DEMO_USERS
    email, _n, password, _r = next(u for u in DEMO_USERS if u[3] == role)
    assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
    return client


def _open_m2_alert(db_path):
    from src.alerts import live
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        health_epoch.record_level(c, "m2", 0, "critical", "test-model")
        c.commit()
        _, alert = live.apply_reading(c, "m2", "critical", None, "xjtu_rul",
                                      "2026-10-07T12:00:00+00:00", health_episode=0)
        return alert["id"]
    finally:
        c.close()


def test_supervisor_delete_state_bumps_the_db_epoch_and_resolves_the_alert(rul_client, db_path, fan_out_spy):
    alert_id = _open_m2_alert(db_path)
    # One snapshot before the reset, so without it the next would be the 2nd.
    assert rul_client.post("/api/predictions/rul", json=_payload(machine_id="m2")).status_code == 200
    resp = _login(rul_client, "supervisor").delete("/api/predictions/rul/m2/state")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"machine_id": "m2", "status": "reset", "health_epoch": 1, "health_episode": 1}
    (alert,) = _rows(db_path, f"SELECT status FROM alerts WHERE id = {alert_id}")
    assert alert["status"] == "resolved"
    nxt = rul_client.post("/api/predictions/rul", json=_payload(machine_id="m2"))
    assert nxt.status_code == 200, nxt.text
    assert nxt.json()["history_snapshots"] == 1


def test_operator_delete_state_is_forbidden(rul_client, db_path):
    resp = _login(rul_client, "operator").delete("/api/predictions/rul/m2/state")
    assert resp.status_code == 403
    assert _rows(db_path, "SELECT * FROM machine_health_state WHERE machine_id = 'm2'") == []


def test_delete_state_touches_only_the_db(rul_client, db_path, monkeypatch):
    from src.prediction.rul_realtime import RealTimeRULPredictor
    calls = []
    monkeypatch.setattr(RealTimeRULPredictor, "reset_machine",
                        lambda self, *a, **k: calls.append("reset_machine"))
    monkeypatch.setattr(RealTimeRULPredictor, "restore_health",
                        lambda self, *a, **k: calls.append("restore_health"))
    resp = _login(rul_client, "supervisor").delete("/api/predictions/rul/m2/state")
    assert resp.status_code == 200, resp.text
    assert calls == []
