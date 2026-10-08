"""src/prediction/health_epoch.py: the DB authority for a machine's health
epoch, alert episode and held (ratcheted) level (plan Task 2)."""
import random
import sqlite3
import threading

import pytest

from src.prediction import health_epoch
from src.prediction.health_epoch import HealthRow

NOW = "2026-10-07T12:00:00+00:00"


def _row(conn, machine_id):
    return conn.execute(
        "SELECT * FROM machine_health_state WHERE machine_id = ?", (machine_id,)
    ).fetchone()


def _insert_alert(conn, machine_id="m2", *, source="xjtu_rul", status="open",
                  state="critical") -> int:
    cur = conn.execute(
        "INSERT INTO alerts (machine_id, opened_at, severity, health_state, message, "
        "status, source, created_at) VALUES (?, ?, 'high', ?, 'x', ?, ?, ?)",
        (machine_id, "2026-10-07T10:00:00+00:00", state, status, source, NOW),
    )
    conn.commit()
    return cur.lastrowid


# --- current ---------------------------------------------------------------

def test_current_defaults_without_a_row(conn):
    assert health_epoch.current(conn, "m2") == HealthRow(0, 0, "healthy", None, None)


def test_current_is_none_without_the_table(conn):
    conn.execute("DROP TABLE machine_health_state")
    conn.commit()
    assert health_epoch.current(conn, "m2") is None


# --- record_level ----------------------------------------------------------

def test_record_level_only_raises(conn):
    health_epoch.record_level(conn, "m2", 0, "faulty", "v1")
    health_epoch.record_level(conn, "m2", 0, "degrading", "v1")
    conn.commit()
    row = health_epoch.current(conn, "m2")
    assert (row.epoch, row.episode, row.max_state, row.model_version) == (0, 0, "faulty", "v1")
    health_epoch.record_level(conn, "m2", 0, "critical", "v1")
    assert health_epoch.current(conn, "m2").max_state == "critical"


def test_record_level_stale_episode_is_a_noop(conn):
    health_epoch.reset_machine_health(conn, "m2", reason="test", now=NOW)  # (1, 1)
    health_epoch.record_level(conn, "m2", 0, "critical", "v1")
    assert health_epoch.current(conn, "m2").max_state == "healthy"
    health_epoch.record_level(conn, "m2", 1, "faulty", "v1")
    assert health_epoch.current(conn, "m2").max_state == "faulty"


def test_record_level_without_a_row_inserts_only_at_episode_zero(conn):
    health_epoch.record_level(conn, "m2", 1, "critical", "v1")
    assert _row(conn, "m2") is None
    health_epoch.record_level(conn, "m2", 0, "degrading", "v1")
    assert health_epoch.current(conn, "m2") == HealthRow(0, 0, "degrading", "v1", None)


def test_record_level_does_not_commit(db_path, conn):
    health_epoch.record_level(conn, "m2", 0, "faulty", "v1")
    other = sqlite3.connect(db_path, timeout=0.2)
    try:
        conn.rollback()
        assert other.execute(
            "SELECT COUNT(*) FROM machine_health_state WHERE machine_id = 'm2'").fetchone()[0] == 0
    finally:
        other.close()


def test_record_level_rejects_unknown_state(conn):
    with pytest.raises(ValueError):
        health_epoch.record_level(conn, "m2", 0, "broken", "v1")


# --- reset -----------------------------------------------------------------

def test_reset_bumps_epoch_and_episode_and_resolves_the_real_alert(conn):
    health_epoch.record_level(conn, "m2", 0, "critical", "v1")
    real = _insert_alert(conn)
    demo = _insert_alert(conn, source="demo")

    row, resolved = health_epoch.reset_machine_health(conn, "m2", reason="maintenance:7", now=NOW)

    assert (row.epoch, row.episode, row.max_state) == (1, 1, "healthy")
    assert row.epoch_started_at == NOW
    stored = _row(conn, "m2")
    assert stored["reset_reason"] == "maintenance:7"
    assert stored["episode_started_at"] == NOW
    assert [a["id"] for a in resolved] == [real]
    assert resolved[0]["status"] == "resolved"
    assert resolved[0]["resolved_at"] == NOW
    assert resolved[0]["closed_by"] is None
    assert set(resolved[0]) >= {"severity", "health_state", "page_level", "source"}
    alert = conn.execute("SELECT status, resolved_at, closed_by FROM alerts WHERE id = ?",
                         (real,)).fetchone()
    assert (alert["status"], alert["resolved_at"], alert["closed_by"]) == ("resolved", NOW, None)
    assert conn.execute("SELECT status FROM alerts WHERE id = ?", (demo,)).fetchone()[0] == "open"


