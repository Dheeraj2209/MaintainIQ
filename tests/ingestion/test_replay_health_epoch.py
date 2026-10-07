"""ReplayService.replay_once predicts through health_epoch.predict_synced and
never fans out a stale result (plan Task 6, D4, D6)."""
import logging

from src.prediction import health_epoch, rul_store
from src.prediction.rul_realtime import StaleEpoch
from tests.ingestion.test_replay_service import _FakePredictor, _row_conn, _service


def _fan_out_recorder():
    calls = []

    def on_prediction(conn, result, base, **kw):
        calls.append(result)
        return {}

    return calls, on_prediction


def _count(db_path, sql):
    conn = _row_conn(db_path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_replay_predicts_through_predict_synced(db_path, monkeypatch):
    seen = []
    real = health_epoch.predict_synced

    def spy(predictor, conn, machine_id, call):
        seen.append((machine_id, conn is not None))
        return real(predictor, conn, machine_id, call)

    monkeypatch.setattr(health_epoch, "predict_synced", spy)
    fanned, on_prediction = _fan_out_recorder()
    svc = _service(db_path, _FakePredictor(), on_prediction=on_prediction)
    assert svc.replay_once("m1") is True
    assert seen == [("m1", True)]
    assert len(fanned) == 1


def test_replay_skips_fan_out_when_the_result_is_stale(db_path, monkeypatch, caplog):
    monkeypatch.setattr(rul_store, "persist_prediction", lambda conn, result, reading_id=None: None)
    fanned, on_prediction = _fan_out_recorder()
    svc = _service(db_path, _FakePredictor(), on_prediction=on_prediction)
    with caplog.at_level(logging.INFO, logger="src.ingestion.replay_service"):
        assert svc.replay_once("m1") is True
    assert fanned == []
    assert _count(db_path, "SELECT COUNT(*) FROM model_inference_log WHERE machine_id='m1'") == 1
    assert "stale" in caplog.text


def test_replay_skips_a_stale_epoch_prediction(db_path, monkeypatch, caplog):
    def stale(predictor, conn, machine_id, call):
        raise StaleEpoch(machine_id)

    monkeypatch.setattr(health_epoch, "predict_synced", stale)
    fanned, on_prediction = _fan_out_recorder()
    svc = _service(db_path, _FakePredictor(), on_prediction=on_prediction)
    with caplog.at_level(logging.INFO, logger="src.ingestion.replay_service"):
        assert svc.replay_once("m1") is True  # the snapshot is consumed, the run goes on
    assert fanned == []
    assert _count(db_path, "SELECT COUNT(*) FROM predictions WHERE source='xjtu_rul'") == 0
    assert "health state" in caplog.text
    assert svc.status()["m1"]["replayed"] == 1


def test_replay_still_persists_and_fans_out_normally(db_path):
    fanned, on_prediction = _fan_out_recorder()
    svc = _service(db_path, _FakePredictor(), on_prediction=on_prediction)
    assert svc.replay_once("m1") is True
    assert len(fanned) == 1
    assert _count(db_path, "SELECT COUNT(*) FROM predictions WHERE source='xjtu_rul'") == 1
