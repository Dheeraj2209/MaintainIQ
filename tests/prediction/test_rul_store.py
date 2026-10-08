import json

import pytest

from src.prediction import rul_store


def _result(**overrides):
    base = {
        "machine_id": "m1",
        "predicted_rul_minutes": 42.0,
        "predicted_rul_hours": 0.7,
        "rul_estimate_kind": "point_estimate",
        "prognostic_horizon_minutes": 120.0,
        "failure_within_horizon_probability": 0.83,
        "prediction_interval_90_minutes": [30.0, 54.0],
        "health_state": "faulty",
        "model_version": "test-model",
        "history_snapshots": 61,
        "out_of_distribution": True,
        "warnings": ["warming_up: 3/60 snapshots", "rul_lower_bound: ..."],
    }
    base.update(overrides)
    return base


def test_persist_prediction_maps_all_columns(conn):
    row_id = rul_store.persist_prediction(conn, _result(), reading_id=7)
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (row_id,)).fetchone()
    assert row["machine_id"] == "m1"
    assert row["reading_id"] == 7
    assert row["source"] == "xjtu_rul"
    assert row["health_state"] == "faulty"
    assert row["predicted_rul_minutes"] == 42.0
    assert row["rul_estimate_kind"] == "point_estimate"
    assert row["failure_within_horizon_probability"] == 0.83
    assert row["prognostic_horizon_minutes"] == 120.0
    assert row["prediction_interval_low"] == 30.0
    assert row["prediction_interval_high"] == 54.0
    assert row["model_version"] == "test-model"
    assert row["out_of_distribution"] == 1
    assert row["history_snapshots"] == 61
    assert json.loads(row["warnings_json"]) == _result()["warnings"]
    assert row["timestamp"]  # set to a non-empty ISO timestamp


def test_persist_prediction_handles_lower_bound_open_interval(conn):
    row_id = rul_store.persist_prediction(
        conn, _result(prediction_interval_90_minutes=[120.0, None], out_of_distribution=False)
    )
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (row_id,)).fetchone()
    assert row["prediction_interval_low"] == 120.0
    assert row["prediction_interval_high"] is None
    assert row["reading_id"] is None
    assert row["out_of_distribution"] == 0