def test_second_reset_resolves_nothing(conn):
    _insert_alert(conn)
    health_epoch.reset_machine_health(conn, "m2", reason="a", now=NOW)
    row, resolved = health_epoch.reset_machine_health(conn, "m2", reason="b", now=NOW)
    assert resolved == []
    assert (row.epoch, row.episode) == (2, 2)


def test_reset_commits(db_path, conn):
    health_epoch.reset_machine_health(conn, "m2", reason="a", now=NOW)
    other = sqlite3.connect(db_path)
    try:
        assert other.execute(
            "SELECT epoch FROM machine_health_state WHERE machine_id = 'm2'").fetchone()[0] == 1
    finally:
        other.close()


def test_reset_rolls_back_on_failure(conn, monkeypatch):
    alert_id = _insert_alert(conn)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(health_epoch, "_resolved_alerts", boom)
    with pytest.raises(RuntimeError):
        health_epoch.reset_machine_health(conn, "m2", reason="a", now=NOW)
    assert _row(conn, "m2") is None
    assert conn.execute("SELECT status FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0] == "open"


# --- re-arm ----------------------------------------------------------------

def test_rearm_bumps_episode_only(conn):
    health_epoch.reset_machine_health(conn, "m2", reason="a", now=NOW)  # (1, 1)
    health_epoch.record_level(conn, "m2", 1, "critical", "v1")
    row = health_epoch._rearm_locked(conn, "m2", now=NOW)
    conn.commit()
    assert (row.epoch, row.episode, row.max_state) == (1, 2, "healthy")
    assert health_epoch.current(conn, "m2") == row


def test_rearm_without_a_row(conn):
    row = health_epoch._rearm_locked(conn, "m2", now=NOW)
    assert (row.epoch, row.episode, row.max_state) == (0, 1, "healthy")


# --- announce_resolved -----------------------------------------------------

def test_announce_resolved_fans_out_each_and_swallows_errors(conn, monkeypatch):
    from src.prediction import pipeline

    calls = []

    def fake_fan_out(c, applied, *, at, **kwargs):
        calls.append((applied, at))
        if len(calls) == 1:
            raise RuntimeError("broadcast down")
        return {}

    monkeypatch.setattr(pipeline, "fan_out", fake_fan_out)
    alerts = [{"id": 1, "machine_id": "m1"}, {"id": 2, "machine_id": "m1"}]
    health_epoch.announce_resolved(conn, alerts, at=NOW)
    assert calls == [(("alert_resolved", alerts[0]), NOW), (("alert_resolved", alerts[1]), NOW)]


# --- effective_state -------------------------------------------------------

def test_effective_state_current_episode_uses_the_prediction(conn):
    health_epoch.record_level(conn, "m2", 0, "faulty", "v1")
    got = health_epoch.effective_state(conn, "m2", {"health_state": "critical", "health_episode": 0})
    assert got == {"health_state": "critical", "reset_pending_reading": False, "health_episode": 0}


def test_effective_state_older_episode_uses_the_held_level(conn):
    health_epoch.record_level(conn, "m2", 0, "critical", "v1")
    health_epoch._rearm_locked(conn, "m2", now=NOW)
    health_epoch.record_level(conn, "m2", 1, "degrading", "v1")
    got = health_epoch.effective_state(conn, "m2", {"health_state": "critical", "health_episode": 0})
    assert got == {"health_state": "degrading", "reset_pending_reading": True, "health_episode": 1}


def test_effective_state_null_episode_with_a_row_uses_the_held_level(conn):
    health_epoch.reset_machine_health(conn, "m2", reason="a", now=NOW)
    got = health_epoch.effective_state(conn, "m2", {"health_state": "critical", "health_episode": None})
    assert got == {"health_state": "healthy", "reset_pending_reading": True, "health_episode": 1}


def test_effective_state_null_episode_on_a_never_reset_seed_is_held_not_pending(conn):
    # Migration 008 seeds a held level at episode 0 without any reset; the
    # machine's latest prediction predates the ratchet (no episode). Nothing
    # was reset, so this is the held level, not "reset, awaiting reading".
    health_epoch.record_level(conn, "m2", 0, "critical", None)
    got = health_epoch.effective_state(conn, "m2", {"health_state": "faulty", "health_episode": None})
    assert got == {"health_state": "critical", "reset_pending_reading": False, "health_episode": 0}


def test_effective_state_null_episode_without_a_row_is_legacy(conn):
    got = health_epoch.effective_state(conn, "m2", {"health_state": "faulty"})
    assert got == {"health_state": "faulty", "reset_pending_reading": False, "health_episode": 0}


def test_effective_state_without_the_table_uses_the_prediction(conn):
    conn.execute("DROP TABLE machine_health_state")
    conn.commit()
    got = health_epoch.effective_state(conn, "m2", {"health_state": "faulty", "health_episode": 3})
    assert got == {"health_state": "faulty", "reset_pending_reading": False, "health_episode": 3}


def test_effective_state_without_a_prediction(conn):
    assert health_epoch.effective_state(conn, "m2", None) == {
        "health_state": None, "reset_pending_reading": False, "health_episode": 0}
    health_epoch.record_level(conn, "m2", 0, "faulty", "v1")
    assert health_epoch.effective_state(conn, "m2", None) == {
        "health_state": "faulty", "reset_pending_reading": False, "health_episode": 0}
    health_epoch._rearm_locked(conn, "m2", now=NOW)
    assert health_epoch.effective_state(conn, "m2", None) == {
        "health_state": "healthy", "reset_pending_reading": True, "health_episode": 1}


# --- concurrency -----------------------------------------------------------

def _thread_conn(db_path):
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def test_concurrent_record_level_and_reset_stay_consistent(db_path):
    """2 threads x 50 record_level calls (each at the episode it just read)
    interleaved with one reset: no IntegrityError, and the final held level is
    exactly the worst level recorded at the final episode."""
    written = []  # (episode, state) of every record_level call
    errors = []
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def recorder(seed):
        rng = random.Random(seed)
        conn = _thread_conn(db_path)
        try:
            barrier.wait()
            for _ in range(50):
                episode = health_epoch.current(conn, "m2").episode
                state = rng.choice(["healthy", "degrading", "faulty", "critical"])
                health_epoch.record_level(conn, "m2", episode, state, "v1")
                conn.commit()
                with lock:
                    written.append((episode, state))
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            conn.close()

    def resetter():
        conn = _thread_conn(db_path)
        try:
            barrier.wait()
            health_epoch.reset_machine_health(conn, "m2", reason="race")
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=recorder, args=(s,)) for s in (1, 2)]
    threads.append(threading.Thread(target=resetter))
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert errors == []
    check = _thread_conn(db_path)
    try:
        final = health_epoch.current(check, "m2")
    finally:
        check.close()
    assert (final.epoch, final.episode) == (1, 1)
    at_final = [s for e, s in written if e == final.episode]
    expected = max(at_final or ["healthy"], key=health_epoch.HEALTH_RANK.__getitem__)
    assert final.max_state == expected


