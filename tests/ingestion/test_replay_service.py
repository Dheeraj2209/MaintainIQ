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


def test_replay_once_fans_out_to_alerts_and_notifications(db_path):
    """Persisting a prediction row is not enough: a replayed prediction has to
    reach the alert state machine, email paging and the realtime feed, or the
    model runs invisibly and nobody is ever told a machine is failing."""
    seen = []
    pred = _FakePredictor()
    svc = _service(db_path, pred, on_prediction=lambda conn, result, features, **_: seen.append(
        (result, features)
    ))

    svc.replay_once("m1")

    assert len(seen) == 1
    result, features = seen[0]
    assert result["health_state"] == "faulty"
    assert result["machine_id"] == "m1"
    # The same stored feature vector the prediction was made from, so root
    # cause is classified off real data rather than re-derived.
    assert features == {"vibration_h_high_band_energy_ratio": 0.1}


def test_default_fan_out_opens_a_real_alert(db_path, monkeypatch):
    """Wired end to end with no hook injected — the production default."""
    monkeypatch.setattr(
        "src.notifications.dispatch.send_email",
        lambda to, subject, body: None,
    )
    # m2 has no open alert in the seed fixture, so this exercises alert_created.
    svc = _service(db_path, _FakePredictor())

    svc.replay_once("m2")

    conn = _row_conn(db_path)
    try:
        alert = conn.execute(
            "SELECT * FROM alerts WHERE machine_id='m2' AND status='open'"
        ).fetchone()
        notifications = conn.execute(
            "SELECT status FROM notifications WHERE alert_id = ?", (alert["id"],)
        ).fetchall()
    finally:
        conn.close()

    assert alert["health_state"] == "faulty"
    assert alert["severity"] == "medium"
    assert alert["source"] == "xjtu_rul"   # attributable to the model, not a demo
    # Linked to the replayed reading and the prediction made from it.
    conn = _row_conn(db_path)
    try:
        prediction = conn.execute("SELECT * FROM predictions WHERE id = ?",
                                  (alert["prediction_id"],)).fetchone()
        reading = conn.execute("SELECT * FROM readings WHERE id = ?", (alert["reading_id"],)).fetchone()
    finally:
        conn.close()
    assert prediction["machine_id"] == "m2" and prediction["reading_id"] == alert["reading_id"]
    assert reading["machine_id"] == "m2" and reading["cycle"] == 0
    assert alert["model_version"] == "test-model-v1"
    assert notifications and all(row["status"] == "sent" for row in notifications)


def test_fan_out_failure_does_not_lose_the_prediction(db_path):
    """The prediction row is the durable record. A downstream alerting or
    mail failure must not roll it back or stall the replay loop."""
    def _boom(conn, result, features, **_):
        raise RuntimeError("alerting is down")

    svc = _service(db_path, _FakePredictor(), on_prediction=_boom)

    assert svc.replay_once("m1") is True

    conn = _row_conn(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM predictions WHERE machine_id='m1' AND source='xjtu_rul'"
        ).fetchone()["c"]
    finally:
        conn.close()
    assert n == 1
    assert svc.status()["m1"]["replayed"] == 1


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


# --- Replay vs live MQTT ingest (M6 review regression) -----------------------------------


def _add_live_reading(db_path, machine_id, timestamp):
    conn = _row_conn(db_path)
    try:
        conn.execute(
            """INSERT INTO readings (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm,
                                     load_kn, sample_rate_hz, vibration_h_rms,
                                     vibration_h_kurtosis, vibration_v_rms, vibration_v_kurtosis,
                                     cross_axis_rms_ratio, cross_axis_correlation,
                                     features_json, dataset)
               VALUES (?, ?, 999, 0, 2100.0, 12.0, 25600.0, 1, 3, 1, 3, 1, 0.5,
                       '{"h_rms": 1.0}', 'live_mqtt')""",
            (machine_id, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def test_live_readings_are_never_in_the_replay_worklist(db_path):
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _add_live_reading(db_path, "m1", old)
    pred = _FakePredictor()
    svc = _service(db_path, pred)
    replayed = 0
    while svc.replay_once("m1"):
        replayed += 1
    assert replayed == 2  # the two seeded dataset readings, not the live one
    assert all(base != {"h_rms": 1.0} for _m, base, *_ in pred.calls)


def test_start_refuses_a_machine_fed_by_live_telemetry(db_path):
    from datetime import datetime, timezone

    from src.ingestion.replay_service import LiveMachineError

    _add_live_reading(db_path, "m1", datetime.now(timezone.utc).isoformat())
    svc = _service(db_path, _FakePredictor())
    with pytest.raises(LiveMachineError):
        svc.start("m1")
    assert "m1" not in svc.status() or not svc.status()["m1"]["running"]



def test_hook_receives_the_prediction_and_reading_ids(db_path):
    seen = []
    svc = _service(db_path, _FakePredictor(),
                   on_prediction=lambda conn, result, features, **ids: seen.append(ids))

    svc.replay_once("m2")

    conn = _row_conn(db_path)
    try:
        prediction = conn.execute(
            "SELECT id, reading_id FROM predictions WHERE machine_id='m2' AND source='xjtu_rul'"
        ).fetchone()
        first = conn.execute("SELECT id FROM readings WHERE machine_id='m2' ORDER BY cycle").fetchone()
    finally:
        conn.close()
    assert seen == [{"prediction_id": prediction["id"], "reading_id": first["id"]}]
    assert prediction["reading_id"] == first["id"]