def test_log_inference_success_row(conn):
    rul_store.log_inference(
        conn, machine_id="m1", model_version="test-model", latency_ms=12.5, result=_result()
    )
    row = conn.execute("SELECT * FROM model_inference_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "ok"
    assert row["error_message"] is None
    assert row["latency_ms"] == 12.5
    assert row["failure_probability"] == 0.83
    assert row["predicted_rul_minutes"] == 42.0
    assert row["out_of_distribution"] == 1
    assert row["warming_up"] == 1
    assert row["warnings_count"] == 2


def test_log_inference_error_row(conn):
    rul_store.log_inference(
        conn, machine_id="m1", model_version="test-model", latency_ms=3.0, error="bad input"
    )
    row = conn.execute("SELECT * FROM model_inference_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "error"
    assert row["error_message"] == "bad input"
    assert row["failure_probability"] is None
    assert row["predicted_rul_minutes"] is None
    assert row["warming_up"] == 0
    assert row["warnings_count"] == 0


def test_register_active_model_upserts_and_is_idempotent(conn):
    rul_store.register_active_model(
        conn, model_version="v1", artifact_path="models/xjtu_rul_model.joblib", algorithm="extratrees"
    )
    rul_store.register_active_model(
        conn, model_version="v1", artifact_path="models/xjtu_rul_model.joblib", algorithm="extratrees"
    )
    rows = conn.execute("SELECT * FROM model_registry").fetchall()
    assert len(rows) == 1
    assert rows[0]["artifact_path"] == "models/xjtu_rul_model.joblib"
    assert rows[0]["is_active"] == 1
    assert rows[0]["deployed_at"]


def test_register_active_model_deactivates_previous(conn):
    rul_store.register_active_model(conn, model_version="v1", artifact_path="a")
    rul_store.register_active_model(conn, model_version="v2", artifact_path="b")
    active = conn.execute("SELECT model_version FROM model_registry WHERE is_active = 1").fetchall()
    assert [r["model_version"] for r in active] == ["v2"]


import json as _json

import numpy as np

from src.ingestion.xjtu_sy import extract_snapshot_features
from src.prediction.rul_realtime import RealTimeRULPredictor
from src.storage.db import init_schema, insert_machines, insert_readings


class _AlwaysLate:
    def predict_proba(self, X):
        return np.tile([0.0, 1.0], (len(X), 1))


class _ThirtyMin:
    def predict(self, X):
        return np.full(len(X), 30.0)


def _fake_artifact(path, **overrides):
    import joblib

    artifact = {
        "classifiers": [_AlwaysLate(), _AlwaysLate()],
        "regressor": _ThirtyMin(),
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
    }
    artifact.update(overrides)
    joblib.dump(artifact, path)
    return path


def _signal(cycle, n=256, sr=25_600.0):
    t = np.arange(n) / sr
    amp = 1.0 + 0.05 * cycle
    return amp * np.sin(2 * np.pi * 1000 * t), amp * np.cos(2 * np.pi * 1000 * t)


def _fresh_db(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "rehydrate.db")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    insert_machines(conn, [{"machine_id": "b1", "dataset": "xjtu_sy"}])
    return conn


def _persist_reading(conn, *, cycle, base, epoch, episode=None, speed=2100.0, load=12.0,
                     sr=25_600.0, features_json=None, machine_id="b1", state="healthy",
                     commit=True):
    """One stored reading plus the prediction row (stamped with `epoch`) made
    from it, the way the ingest/replay callers persist them."""
    features = _json.dumps(base) if features_json is None else features_json
    cur = conn.execute(
        """INSERT INTO readings (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm,
               load_kn, sample_rate_hz, features_json, dataset,
               vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
               vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'xjtu_sy', 0, 0, 0, 0, 0, 0)""",
        (machine_id, f"2020-01-01T00:00:{cycle:06d}", cycle, float(cycle), speed, load, sr, features),
    )
    conn.execute(
        """INSERT INTO predictions (reading_id, machine_id, timestamp, health_state, source,
               health_epoch, health_episode)
           VALUES (?, ?, ?, ?, 'xjtu_rul', ?, ?)""",
        (cur.lastrowid, machine_id, f"2020-01-01T00:00:{cycle:06d}", state, epoch,
         epoch if episode is None else episode),
    )
    if commit:
        conn.commit()


def test_rehydrate_reproduces_next_prediction_health_state(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib")
    n_history = 8
    sr, speed, load = 25_600.0, 2100.0, 12.0

    # Warm predictor A: process n_history snapshots; persist each reading and
    # its episode-0 prediction row the way the callers do.
    conn = _fresh_db(tmp_path)
    warm = RealTimeRULPredictor(art)
    warm.restore_health("b1", epoch=0, episode=0, max_state="healthy")
    for cycle in range(n_history):
        h, v = _signal(cycle)
        result = warm.predict("b1", h, v, sr, speed, load)
        base = extract_snapshot_features(h, v, sr)
        cur = conn.execute(
            """INSERT INTO readings (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm,
                   load_kn, sample_rate_hz, features_json, dataset,
                   vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
                   vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation)
               VALUES ('b1', ?, ?, ?, ?, ?, ?, ?, 'xjtu_sy', 0, 0, 0, 0, 0, 0)""",
            (f"2020-01-01T00:{cycle:02d}:00+00:00", cycle, float(cycle), speed, load, sr,
             _json.dumps(base)),
        )
        conn.commit()
        assert rul_store.persist_prediction(conn, result, reading_id=cur.lastrowid) is not None
    held = conn.execute(
        "SELECT max_state FROM machine_health_state WHERE machine_id = 'b1'").fetchone()
    held = held[0] if held else "healthy"

    # Cold predictor B: rehydrate the epoch, then adopt the DB held level.
    cold = RealTimeRULPredictor(art)
    replayed = rul_store.rehydrate(cold, conn, "b1", epoch=0)
    assert replayed == n_history
    cold.restore_health("b1", epoch=0, episode=0, max_state=held)

    # Next snapshot fed to both must agree.
    h_next, v_next = _signal(n_history)
    warm_next = warm.predict("b1", h_next, v_next, sr, speed, load)
    cold_next = cold.predict("b1", h_next, v_next, sr, speed, load)
    assert cold_next["health_state"] == warm_next["health_state"]
    assert cold_next["predicted_rul_minutes"] == warm_next["predicted_rul_minutes"]
    assert cold_next["history_snapshots"] == warm_next["history_snapshots"]
    conn.close()


def test_rehydrate_skips_readings_without_feature_vector(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib")
    conn = _fresh_db(tmp_path)
    _persist_reading(conn, cycle=0, base={}, epoch=0, features_json="{}")
    cold = RealTimeRULPredictor(art)
    assert rul_store.rehydrate(cold, conn, "b1", epoch=0) == 0
    conn.close()


# --- epoch-scoped, bounded rehydrate (plan Task 5, D5) ----------------------

from src.training.xjtu_rul import ROLLING_SOURCE_COLUMNS


class _KurtosisDriven:
    """Failure probability follows h_kurtosis, so a prediction depends on the
    smoothing/persistence deques and the rolling history, not a constant."""

    def predict_proba(self, X):
        p = np.clip(X["h_kurtosis"].to_numpy(dtype=float) - 3.0, 0.0, 1.0)
        return np.column_stack([1.0 - p, p])


def _base(i):
    """A synthetic snapshot feature vector: every rolling source column, with
    h_kurtosis stepping up and down so the failure probability flaps."""
    base = {column: 1.0 + 0.001 * i + 0.01 * (n % 7)
            for n, column in enumerate(ROLLING_SOURCE_COLUMNS)}
    base["h_kurtosis"] = 3.0 + ((i // 7) % 3)
    return base


def test_rehydrate_replays_only_the_given_epoch(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib")
    conn = _fresh_db(tmp_path)
    for i in range(6):          # the old bearing
        _persist_reading(conn, cycle=i, base=_base(i), epoch=0)
    for i in range(4):          # the new bearing
        _persist_reading(conn, cycle=100 + i, base=_base(i), epoch=1)
    _persist_reading(conn, cycle=200, base=_base(0), epoch=None)  # pre-ratchet row

    cold = RealTimeRULPredictor(art)
    assert rul_store.rehydrate(cold, conn, "b1", epoch=1) == 4
    nxt = cold._predict_from_base("b1", _base(4), sample_rate_hz=25_600.0,
                                  speed_rpm=2100.0, load_kn=12.0)
    assert nxt["history_snapshots"] == 5
    conn.close()


def test_rehydrate_is_bounded_and_matches_a_warm_predictor(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib", classifiers=[_KurtosisDriven()],
                         baseline_window=5)
    conn = _fresh_db(tmp_path)
    kwargs = dict(sample_rate_hz=25_600.0, speed_rpm=2100.0, load_kn=12.0)
    # max_history cannot go below max(ROLLING_WINDOWS) = 60 (the predictor
    # rejects it), so the bound is baseline_window + 60 = 65 replays.
    n_rows, max_history = 300, 60

    warm = RealTimeRULPredictor(art, max_history=max_history)
    warm.restore_health("b1", epoch=1, episode=1, max_state="healthy")
    for i in range(n_rows):
        warm._predict_from_base("b1", _base(i), **kwargs)
        _persist_reading(conn, cycle=i, base=_base(i), epoch=1, commit=False)
    conn.commit()

    cold = RealTimeRULPredictor(art, max_history=max_history)
    calls = []
    real = cold._predict_from_base

    def spy(*args, **kw):
        calls.append(args[0])
        return real(*args, **kw)

    cold._predict_from_base = spy
    # All 65 bounded rows rebuild the feature history, but only the last
    # smoothing + persistence - 1 = 5 run the model: the probability and
    # warning deques are the only model-derived state, and the held level is
    # restored from the DB afterwards. Running the model on every row made
    # each machine's first prediction after a restart take 30-45 s.
    assert rul_store.rehydrate(cold, conn, "b1", epoch=1) == 5 + max_history
    assert len(calls) == 3 + 3 - 1
    held = warm._max_state.get("b1", "healthy")
    cold.restore_health("b1", epoch=1, episode=1, max_state=held)
    warm.restore_health("b1", epoch=1, episode=1, max_state=held)

    states = set()
    for i in range(n_rows, n_rows + 10):
        w = warm._predict_from_base("b1", _base(i), **kwargs)
        c = real("b1", _base(i), **kwargs)
        states.add(w["instant_health_state"])
        for key in ("history_snapshots", "health_state", "instant_health_state",
                    "failure_within_horizon_probability", "predicted_rul_minutes",
                    "warnings"):
            assert c[key] == w[key], key
    assert len(states) > 1  # the comparison covered a state change
    conn.close()


def test_rehydrate_full_replays_the_whole_epoch(tmp_path):
    art = _fake_artifact(tmp_path / "rul.joblib", baseline_window=5)
    conn = _fresh_db(tmp_path)
    for i in range(80):
        _persist_reading(conn, cycle=i, base=_base(i), epoch=1, commit=False)
    conn.commit()
    cold = RealTimeRULPredictor(art)
    assert rul_store.rehydrate(cold, conn, "b1", epoch=1, full=True) == 80
    conn.close()


def test_rehydrate_skips_corrupt_rows_and_logs_them(tmp_path, caplog):
    art = _fake_artifact(tmp_path / "rul.joblib")
    conn = _fresh_db(tmp_path)
    missing = _base(1)
    del missing["h_rms"]
    _persist_reading(conn, cycle=0, base=_base(0), epoch=0)
    _persist_reading(conn, cycle=1, base=missing, epoch=0)
    _persist_reading(conn, cycle=2, base={}, epoch=0, features_json="{")
    _persist_reading(conn, cycle=3, base=_base(3), epoch=0)

    cold = RealTimeRULPredictor(art)
    with caplog.at_level("WARNING", logger="src.prediction.rul_store"):
        assert rul_store.rehydrate(cold, conn, "b1", epoch=0) == 2
    assert "h_rms" in caplog.text
    assert "unparsable" in caplog.text
    # The cycle count still follows the epoch's prediction rows.
    nxt = cold._predict_from_base("b1", _base(4), sample_rate_hz=25_600.0,
                                  speed_rpm=2100.0, load_kn=12.0)
    assert nxt["history_snapshots"] == 5
    conn.close()