# --- rul_store.persist_prediction: episode-guarded (plan Task 5, D6) --------

def _ratchet_result(**overrides):
    result = {
        "machine_id": "m2",
        "predicted_rul_minutes": 20.0,
        "rul_estimate_kind": "point_estimate",
        "prognostic_horizon_minutes": 120.0,
        "failure_within_horizon_probability": 0.9,
        "prediction_interval_90_minutes": [10.0, 30.0],
        "health_state": "critical",
        "instant_health_state": "critical",
        "health_ratchet": True,
        "commissioning": None,
        "health_epoch": 0,
        "health_episode": 0,
        "model_version": "v1",
        "history_snapshots": 30,
        "out_of_distribution": False,
        "warnings": [],
    }
    result.update(overrides)
    return result


def _pred(conn, row_id):
    return conn.execute("SELECT * FROM predictions WHERE id = ?", (row_id,)).fetchone()


def test_persist_at_the_current_episode_stamps_and_raises_the_level(conn):
    from src.prediction import rul_store

    health_epoch.reset_machine_health(conn, "m2", reason="a", now=NOW)  # (1, 1)
    row_id = rul_store.persist_prediction(
        conn, _ratchet_result(health_epoch=1, health_episode=1, health_state="faulty",
                              instant_health_state="degrading"), reading_id=3)
    assert row_id is not None
    row = _pred(conn, row_id)
    assert (row["health_epoch"], row["health_episode"]) == (1, 1)
    assert row["instant_health_state"] == "degrading"
    assert row["health_state"] == "faulty"
    assert row["reading_id"] == 3
    assert row["source"] == "xjtu_rul"
    stored = health_epoch.current(conn, "m2")
    assert (stored.max_state, stored.model_version) == ("faulty", "v1")


