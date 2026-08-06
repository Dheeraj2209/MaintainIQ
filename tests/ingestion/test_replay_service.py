import sqlite3
import time

import pytest

from src.ingestion.replay_service import ReplayService


class _FakePredictor:
    """Stand-in for RealTimeRULPredictor. Records calls and returns a result
    dict shaped exactly like _predict_from_base's output, so the real
    rul_store.persist_prediction / log_inference mapping runs unchanged."""

    def __init__(self):
        self.calls = []

    def _predict_from_base(self, machine_id, base, *, sample_rate_hz, speed_rpm, load_kn):
        self.calls.append((machine_id, dict(base), sample_rate_hz, speed_rpm, load_kn))
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


def _service(db_path, predictor, **kwargs):
    return ReplayService(
        predictor_provider=lambda: predictor,
        connection_factory=lambda: _row_conn(db_path),
        **kwargs,
    )


def test_replay_once_routes_through_predict_and_persist(db_path):
    pred = _FakePredictor()
    svc = _service(db_path, pred)
    # seed m1 has two readings, each with a non-empty features_json.
    assert svc.replay_once("m1") is True
    assert len(pred.calls) == 1
    # feature vector came from readings.features_json (never recomputed).
    _mid, base, sr, speed, load = pred.calls[0]
    assert base == {"vibration_h_high_band_energy_ratio": 0.1}
    assert sr == 25600.0 and speed == 2100.0 and load == 12.0

    conn = _row_conn(db_path)
    try:
        pred_rows = conn.execute(
            "SELECT source, model_version, predicted_rul_minutes, reading_id "
            "FROM predictions WHERE machine_id='m1' AND source='xjtu_rul'"
        ).fetchall()
        log_rows = conn.execute(
            "SELECT status FROM model_inference_log WHERE machine_id='m1'"
        ).fetchall()
    finally:
        conn.close()
    assert len(pred_rows) == 1
    assert pred_rows[0]["source"] == "xjtu_rul"          # same source a live /rul write uses
    assert pred_rows[0]["model_version"] == "test-model-v1"
    assert pred_rows[0]["predicted_rul_minutes"] == 42.0
    assert pred_rows[0]["reading_id"] is not None         # linked to the stored reading
    assert len(log_rows) == 1 and log_rows[0]["status"] == "ok"


def test_replay_once_exhausts_after_all_snapshots(db_path):
    pred = _FakePredictor()
    svc = _service(db_path, pred)
    assert svc.replay_once("m1") is True   # cycle 0
    assert svc.replay_once("m1") is True   # cycle 1
    assert svc.replay_once("m1") is False  # exhausted (seed has 2 readings)
    assert len(pred.calls) == 2
    st = svc.status()["m1"]
    assert st["replayed"] == 2
    assert st["cycle"] == 1                # last processed reading's cycle


def test_start_sets_running_then_stop_halts(db_path):
    pred = _FakePredictor()
    # long interval: the loop parks in stop_event.wait() and cannot finish on its own.
    svc = _service(db_path, pred, base_interval_seconds=10.0)
    svc.start("m1")
    assert svc.status()["m1"]["running"] is True
    svc.stop("m1")
    assert svc.status()["m1"]["running"] is False
    assert svc._threads.get("m1") is None   # thread cleaned up — no leaked loop


def test_start_replays_all_then_marks_not_running(db_path):
    pred = _FakePredictor()
    svc = _service(db_path, pred, base_interval_seconds=0.001)
    svc.start("m1")
    deadline = time.time() + 5.0
    while svc.status()["m1"]["running"] and time.time() < deadline:
        time.sleep(0.02)
    svc.stop_all()
    st = svc.status()["m1"]
    assert st["running"] is False
    assert st["replayed"] == 2
    conn = _row_conn(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM predictions "
            "WHERE machine_id='m1' AND source='xjtu_rul'"
        ).fetchone()["c"]
    finally:
        conn.close()
    assert n == 2


def test_start_unknown_machine_raises(db_path):
    svc = _service(db_path, _FakePredictor())
    with pytest.raises(ValueError):
        svc.start("does-not-exist")


def test_start_rejects_nonpositive_speed(db_path):
    svc = _service(db_path, _FakePredictor())
    with pytest.raises(ValueError):
        svc.start("m1", speed_multiplier=0)


def test_worker_error_is_captured_in_status(db_path):
    class _BoomPredictor:
        def _predict_from_base(self, *args, **kwargs):
            raise RuntimeError("model exploded")

    svc = ReplayService(
        predictor_provider=lambda: _BoomPredictor(),
        connection_factory=lambda: _row_conn(db_path),
        base_interval_seconds=0.001,
    )
    svc.start("m1")
    deadline = time.time() + 5.0
    while svc.status()["m1"]["running"] and time.time() < deadline:
        time.sleep(0.02)
    svc.stop_all()
    st = svc.status()["m1"]
    assert st["running"] is False
    assert st["replayed"] == 0
    assert st["error"] is not None and "model exploded" in st["error"]
