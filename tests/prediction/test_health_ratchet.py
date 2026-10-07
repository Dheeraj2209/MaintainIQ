"""Predictor-side health ratchet (docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md,
Task 3, decisions D3, D4, D8, D13).

The stub classifier always calls late life and the stub regressor returns the
row's ``scripted_rul`` feature, so each base dict picks its own instant state:
900 -> healthy, 500 -> degrading, 100 -> faulty, 10 -> critical (default
thresholds, horizon 1000). Smoothing and persistence are 1, so there is no lag.
"""
import joblib
import numpy as np
import pytest

from src.prediction.rul_realtime import RealTimeRULPredictor, StaleEpoch
from src.training.xjtu_rul import ROLLING_SOURCE_COLUMNS

RUL = {"healthy": 900.0, "degrading": 500.0, "faulty": 100.0, "critical": 10.0}
M = "b1"


class _AlwaysLate:
    def predict_proba(self, X):
        return np.tile([0.0, 1.0], (len(X), 1))


class _ScriptedRul:
    def predict(self, X):
        return X["scripted_rul"].to_numpy(dtype=float)


def _artifact(path, **overrides):
    artifact = {
        "classifiers": [_AlwaysLate()],
        "regressor": _ScriptedRul(),
        "classifier_feature_columns": ["speed_rpm", "h_kurtosis"],
        "regressor_feature_columns": ["scripted_rul"],
        "feature_bounds_99pct": {"v_rms": (0.0, 2.0)},
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
    for key in [k for k, v in artifact.items() if v is None]:
        del artifact[key]
    joblib.dump(artifact, path)
    return path


def _predictor(tmp_path, *, ratchet=None, **overrides):
    return RealTimeRULPredictor(_artifact(tmp_path / "rul.joblib", **overrides), ratchet=ratchet)


def _base(state, *, ood=False):
    base = {column: 1.0 for column in ROLLING_SOURCE_COLUMNS}
    base["scripted_rul"] = RUL[state]
    if ood:
        base["v_rms"] = 5.0
    return base


def _feed(predictor, states, *, ood=False, machine_id=M):
    return [
        predictor._predict_from_base(
            machine_id, _base(state, ood=ood),
            sample_rate_hz=25_600.0, speed_rpm=2100.0, load_kn=12.0,
        )
        for state in states
    ]


def _has(result, prefix):
    return any(w.startswith(prefix) for w in result["warnings"])


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("MAINTAINIQ_HEALTH_RATCHET", raising=False)


def test_ratchet_holds_worst_state(tmp_path):
    predictor = _predictor(tmp_path)
    _feed(predictor, ["healthy", "healthy"])  # commissioning (baseline_window 2)
    results = _feed(predictor, ["faulty", "healthy", "critical", "degrading"])

    assert [r["health_state"] for r in results] == ["faulty", "faulty", "critical", "critical"]
    assert [r["instant_health_state"] for r in results] == [
        "faulty", "healthy", "critical", "degrading"]
    assert [r["health_state_held"] for r in results] == [False, True, False, True]
    assert [_has(r, "condition_receded:") for r in results] == [False, True, False, True]
    assert all(r["health_ratchet"] is True for r in results)
    assert all(r["commissioning"] is None for r in results)
    assert predictor._max_state[M] == "critical"


def test_commissioning_rows_never_latch(tmp_path):
    predictor = _predictor(tmp_path, baseline_window=5)
    results = _feed(predictor, ["critical"] * 5)

    for k, result in enumerate(results, start=1):
        assert result["commissioning"] == {"seen": k, "of": 5}
        assert result["health_state"] == "healthy"
        assert result["instant_health_state"] == "critical"
        assert _has(result, "commissioning:")
    assert M not in predictor._max_state
    results += _feed(predictor, ["critical"])
    assert results[5]["commissioning"] is None
    assert not _has(results[5], "commissioning:")
    assert results[5]["health_state"] == "critical"
    assert predictor._max_state[M] == "critical"


def test_reset_restarts_commissioning(tmp_path):
    predictor = _predictor(tmp_path)
    _feed(predictor, ["healthy", "healthy", "critical"])
    assert predictor._max_state[M] == "critical"

    predictor.reset_machine(M)
    assert M not in predictor._max_state
    first, second, third = _feed(predictor, ["critical", "critical", "degrading"])
    assert first["commissioning"] == {"seen": 1, "of": 2}
    assert first["health_state"] == "healthy"
    assert second["commissioning"] == {"seen": 2, "of": 2}
    assert third["commissioning"] is None
    assert third["health_state"] == "degrading"


def test_ood_rows_do_not_latch(tmp_path):
    predictor = _predictor(tmp_path, baseline_window=5)
    _feed(predictor, ["healthy"] * 6)
    ood = _feed(predictor, ["critical"] * 3, ood=True)

    for result in ood:
        assert result["out_of_distribution"] is True
        assert result["health_state"] == "critical"
        assert _has(result, "ood_not_latched:")
        assert result["health_state_held"] is False
    assert predictor._max_state.get(M, "healthy") == "healthy"

    (after,) = _feed(predictor, ["healthy"])
    assert after["health_state"] == "healthy"
    assert not _has(after, "ood_not_latched:")
    assert predictor._max_state.get(M, "healthy") == "healthy"


def test_ood_row_does_not_lower_held(tmp_path):
    predictor = _predictor(tmp_path)
    _feed(predictor, ["healthy", "healthy", "faulty"])
    (result,) = _feed(predictor, ["healthy"], ood=True)

    assert result["health_state"] == "faulty"
    assert result["instant_health_state"] == "healthy"
    assert result["health_state_held"] is True
    assert _has(result, "condition_receded:")
    assert not _has(result, "ood_not_latched:")
    assert predictor._max_state[M] == "faulty"


@pytest.mark.parametrize("how", ["kwarg", "env", "artifact"])
def test_ratchet_off_is_legacy(tmp_path, monkeypatch, how):
    if how == "kwarg":
        predictor = _predictor(tmp_path, ratchet=False)
    elif how == "env":
        monkeypatch.setenv("MAINTAINIQ_HEALTH_RATCHET", "off")
        predictor = _predictor(tmp_path)
    else:
        predictor = _predictor(tmp_path, health_ratchet=False)
    assert predictor.ratchet is False

    states = ["critical", "healthy", "faulty", "healthy", "critical", "degrading"]
    results = _feed(predictor, states[:4]) + _feed(predictor, states[4:], ood=True)
    assert [r["health_state"] for r in results] == states
    assert [r["instant_health_state"] for r in results] == states
    for result in results:
        assert result["health_ratchet"] is False
        assert result["health_state_held"] is False
        assert result["commissioning"] is None
        for prefix in ("commissioning:", "ood_not_latched:", "condition_receded:"):
            assert not _has(result, prefix)
    assert M not in predictor._max_state


def test_kwarg_beats_env_beats_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAINIQ_HEALTH_RATCHET", "1")
    assert _predictor(tmp_path, ratchet=False, health_ratchet=True).ratchet is False
    assert _predictor(tmp_path, health_ratchet=False).ratchet is True
    monkeypatch.setenv("MAINTAINIQ_HEALTH_RATCHET", "no")
    assert _predictor(tmp_path, ratchet=True, health_ratchet=True).ratchet is True
    assert _predictor(tmp_path, health_ratchet=True).ratchet is False
    monkeypatch.delenv("MAINTAINIQ_HEALTH_RATCHET")
    assert _predictor(tmp_path, health_ratchet=False).ratchet is False


def test_invalid_env_value_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAINIQ_HEALTH_RATCHET", "maybe")
    with pytest.raises(ValueError, match="MAINTAINIQ_HEALTH_RATCHET"):
        _predictor(tmp_path)


def test_default_is_on_without_artifact_key(tmp_path):
    predictor = _predictor(tmp_path)
    assert predictor.ratchet is True
    (result,) = _feed(predictor, ["healthy"])
    assert result["health_ratchet"] is True


def test_result_carries_epoch_and_episode_none_when_fresh(tmp_path):
    (result,) = _feed(_predictor(tmp_path), ["healthy"])
    assert result["health_epoch"] is None
    assert result["health_episode"] is None


def test_reset_mid_prediction_raises_stale_epoch_and_leaves_state_alone(tmp_path):
    """A reset landing between the locked sections (D4) must not let the old
    prediction mutate the fresh machine's deques or held level."""
    predictor = _predictor(tmp_path)
    _feed(predictor, ["healthy", "healthy", "faulty"])

    class _ResetDuringPredict:
        def predict_proba(self, X):
            predictor.reset_machine(M)
            return np.tile([0.0, 1.0], (len(X), 1))

    predictor.classifiers = [_ResetDuringPredict()]
    with pytest.raises(StaleEpoch):
        _feed(predictor, ["critical"])
    assert M not in predictor._probability_history
    assert M not in predictor._warning_history
    assert M not in predictor._max_state


def test_stale_epoch_is_a_runtime_error():
    assert issubclass(StaleEpoch, RuntimeError)