def test_persist_commits_row_and_level_together(db_path, conn):
    from src.prediction import rul_store

    rul_store.persist_prediction(conn, _ratchet_result())
    other = sqlite3.connect(db_path)
    try:
        assert other.execute(
            "SELECT max_state FROM machine_health_state WHERE machine_id = 'm2'").fetchone()[0] == "critical"
        assert other.execute(
            "SELECT COUNT(*) FROM predictions WHERE machine_id = 'm2' AND health_episode = 0"
        ).fetchone()[0] == 1
    finally:
        other.close()


def test_persist_of_a_stale_episode_returns_none_and_writes_nothing(conn):
    from src.prediction import rul_store

    health_epoch.reset_machine_health(conn, "m2", reason="a", now=NOW)  # (1, 1)
    before = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    assert rul_store.persist_prediction(conn, _ratchet_result(health_episode=0)) is None
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == before
    assert health_epoch.current(conn, "m2").max_state == "healthy"


def test_persist_with_the_ratchet_off_inserts_without_recording_a_level(conn):
    from src.prediction import rul_store

    row_id = rul_store.persist_prediction(conn, _ratchet_result(health_ratchet=False))
    assert _pred(conn, row_id)["health_episode"] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM machine_health_state WHERE machine_id = 'm2'").fetchone()[0] == 0


def test_persist_of_an_ood_row_does_not_latch(conn):
    """D8: an out-of-distribution row is shown above the held floor but never
    raises the DB held level either."""
    from src.prediction import rul_store

    row_id = rul_store.persist_prediction(conn, _ratchet_result(out_of_distribution=True))
    assert _pred(conn, row_id)["health_state"] == "critical"
    assert health_epoch.current(conn, "m2").max_state == "healthy"


def test_persist_of_a_commissioning_row_does_not_latch(conn):
    from src.prediction import rul_store

    rul_store.persist_prediction(
        conn, _ratchet_result(commissioning={"seen": 3, "of": 20}))
    assert health_epoch.current(conn, "m2").max_state == "healthy"


def test_persist_without_an_episode_is_the_legacy_insert(conn):
    from src.prediction import rul_store

    result = _ratchet_result(health_episode=None, health_epoch=None)
    for key in ("instant_health_state", "health_ratchet", "commissioning"):
        result.pop(key)
    row = _pred(conn, rul_store.persist_prediction(conn, result))
    assert row["health_state"] == "critical"
    assert (row["health_epoch"], row["health_episode"], row["instant_health_state"]) == (None, None, None)
    assert health_epoch.current(conn, "m2").max_state == "healthy"


def test_persist_falls_back_to_legacy_without_the_health_schema(conn):
    from src.prediction import rul_store

    conn.execute("DROP TABLE machine_health_state")
    conn.commit()
    row_id = rul_store.persist_prediction(conn, _ratchet_result())
    assert row_id is not None
    assert _pred(conn, row_id)["health_episode"] is None


# --- predict_synced: every predictor call syncs to the DB first (Task 6) ----

import json

import joblib
import numpy as np

from src.alerts import live
from src.prediction import rul_store
from src.prediction.rul_realtime import RealTimeRULPredictor
from src.training.xjtu_rul import ROLLING_SOURCE_COLUMNS

M = "m2"
RUL = {"healthy": 900.0, "degrading": 500.0, "faulty": 100.0, "critical": 10.0}
KW = dict(sample_rate_hz=25_600.0, speed_rpm=2100.0, load_kn=12.0)


class _AlwaysLate:
    """Late life on every row; with _ScriptedRul each base picks its state."""

    def __init__(self):
        self.calls = 0

    def predict_proba(self, X):
        self.calls += 1
        return np.tile([0.0, 1.0], (len(X), 1))


class _NeverLate:
    def predict_proba(self, X):
        return np.tile([1.0, 0.0], (len(X), 1))


class _ScriptedRul:
    def predict(self, X):
        return X["scripted_rul"].to_numpy(dtype=float)


