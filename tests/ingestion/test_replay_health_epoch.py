"""ReplayService.replay_once predicts through health_epoch.predict_synced and
never fans out a stale result (plan Task 6, D4, D6); a replay start begins a
new health epoch and an external reset stops the run (plan Task 9, D9)."""
import json
import logging
import threading
import time
from datetime import datetime, timezone

import pytest

from src.ingestion.replay_service import LiveMachineError, ReplayService
from src.prediction import health_epoch, rul_store
from src.prediction.rul_realtime import RealTimeRULPredictor, StaleEpoch
from tests.ingestion.test_replay_service import _FakePredictor, _row_conn, _service
from tests.prediction.test_health_epoch import _artifact, _sbase


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


# --- Task 9: replay restart and external reset (D9) -------------------------

def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    assert predicate()


def _epoch(db_path, machine_id="m1"):
    conn = _row_conn(db_path)
    try:
        return health_epoch.current(conn, machine_id)
    finally:
        conn.close()


def _insert_replay_alert(db_path, machine_id="m1") -> int:
    conn = _row_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO alerts (machine_id, opened_at, severity, health_state, message, "
            "status, source, created_at) VALUES (?, ?, 'high', 'critical', 'x', 'open', "
            "'xjtu_rul', ?)",
            (machine_id, "2026-10-07T10:00:00+00:00", "2026-10-07T10:00:00+00:00"),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _alert_status(db_path, alert_id):
    conn = _row_conn(db_path)
    try:
        return conn.execute("SELECT status FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0]
    finally:
        conn.close()


def test_restart_bumps_the_epoch_and_resolves_the_first_runs_alert(db_path):
    _, on_prediction = _fan_out_recorder()
    svc = _service(db_path, _FakePredictor(), base_interval_seconds=10.0,
                   on_prediction=on_prediction)
    svc.start("m1")
    _wait(lambda: svc.status()["m1"]["replayed"] >= 1)
    alert_id = _insert_replay_alert(db_path)  # raised by the first run
    svc.stop("m1")
    svc.start("m1")
    svc.stop("m1")
    assert _epoch(db_path).epoch == 2
    assert _alert_status(db_path, alert_id) == "resolved"


def test_after_a_restart_the_first_result_starts_a_fresh_history(db_path, tmp_path):
    conn = _row_conn(db_path)
    conn.execute("UPDATE readings SET features_json = ? WHERE machine_id = 'm1'",
                 (json.dumps(_sbase("healthy")),))
    conn.commit()
    conn.close()
    predictor = RealTimeRULPredictor(_artifact(tmp_path / "a.joblib"))
    results, on_prediction = _fan_out_recorder()
    svc = ReplayService(predictor_provider=lambda: predictor,
                        connection_factory=lambda: _row_conn(db_path),
                        base_interval_seconds=0.001, on_prediction=on_prediction)
    svc.start("m1")
    _wait(lambda: not svc.status()["m1"]["running"])
    assert [r["history_snapshots"] for r in results] == [1, 2]
    svc.start("m1")
    _wait(lambda: not svc.status()["m1"]["running"])
    svc.stop_all()
    assert results[2]["history_snapshots"] == 1


def test_a_live_machine_or_an_empty_worklist_does_not_bump(db_path):
    conn = _row_conn(db_path)
    conn.execute(
        """INSERT INTO readings (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm,
               load_kn, sample_rate_hz, features_json, dataset, vibration_h_rms,
               vibration_h_kurtosis, vibration_v_rms, vibration_v_kurtosis,
               cross_axis_rms_ratio, cross_axis_correlation)
           VALUES ('m1', ?, 9, 0, 2100.0, 12.0, 25600.0, '{}', 'live_mqtt', 0, 0, 0, 0, 0, 0)""",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.execute("UPDATE readings SET features_json = '{}' WHERE machine_id = 'm2'")
    conn.commit()
    conn.close()
    svc = _service(db_path, _FakePredictor(), base_interval_seconds=10.0)
    with pytest.raises(LiveMachineError):
        svc.start("m1")
    with pytest.raises(ValueError):
        svc.start("m2")
    assert _epoch(db_path, "m1").epoch == 0
    assert _epoch(db_path, "m2").epoch == 0


def test_start_while_running_does_not_bump(db_path):
    svc = _service(db_path, _FakePredictor(), base_interval_seconds=10.0)
    svc.start("m1")
    svc.start("m1")
    svc.stop("m1")
    assert _epoch(db_path).epoch == 1


def test_the_reset_does_not_hold_the_service_lock(db_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = health_epoch.reset_machine_health

    def blocking(conn, machine_id, **kw):
        entered.set()
        release.wait(5.0)
        return real(conn, machine_id, **kw)

    monkeypatch.setattr(health_epoch, "reset_machine_health", blocking)
    _, on_prediction = _fan_out_recorder()
    svc = _service(db_path, _FakePredictor(), base_interval_seconds=10.0,
                   on_prediction=on_prediction)
    starter = threading.Thread(target=svc.start, args=("m1",))
    starter.start()
    try:
        assert entered.wait(5.0)
        done = []
        probe = threading.Thread(target=lambda: done.append((svc.status(), svc.replay_once("m2"))))
        probe.start()
        probe.join(2.0)
        assert not probe.is_alive(), "status()/replay_once waited on the reset"
        assert done[0][1] is True
    finally:
        release.set()
        starter.join(5.0)
        svc.stop_all()


def _started(db_path):
    """A running replay of m1 parked after its first snapshot."""
    _, on_prediction = _fan_out_recorder()
    svc = _service(db_path, _FakePredictor(), base_interval_seconds=10.0,
                   on_prediction=on_prediction)
    svc.start("m1")
    _wait(lambda: svc.status()["m1"]["replayed"] >= 1)
    return svc


def test_an_external_reset_stops_the_replay(db_path):
    svc = _started(db_path)
    try:
        conn2 = _row_conn(db_path)
        health_epoch.reset_machine_health(conn2, "m1", reason="work_order")
        conn2.close()
        assert svc.replay_once("m1") is False
        assert svc.status()["m1"]["stopped_reason"] == "health_reset"
        assert _count(db_path, "SELECT COUNT(*) FROM predictions "
                               "WHERE machine_id='m1' AND source='xjtu_rul'") == 1
    finally:
        svc.stop_all()


def test_a_rearm_does_not_stop_the_replay(db_path):
    svc = _started(db_path)
    try:
        conn2 = _row_conn(db_path)
        with health_epoch.live._TRANSITION_LOCK:
            health_epoch._rearm_locked(conn2, "m1", now="2026-10-07T12:00:00+00:00")
            conn2.commit()
        conn2.close()
        assert svc.replay_once("m1") is True
        assert svc.status()["m1"].get("stopped_reason") is None
    finally:
        svc.stop_all()


def test_reset_machine_health_without_the_table_is_a_noop(conn):
    conn.execute("DROP TABLE machine_health_state")
    conn.commit()
    assert health_epoch.reset_machine_health(conn, "m1", reason="replay_restart") == (None, [])


def test_racing_starts_reset_once_and_the_run_survives(db_path, monkeypatch):
    # A enters its reset, B arrives while A is still resetting. B must not
    # bump the epoch again (that would stop A's run on its first tick).
    entered, release = threading.Event(), threading.Event()
    real = health_epoch.reset_machine_health
    calls = []

    def first_blocks(conn, machine_id, **kw):
        calls.append(machine_id)
        if len(calls) == 1:
            entered.set()
            release.wait(5.0)
        return real(conn, machine_id, **kw)

    monkeypatch.setattr(health_epoch, "reset_machine_health", first_blocks)
    _, on_prediction = _fan_out_recorder()
    svc = _service(db_path, _FakePredictor(), base_interval_seconds=10.0,
                   on_prediction=on_prediction)
    a = threading.Thread(target=svc.start, args=("m1",))
    a.start()
    try:
        assert entered.wait(5.0)
        b = threading.Thread(target=svc.start, args=("m1",))
        b.start()
        time.sleep(0.2)
        release.set()
        a.join(5.0)
        b.join(5.0)
        _wait(lambda: svc.status()["m1"]["replayed"] >= 1)
        assert _epoch(db_path).epoch == 1
        assert svc.status()["m1"].get("stopped_reason") is None
    finally:
        release.set()
        svc.stop_all()


def test_a_reset_after_the_epoch_check_still_stops_the_replay(db_path, monkeypatch):
    # The reset commits after replay_once's start but before the synced
    # prediction: the old trajectory row must not join the new epoch (D9).
    svc = _started(db_path)
    real = health_epoch.predict_synced

    def reset_first(predictor, conn, machine_id, call):
        conn2 = _row_conn(db_path)
        health_epoch.reset_machine_health(conn2, machine_id, reason="work_order")
        conn2.close()
        return real(predictor, conn, machine_id, call)

    monkeypatch.setattr(health_epoch, "predict_synced", reset_first)
    try:
        assert svc.replay_once("m1") is False
        assert svc.status()["m1"]["stopped_reason"] == "health_reset"
        assert _count(db_path, "SELECT COUNT(*) FROM predictions "
                               "WHERE machine_id='m1' AND source='xjtu_rul'") == 1
    finally:
        svc.stop_all()
