"""MQTT ingest predicts through health_epoch.predict_synced, never fans out a
stale result, and does not report a StaleEpoch skip as an ERROR (plan Task 6,
D4, D6, drift item 4)."""
import logging
from datetime import timedelta

from src.prediction import health_epoch, rul_store
from src.prediction.rul_realtime import StaleEpoch
from tests.telemetry.test_ingest import T0, _ingestor, _messages, _snapshot, db  # noqa: F401  (fixture)


def _handle(ing, seq=0):
    topic, payload = _snapshot(seq=seq)
    return ing.handle_snapshot(topic, payload, T0 + timedelta(seconds=1))


def test_ingest_predicts_through_predict_synced(db_path, db, monkeypatch):
    seen = []
    real = health_epoch.predict_synced

    def spy(predictor, conn, machine_id, call):
        seen.append(machine_id)
        return real(predictor, conn, machine_id, call)

    monkeypatch.setattr(health_epoch, "predict_synced", spy)
    ing, pred, fan = _ingestor(db_path)
    out = _handle(ing)
    assert out.status == "accepted"
    assert seen == ["sim-01"]
    assert len(pred.calls) == 1 and len(fan) == 1


def test_ingest_skips_fan_out_when_the_result_is_stale(db_path, db, monkeypatch):
    monkeypatch.setattr(rul_store, "persist_prediction", lambda conn, result, reading_id=None: None)
    ing, _pred, fan = _ingestor(db_path)
    out = _handle(ing)
    assert out.status == "accepted" and out.reading_id is not None
    assert fan == []
    assert [m["status"] for m in _messages(db)] == ["accepted"]
    log = db.execute("SELECT status FROM model_inference_log WHERE machine_id='sim-01'").fetchall()
    assert [r["status"] for r in log] == ["ok"]


def test_ingest_stale_epoch_is_a_skip_not_an_error(db_path, db, monkeypatch, caplog):
    def stale(predictor, conn, machine_id, call):
        raise StaleEpoch(machine_id)

    monkeypatch.setattr(health_epoch, "predict_synced", stale)
    ing, _pred, fan = _ingestor(db_path)
    with caplog.at_level(logging.INFO, logger="src.telemetry.ingest"):
        out = _handle(ing)
    assert out.status == "accepted" and out.reading_id is not None
    assert out.error is None
    assert fan == []
    (msg,) = _messages(db)
    assert msg["status"] == "accepted" and msg["error"] is None
    assert db.execute("SELECT COUNT(*) FROM predictions WHERE reading_id = ?",
                      (out.reading_id,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM model_inference_log WHERE machine_id='sim-01'"
                      ).fetchone()[0] == 0
    assert "health state" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