def _artifact(path, **overrides):
    artifact = {
        "classifiers": [_AlwaysLate()],
        "regressor": _ScriptedRul(),
        "classifier_feature_columns": ["speed_rpm", "h_kurtosis"],
        "regressor_feature_columns": ["scripted_rul"],
        "feature_bounds_99pct": {},
        "conformal_error_90_minutes": 10.0,
        "model_version": "test-model",
        "prognostic_horizon_minutes": 1000.0,
        "failure_probability_threshold": 0.6,
        "probability_smoothing_window": 1,
        "warning_persistence_snapshots": 1,
        "baseline_window": 2,
        "sample_rate_hz": 25_600.0,
    }
    artifact.update(overrides)
    joblib.dump(artifact, path)
    return path


def _make(tmp_path, name="a", **overrides):
    return RealTimeRULPredictor(_artifact(tmp_path / f"{name}.joblib", **overrides))


def _sbase(state):
    base = {column: 1.0 for column in ROLLING_SOURCE_COLUMNS}
    base["scripted_rul"] = RUL[state]
    return base


def _synced(predictor, conn, state, machine_id=M):
    base = _sbase(state)
    return health_epoch.predict_synced(
        predictor, conn, machine_id,
        lambda: predictor._predict_from_base(machine_id, base, **KW))


def _insert_reading(conn, state, machine_id=M) -> int:
    cur = conn.execute(
        """INSERT INTO readings (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm,
               load_kn, sample_rate_hz, features_json, dataset,
               vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
               vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation)
           VALUES (?, ?, (SELECT COALESCE(MAX(cycle), -1) + 1 FROM readings WHERE machine_id = ?),
                   0, 2100.0, 12.0, 25600.0, ?, 'xjtu_sy', 0, 0, 0, 0, 0, 0)""",
        (machine_id, NOW, machine_id, json.dumps(_sbase(state))),
    )
    return cur.lastrowid


def _synced_persist(predictor, conn, state, machine_id=M):
    """One synced prediction, stored the way ingest/replay store it: the
    reading (features_json) and its prediction row."""
    result = _synced(predictor, conn, state, machine_id)
    reading_id = _insert_reading(conn, state, machine_id)
    conn.commit()
    return result, rul_store.persist_prediction(conn, result, reading_id=reading_id)


def _store_rows(conn, states, *, epoch=0, episode=0, machine_id=M):
    for state in states:
        reading_id = _insert_reading(conn, state, machine_id)
        conn.execute(
            """INSERT INTO predictions (reading_id, machine_id, timestamp, health_state, source,
                   health_epoch, health_episode)
               VALUES (?, ?, ?, ?, 'xjtu_rul', ?, ?)""",
            (reading_id, machine_id, NOW, state, epoch, episode),
        )
    conn.commit()


def test_predict_synced_restores_after_a_restart(tmp_path, conn):
    """F1: a cold predictor rehydrates the epoch and adopts the DB held level."""
    a = _make(tmp_path)
    for state in ["healthy", "healthy", "critical", "critical"]:
        _synced_persist(a, conn, state)
    assert health_epoch.current(conn, M).max_state == "critical"

    b = _make(tmp_path, "b")
    result = _synced(b, conn, "healthy")
    assert result["health_state"] == "critical"
    assert result["history_snapshots"] == 5
    assert (result["health_epoch"], result["health_episode"]) == (0, 0)


def test_predict_synced_follows_a_reset_made_elsewhere(tmp_path, conn):
    a = _make(tmp_path)
    for state in ["healthy", "healthy", "critical"]:
        _synced_persist(a, conn, state)
    health_epoch.reset_machine_health(conn, M, reason="test", now=NOW)

    result = _synced(a, conn, "critical")
    assert result["history_snapshots"] == 1
    assert result["health_state"] == "healthy"
    assert result["commissioning"] == {"seen": 1, "of": 2}
    assert (result["health_epoch"], result["health_episode"]) == (1, 1)


def test_predict_synced_follows_a_rearm_made_elsewhere(tmp_path, conn):
    a = _make(tmp_path)
    for state in ["healthy", "healthy", "critical"]:
        _synced_persist(a, conn, state)
    with live._TRANSITION_LOCK:
        health_epoch._rearm_locked(conn, M, now=NOW)
        conn.commit()

    result = _synced(a, conn, "healthy")
    assert result["history_snapshots"] == 4  # the baseline and history are kept
    assert result["health_state"] == "healthy"
    assert result["commissioning"] is None
    assert (result["health_epoch"], result["health_episode"]) == (0, 1)
    assert _synced(a, conn, "critical")["health_state"] == "critical"


