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