def test_predict_synced_survives_a_rehydrate_failure(tmp_path, conn, monkeypatch):
    a = _make(tmp_path)
    for state in ["healthy", "healthy", "critical"]:
        _synced_persist(a, conn, state)
    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("corrupt history")

    monkeypatch.setattr(rul_store, "rehydrate", boom)
    b = _make(tmp_path, "b")
    result = _synced(b, conn, "healthy")
    assert result["health_epoch"] == 0
    assert result["history_snapshots"] == 1
    assert result["health_state"] == "critical"  # the DB held level is still adopted
    _synced(b, conn, "healthy")
    assert len(calls) == 1


def test_model_change_rederives_the_held_level(tmp_path, conn):
    _store_rows(conn, ["critical"] * 6)
    health_epoch.record_level(conn, M, 0, "critical", "old")
    conn.commit()

    new = _make(tmp_path, classifiers=[_NeverLate()], model_version="new")
    result = _synced(new, conn, "healthy")
    row = health_epoch.current(conn, M)
    assert (row.max_state, row.model_version) == ("healthy", "new")
    assert result["health_state"] == "healthy"
    assert result["history_snapshots"] == 7


def test_model_change_keeps_the_level_of_an_open_alert(tmp_path, conn):
    _store_rows(conn, ["critical"] * 6)
    health_epoch.record_level(conn, M, 0, "critical", "old")
    alert_id = _insert_alert(conn, M, state="critical")

    new = _make(tmp_path, classifiers=[_NeverLate()], model_version="new")
    result = _synced(new, conn, "healthy")
    row = health_epoch.current(conn, M)
    assert (row.max_state, row.model_version) == ("critical", "new")
    assert result["health_state"] == "critical"
    assert conn.execute("SELECT status FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0] == "open"


def test_model_change_rederives_only_the_current_episode(tmp_path, conn):
    """Rows of an earlier episode (before a re-arm) never set the level."""
    _store_rows(conn, ["healthy", "healthy", "critical"], episode=0)
    _store_rows(conn, ["degrading"], episode=1)
    conn.execute(
        "INSERT INTO machine_health_state (machine_id, epoch, episode, max_state, model_version, "
        "updated_at) VALUES (?, 0, 1, 'critical', 'old', ?)", (M, NOW))
    conn.commit()

    _synced(_make(tmp_path, model_version="new"), conn, "healthy")
    row = health_epoch.current(conn, M)
    assert (row.episode, row.max_state, row.model_version) == (1, "degrading", "new")


def test_null_model_version_seed_is_kept_and_adopted(tmp_path, conn):
    conn.execute(
        "INSERT INTO machine_health_state (machine_id, epoch, episode, max_state, updated_at) "
        "VALUES (?, 0, 0, 'critical', ?)", (M, NOW))
    conn.commit()

    result = _synced(_make(tmp_path), conn, "healthy")
    assert result["health_state"] == "critical"
    row = health_epoch.current(conn, M)
    assert (row.max_state, row.model_version) == ("critical", "test-model")


def test_predict_synced_without_restore_health_just_calls(conn):
    class Fake:
        pass

    assert health_epoch.predict_synced(Fake(), conn, M, lambda: "called") == "called"


def test_predict_synced_without_the_table_just_calls(tmp_path, conn):
    conn.execute("DROP TABLE machine_health_state")
    conn.commit()
    assert _synced(_make(tmp_path), conn, "healthy")["health_epoch"] is None


def test_predict_synced_rehydrate_is_bounded(tmp_path, conn):
    """A 3,000-row epoch costs at most baseline_window + max_history
    classifier calls to rehydrate (D5), plus the one prediction."""
    _store_rows(conn, ["healthy"] * 3000)
    classifier = _AlwaysLate()
    cold = _make(tmp_path, classifiers=[classifier], baseline_window=20)
    cold.classifiers = [classifier]  # the unpickled copy would not count
    result = _synced(cold, conn, "healthy")
    assert result["history_snapshots"] == 3001
    assert classifier.calls <= 20 + cold.max_history + 1
