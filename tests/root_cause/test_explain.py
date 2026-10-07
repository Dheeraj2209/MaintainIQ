"""Tests for src/root_cause/explain.py — the "Why this alert?" builder
(design/2026-10-07-alert-explanation-design.md, decisions 3-11).

Every test runs on an in-memory DB at the latest migration with its own
machines (mx, my), and passes `predictor=` explicitly (or None) so a real
model that an earlier test cached in predictions._cached_predictor cannot
leak importances in (design "Risks").
"""
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.root_cause import explain
from src.storage.migrations import run_migrations
from src.training.xjtu_rul import BASELINE_WINDOW, ROLLING_SOURCE_COLUMNS, ROLLING_WINDOWS

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
VIBRATION_KEYS = {"vibration_h_rms", "vibration_h_kurtosis", "vibration_v_rms",
                  "vibration_v_kurtosis", "cross_axis_rms_ratio", "cross_axis_correlation"}


def _ts(minutes: float) -> str:
    return (START + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    for machine in ("mx", "my", "mz"):
        conn.execute("INSERT INTO machines (machine_id) VALUES (?)", (machine,))
    conn.commit()
    yield conn
    conn.close()


def _features(scale: dict | None = None) -> dict:
    values = {column: 1.0 for column in ROLLING_SOURCE_COLUMNS}
    for column in ROLLING_SOURCE_COLUMNS:
        if "kurtosis" in column:
            values[column] = 3.0
    for column, factor in (scale or {}).items():
        values[column] *= factor
    return values


def _reading(conn, machine, i, *, features=None, full=True, promoted=None):
    feats = features if features is not None else _features()
    promoted = promoted or {}
    cur = conn.execute(
        """INSERT INTO readings (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm, load_kn,
               sample_rate_hz, vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
               vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation,
               rul_minutes, features_json, dataset)
           VALUES (?, ?, ?, ?, 2100.0, 12.0, 25600.0, ?, ?, ?, ?, 1.0, 0.5, NULL, ?, 'xjtu_sy')""",
        (machine, _ts(i), i, float(i),
         promoted.get("vibration_h_rms", feats.get("h_rms", 1.0)),
         promoted.get("vibration_h_kurtosis", feats.get("h_kurtosis", 3.0)),
         promoted.get("vibration_v_rms", feats.get("v_rms", 1.0)),
         promoted.get("vibration_v_kurtosis", feats.get("v_kurtosis", 3.0)),
         json.dumps(feats) if full else "{}"),
    )
    conn.commit()
    return cur.lastrowid


def _seed(conn, machine="mx", n=25, *, trigger=None, full=True):
    """n readings, the last one (the trigger) scaled by `trigger`."""
    ids = []
    for i in range(n):
        last = i == n - 1
        feats = _features(trigger if last else None)
        ids.append(_reading(conn, machine, i, features=feats, full=full))
    return ids


def _alert(conn, machine="mx", opened_at=None, *, severity="high", health_state="critical",
           probable_cause="bearing_wear", status="open", source="xjtu_rul", reading_id=None,
           prediction_id=None, resolved_at=None):
    cur = conn.execute(
        """INSERT INTO alerts (machine_id, opened_at, resolved_at, severity, health_state,
               probable_cause, message, status, source, created_at, reading_id, prediction_id)
           VALUES (?, ?, ?, ?, ?, ?, 'm', ?, ?, ?, ?, ?)""",
        (machine, opened_at or _ts(100), resolved_at, severity, health_state, probable_cause,
         status, source, _ts(0), reading_id, prediction_id),
    )
    conn.commit()
    return cur.lastrowid


def _get_alert(conn, alert_id):
    from src.alerts.live import get_alert

    return get_alert(conn, alert_id)


def _prediction(conn, machine="mx", ts=None, *, ood=1, warnings=("out_of_distribution: far",),
                model_name=None, prob=0.83, rul=42.0):
    cur = conn.execute(
        """INSERT INTO predictions (machine_id, reading_id, timestamp, health_state, source,
               model_name, predicted_rul_minutes, rul_estimate_kind,
               failure_within_horizon_probability, prognostic_horizon_minutes,
               prediction_interval_low, prediction_interval_high, model_version,
               out_of_distribution, warnings_json)
           VALUES (?, NULL, ?, 'critical', 'xjtu_rul', ?, ?, 'point_estimate', ?, 120.0,
                   12.0, 72.0, 'v-test', ?, ?)""",
        (machine, ts or _ts(24), model_name, rul, prob, ood, json.dumps(list(warnings))),
    )
    conn.commit()
    return cur.lastrowid


# --- fake predictor ------------------------------------------------------------------

class _Estimator:
    def __init__(self, importances):
        self.feature_importances_ = np.asarray(importances, dtype=float)


class _Pipe:
    def __init__(self, importances):
        self.named_steps = {"imputer": object(), "classifier": _Estimator(importances)}


class FakePredictor:
    def __init__(self, importances: dict, bounds=None, version="fake-v1", columns=None):
        self.classifier_feature_columns = columns or list(importances)
        vector = [importances.get(c, 0.0) for c in self.classifier_feature_columns]
        self.classifiers = [_Pipe(vector), _Pipe(vector)]
        self.artifact = {"model_version": version, "feature_bounds_99pct": bounds or {}}


# --- triggering readings (decision 5) ------------------------------------------------

def test_triggering_window_by_reading_id(db):
    ids = _seed(db, n=40)
    alert_id = _alert(db, reading_id=ids[34], opened_at=_ts(34))
    body = explain.build_snapshot(db, _get_alert(db, alert_id), reading_id=ids[34], predictor=None)
    section = body["triggering_readings"]
    assert section["locate"] == "reading_id"
    assert section["trigger_reading_id"] == ids[34]
    assert section["trigger_timestamp"] == _ts(34)
    readings = section["readings"]
    assert len(readings) == explain.TRIGGER_WINDOW_READINGS == 30
    assert [r["reading_id"] for r in readings] == ids[5:35]
    assert [r["is_trigger"] for r in readings] == [False] * 29 + [True]
    for r in readings:
        assert set(r) == VIBRATION_KEYS | {"reading_id", "timestamp", "cycle", "is_trigger"}
    assert {c["key"] for c in section["channels"]} == VIBRATION_KEYS
    dumped = json.dumps(section)
    assert "temperature" not in dumped and "speed_rpm" not in dumped


def test_nearest_timestamp_fallback(db):
    _seed(db, n=10)
    alert_id = _alert(db, opened_at=_ts(5.5))
    body = explain.build_snapshot(db, _get_alert(db, alert_id), predictor=None)
    section = body["triggering_readings"]
    assert section["locate"] == "nearest_timestamp"
    assert section["trigger_timestamp"] == _ts(5)
    assert any("nearest" in n.lower() for n in body["notes"])

    early = _alert(db, "mx", opened_at=(START - timedelta(days=1)).isoformat(), status="resolved")
    section = explain.build_snapshot(db, _get_alert(db, early), predictor=None)["triggering_readings"]
    assert section["trigger_timestamp"] == _ts(0)
    assert len(section["readings"]) == 1


def test_reading_less_snapshot_appends_an_unsaved_trigger(db):
    _seed(db, n=10)
    alert_id = _alert(db, opened_at=_ts(7.5))
    features = _features({"h_kurtosis": 3.0})
    body = explain.build_snapshot(db, _get_alert(db, alert_id), features=features, at=_ts(7.5),
                                  predictor=None)
    readings = body["triggering_readings"]["readings"]
    assert [r["timestamp"] for r in readings[:-1]] == [_ts(i) for i in range(8)]
    last = readings[-1]
    assert last["reading_id"] is None and last["is_trigger"] is True
    assert last["timestamp"] == _ts(7.5)
    assert last["vibration_h_kurtosis"] == pytest.approx(9.0)
    assert body["triggering_readings"]["trigger_reading_id"] is None


def test_escalation_features_beat_the_alerts_opening_reading(db):
    # POST /api/predictions/rul escalates with features but no reading: the
    # escalated snapshot must describe that vector, not alerts.reading_id.
    ids = _seed(db, n=10)
    alert_id = _alert(db, reading_id=ids[6], opened_at=_ts(6))
    features = _features({"h_kurtosis": 3.0})
    body = explain.build_snapshot(db, _get_alert(db, alert_id), reading_id=None, features=features,
                                  at=_ts(9.5), predictor=None)
    section = body["triggering_readings"]
    assert section["locate"] == "snapshot_features"
    assert section["trigger_reading_id"] is None
    assert section["readings"][-1]["vibration_h_kurtosis"] == pytest.approx(9.0)
    assert body["probable_cause"]["inputs_source"] == "snapshot_features"


def _link_prediction(conn, pid, reading_id):
    conn.execute("UPDATE predictions SET reading_id = ? WHERE id = ?", (reading_id, pid))
    conn.commit()


def test_reconstruction_uses_the_linked_predictions_reading(db):
    # Pre-v5 replay alert: no reading link, wall-clock opened_at long after
    # every (backfill-epoch) reading. The prediction's reading is the trigger.
    ids = _seed(db, n=60)
    pid = _prediction(db, ts=_ts(10_000))
    _link_prediction(db, pid, ids[31])
    alert_id = _alert(db, opened_at=_ts(10_000), prediction_id=pid)
    body = explain.build_snapshot(db, _get_alert(db, alert_id), predictor=None)
    section = body["triggering_readings"]
    assert section["locate"] == "prediction_reading"
    assert section["trigger_reading_id"] == ids[31]
    assert section["readings"][-1]["reading_id"] == ids[31]
    assert not any("nearest reading" in n for n in body["notes"])


def test_reconstruction_uses_the_nearest_predictions_reading(db):
    ids = _seed(db, n=60)
    pid = _prediction(db, ts=_ts(10_000))
    _link_prediction(db, pid, ids[31])
    later = _prediction(db, ts=_ts(20_000))  # after the alert: not used
    _link_prediction(db, later, ids[59])
    alert_id = _alert(db, opened_at=_ts(10_000))
    explanation = explain.get_explanation(db, alert_id, predictor=None)
    section = explanation["triggering_readings"]
    assert explanation["source"] == "reconstructed"
    assert section["locate"] == "prediction_reading"
    assert section["trigger_reading_id"] == ids[31]
    assert explanation["prediction"]["prediction_id"] == pid


def test_prediction_without_a_reading_falls_back_to_nearest_timestamp(db):
    _seed(db, n=10)
    _prediction(db, ts=_ts(5.5))  # reading_id NULL (POST /api/predictions/rul)
    alert_id = _alert(db, opened_at=_ts(5.5))
    section = explain.build_snapshot(db, _get_alert(db, alert_id), predictor=None)["triggering_readings"]
    assert section["locate"] == "nearest_timestamp"
    assert section["trigger_timestamp"] == _ts(5)


def test_no_readings_degrades(db):
    alert_id = _alert(db, "my")
    body = explain.build_snapshot(db, _get_alert(db, alert_id), predictor=None)
    assert body["triggering_readings"]["locate"] == "none"
    assert body["triggering_readings"]["readings"] == []
    assert body["key_factors"]["status"] == "no_readings"
    assert body["key_factors"]["factors"] == []
    assert body["probable_cause"]["display"].startswith("Probable cause: ")


# --- key factors (decision 6) --------------------------------------------------------

def _factors(db, n=25, trigger=None, predictor=None, full=True):
    ids = _seed(db, n=n, trigger=trigger, full=full)
    alert_id = _alert(db, reading_id=ids[-1], opened_at=_ts(n - 1))
    return explain.build_snapshot(db, _get_alert(db, alert_id), reading_id=ids[-1],
                                  predictor=predictor)["key_factors"]


def test_model_context_ranking_and_log_symmetry(db):
    kf = _factors(db, trigger={"h_kurtosis": 4.0, "v_rms": 0.5, "m_rms": 2.0})
    assert kf["status"] == "ok" and kf["method"] == "model_context"
    assert kf["weighting"] == "unweighted"
    assert kf["baseline_readings"] == BASELINE_WINDOW
    top = kf["factors"][0]
    assert top["feature"] == "h_kurtosis" and top["direction"] == "up"
    assert top["ratio"] == pytest.approx(4.0)
    assert top["value"] == pytest.approx(12.0) and top["baseline"] == pytest.approx(3.0)
    assert top["label"] == "Horizontal kurtosis" and top["axis"] == "horizontal"
    by = {f["feature"]: f for f in kf["factors"]}
    assert by["v_rms"]["direction"] == "down"
    assert by["v_rms"]["deviation"] == pytest.approx(by["m_rms"]["deviation"]) == pytest.approx(math.log(2))
    assert by["m_rms"]["axis"] == "combined" and by["v_rms"]["unit"] == "g"
    assert len(kf["factors"]) <= explain.KEY_FACTOR_LIMIT


def test_steady_channels_are_steady(db):
    kf = _factors(db, trigger={"h_kurtosis": 4.0})
    steady = [f for f in kf["factors"] if f["feature"] != "h_kurtosis"]
    assert all(f["direction"] == "steady" for f in steady)


def test_operating_conditions_are_excluded_from_ranking(db):
    predictor = FakePredictor({"speed_rpm": 0.4, "load_kn": 0.4, "h_kurtosis_baseline_ratio": 0.1,
                               "v_rms_mean_5": 0.1})
    kf = _factors(db, trigger={"h_kurtosis": 4.0, "v_rms": 2.0}, predictor=predictor)
    assert kf["weighting"] == "model_importance"
    names = {f["feature"] for f in kf["factors"]}
    assert "speed_rpm" not in names and "load_kn" not in names
    weighted = [f["importance"] for f in kf["factors"] if f["importance"]]
    assert sum(weighted) == pytest.approx(1.0)
    conditions = {c["feature"]: c for c in kf["operating_conditions"]}
    assert set(conditions) == {"speed_rpm", "load_kn"}
    assert conditions["speed_rpm"]["value"] == 2100.0 and conditions["load_kn"]["unit"] == "kN"


def test_importance_orders_equal_deviations_and_uses_longest_prefix(db):
    predictor = FakePredictor({"v_rms_baseline_ratio": 0.1, "h_envelope_rms_mean_5": 0.6,
                               "h_rms_std_5": 0.3})
    kf = _factors(db, trigger={"v_rms": 2.0, "h_envelope_rms": 2.0, "h_rms": 2.0},
                  predictor=predictor)
    assert [f["feature"] for f in kf["factors"][:3]] == ["h_envelope_rms", "h_rms", "v_rms"]
    by = {f["feature"]: f for f in kf["factors"]}
    assert by["h_envelope_rms"]["importance"] == pytest.approx(0.6)
    assert by["h_rms"]["importance"] == pytest.approx(0.3)
    assert by["h_rms"]["score"] == pytest.approx(math.log(2) * 0.3)


def test_no_predictor_is_unweighted(db):
    kf = _factors(db, trigger={"h_kurtosis": 4.0}, predictor=None)
    assert kf["weighting"] == "unweighted"
    for factor in kf["factors"]:
        assert factor["importance"] is None
        assert factor["score"] == pytest.approx(factor["deviation"])
        assert factor["outside_training_bounds"] is None
    assert all(c["outside_training_bounds"] is None for c in kf["operating_conditions"])


def test_training_bounds_flag(db):
    outside = FakePredictor({"h_kurtosis_baseline_ratio": 1.0},
                            bounds={"h_kurtosis_baseline_ratio": [0.5, 2.0], "speed_rpm": [1000, 3000]})
    kf = _factors(db, trigger={"h_kurtosis": 4.0}, predictor=outside)
    top = kf["factors"][0]
    assert top["feature"] == "h_kurtosis"
    assert top["outside_training_bounds"] is True
    assert "h_kurtosis_baseline_ratio" in top["out_of_bounds_features"]
    speed = next(c for c in kf["operating_conditions"] if c["feature"] == "speed_rpm")
    assert speed["outside_training_bounds"] is False


def test_training_bounds_inside(db):
    inside = FakePredictor({"h_kurtosis_baseline_ratio": 1.0},
                           bounds={"h_kurtosis_baseline_ratio": [0.5, 10.0]})
    kf = _factors(db, trigger={"h_kurtosis": 4.0}, predictor=inside)
    top = kf["factors"][0]
    assert top["outside_training_bounds"] is False and top["out_of_bounds_features"] == []


def test_insufficient_history(db):
    kf = _factors(db, n=5, trigger={"h_kurtosis": 4.0})
    assert kf["status"] == "insufficient_history"
    assert kf["factors"] == []


def test_promoted_columns_fallback(db):
    ids = [_reading(db, "mx", i, full=False) for i in range(6)]
    db.execute("UPDATE readings SET vibration_h_kurtosis = 12.0 WHERE id = ?", (ids[-1],))
    db.commit()
    alert_id = _alert(db, reading_id=ids[-1])
    kf = explain.build_snapshot(db, _get_alert(db, alert_id), reading_id=ids[-1],
                                predictor=FakePredictor({"h_kurtosis_mean_5": 1.0}))["key_factors"]
    assert kf["status"] == "ok" and kf["method"] == "promoted_columns"
    assert kf["baseline_readings"] == 5
    assert 0 < len(kf["factors"]) <= 4
    assert {f["feature"] for f in kf["factors"]} <= {"h_rms", "h_kurtosis", "v_rms", "v_kurtosis"}
    top = kf["factors"][0]
    assert top["feature"] == "h_kurtosis" and top["ratio"] == pytest.approx(4.0)
    assert all(f["outside_training_bounds"] is None for f in kf["factors"])


@pytest.mark.parametrize("predictor", [
    type("Legacy", (), {"classifiers": [object()], "classifier_feature_columns": ["h_rms"],
                        "artifact": {"model_version": "legacy"}})(),
    FakePredictor({"h_rms": 1.0}, columns=["h_rms", "v_rms", "extra"], version="mismatch"),
])
def test_malformed_classifiers_fall_back_to_unweighted(db, predictor):
    if isinstance(predictor, FakePredictor):  # importance vector shorter than the columns
        predictor.classifiers = [_Pipe([1.0])]
    kf = _factors(db, trigger={"h_kurtosis": 4.0}, predictor=predictor)
    assert kf["status"] == "ok" and kf["weighting"] == "unweighted"


def test_recent_slice_covers_the_longest_rolling_window():
    assert explain.RECENT_CONTEXT_READINGS == max(ROLLING_WINDOWS)


# --- prediction block (decision 7) ---------------------------------------------------

def test_prediction_by_id_decodes_ood_and_warnings(db):
    ids = _seed(db, n=3)
    pid = _prediction(db)
    alert_id = _alert(db, reading_id=ids[-1], prediction_id=pid)
    block = explain.build_snapshot(db, _get_alert(db, alert_id), predictor=None)["prediction"]
    assert block["locate"] == "prediction_id" and block["prediction_id"] == pid
    assert block["out_of_distribution"] is True
    assert block["warnings"] == ["out_of_distribution: far"]
    assert block["prediction_interval_low"] == 12.0 and block["prediction_interval_high"] == 72.0
    assert block["failure_within_horizon_probability"] == 0.83


def test_prediction_by_nearest_timestamp(db):
    pid = _prediction(db, ts=_ts(50))
    _prediction(db, ts=_ts(150))  # after the alert: not used
    alert_id = _alert(db, opened_at=_ts(100))
    block = explain.build_snapshot(db, _get_alert(db, alert_id), predictor=None)["prediction"]
    assert block["locate"] == "nearest_timestamp" and block["prediction_id"] == pid


def test_no_prediction_is_none_with_a_note(db):
    alert_id = _alert(db)
    body = explain.build_snapshot(db, _get_alert(db, alert_id), predictor=None)
    assert body["prediction"] is None
    assert any("prediction" in n.lower() for n in body["notes"])


def test_snapshot_prediction_keeps_outside_training_features(db):
    alert_id = _alert(db)
    result = {"machine_id": "mx", "health_state": "critical", "predicted_rul_minutes": 42.0,
              "rul_estimate_kind": "point_estimate", "failure_within_horizon_probability": 0.9,
              "prognostic_horizon_minutes": 120.0, "prediction_interval_90_minutes": [12.0, 72.0],
              "model_version": "v9", "out_of_distribution": True,
              "outside_training_features": ["h_kurtosis_baseline_ratio"], "warnings": ["w"]}
    block = explain.build_snapshot(db, _get_alert(db, alert_id), prediction=result, prediction_id=7,
                                   at=_ts(100), predictor=None)["prediction"]
    assert block["locate"] == "snapshot" and block["prediction_id"] == 7
    assert block["outside_training_features"] == ["h_kurtosis_baseline_ratio"]
    assert (block["prediction_interval_low"], block["prediction_interval_high"]) == (12.0, 72.0)
    assert block["model_version"] == "v9" and block["out_of_distribution"] is True


def test_demo_prediction_has_null_probability(db):
    _prediction(db, ts=_ts(99), model_name="demo_simulator", prob=None, rul=None, ood=0, warnings=())
    alert_id = _alert(db, source="demo")
    block = explain.build_snapshot(db, _get_alert(db, alert_id), predictor=None)["prediction"]
    assert block["failure_within_horizon_probability"] is None
    assert block["predicted_rul_minutes"] is None
    assert block["out_of_distribution"] is False


# --- probable cause block (decision 8) -----------------------------------------------

_FORBIDDEN = re.compile(r"\bdiagnos(is|ed)\b|root cause:", re.IGNORECASE)


def test_probable_cause_wording_and_sources(db):
    ids = _seed(db, n=6, trigger={"h_kurtosis": 4.0})
    alert_id = _alert(db, reading_id=ids[-1])
    alert = _get_alert(db, alert_id)
    body = explain.build_snapshot(db, alert, reading_id=ids[-1], predictor=None)
    block = body["probable_cause"]
    assert block["display"] == "Probable cause: bearing wear"
    assert "probable cause, not a diagnosis" in block["disclaimer"]
    assert block["inputs_source"] == "trigger_reading"
    assert block["rule"] == "high_kurtosis" and block["matches_alert"] is True
    serialized = json.dumps(body, ensure_ascii=False).replace(block["disclaimer"], "")
    assert not _FORBIDDEN.search(serialized)

    with_features = explain.build_snapshot(db, alert, features={"h_rms": 1.0, "h_kurtosis": 9.0},
                                           predictor=None)["probable_cause"]
    assert with_features["inputs_source"] == "snapshot_features"
    assert with_features["checks"][0]["value"] == 9.0


def test_probable_cause_disagreement_is_noted(db):
    ids = _seed(db, n=6)  # kurtosis 3.0 everywhere: the rule says imbalance
    alert_id = _alert(db, reading_id=ids[-1], probable_cause="bearing_wear")
    body = explain.build_snapshot(db, _get_alert(db, alert_id), reading_id=ids[-1], predictor=None)
    block = body["probable_cause"]
    assert block["label"] == "bearing_wear" and block["evaluated_label"] == "imbalance"
    assert block["matches_alert"] is False
    assert block["display"] == "Probable cause: bearing wear"
    assert any("differs" in n for n in body["notes"])


def test_section_failure_is_isolated(db, monkeypatch, caplog):
    ids = _seed(db, n=6)
    alert_id = _alert(db, reading_id=ids[-1])

    def boom(*args, **kwargs):
        raise RuntimeError("bad features_json")

    monkeypatch.setattr(explain, "_key_factors", boom)
    body = explain.build_snapshot(db, _get_alert(db, alert_id), reading_id=ids[-1], predictor=None)
    assert body["key_factors"]["status"] == "error"
    assert body["triggering_readings"]["locate"] == "reading_id"
    assert body["probable_cause"]["rule"]
    assert any("key factors" in n.lower() for n in body["notes"])
    assert "bad features_json" in caplog.text


# --- similar incidents (decision 9) --------------------------------------------------

def _feedback(conn, alert_id, outcome="maintenance_prevented", cause=None, notes=None):
    conn.execute("INSERT INTO alert_feedback (alert_id, outcome, actual_cause, notes, recorded_by, "
                 "recorded_at) VALUES (?, ?, ?, ?, 1, ?)", (alert_id, outcome, cause, notes, _ts(0)))
    conn.commit()


def test_similar_incidents_candidates_and_order(db):
    this = _alert(db, "mx", _ts(1000), probable_cause="bearing_wear", severity="high")
    same_cause_other_machine = _alert(db, "my", _ts(900), probable_cause="bearing_wear",
                                      severity="low", status="resolved")
    same_machine_other_cause = _alert(db, "mx", _ts(800), probable_cause="imbalance",
                                      severity="low", status="resolved")
    both = _alert(db, "mx", _ts(500), probable_cause="bearing_wear", severity="high",
                  status="resolved")
    unrelated = _alert(db, "mz", _ts(700), probable_cause="imbalance", status="resolved")
    later = _alert(db, "mx", _ts(1100), probable_cause="bearing_wear", status="resolved")

    incidents = explain.similar_incidents(db, _get_alert(db, this), limit=20)
    ids = [i["alert_id"] for i in incidents]
    assert ids == [both, same_cause_other_machine, same_machine_other_cause]
    assert this not in ids and later not in ids and unrelated not in ids
    assert set(incidents[0]["match_reasons"]) == {"same_probable_cause", "same_machine", "same_severity"}
    assert 0.9 < incidents[0]["similarity"] <= 1.0
    assert incidents[1]["match_reasons"] == ["same_probable_cause"]


def test_actual_cause_match_is_included(db):
    this = _alert(db, "mx", _ts(1000), probable_cause="bearing_wear")
    other = _alert(db, "mz", _ts(500), probable_cause="imbalance", severity="low",
                   status="resolved")
    _feedback(db, other, outcome="confirmed_failure", cause="bearing_wear")
    incidents = explain.similar_incidents(db, _get_alert(db, this))
    assert [i["alert_id"] for i in incidents] == [other]
    assert incidents[0]["match_reasons"] == ["actual_cause_matches"]
    assert incidents[0]["cause_confirmed"] is False  # it predicted imbalance


def test_feedback_work_orders_and_maintenance_are_joined(db):
    this = _alert(db, "mx", _ts(1000))
    confirmed = _alert(db, "mx", _ts(500), status="resolved")
    no_cause = _alert(db, "mx", _ts(400), status="resolved")
    plain = _alert(db, "mx", _ts(300), status="resolved")
    _feedback(db, confirmed, cause="bearing_wear", notes="x" * 400)
    _feedback(db, no_cause, outcome="false_alarm", cause="other")
    direct = db.execute("INSERT INTO maintenance_records (machine_id, performed_at, description, "
                        "technician, created_at, alert_id, type) VALUES ('mx', ?, 'swap', 'T', ?, ?, "
                        "'corrective')", (_ts(600), _ts(600), confirmed)).lastrowid
    via_wo = db.execute("INSERT INTO maintenance_records (machine_id, performed_at, created_at) "
                        "VALUES ('mx', ?, ?)", (_ts(610), _ts(610))).lastrowid
    db.execute("INSERT INTO work_orders (alert_id, machine_id, status, title, created_at, updated_at, "
               "completed_at, maintenance_record_id) VALUES (?, 'mx', 'done', 'Replace bearing', ?, ?, "
               "?, ?)", (confirmed, _ts(550), _ts(610), _ts(610), via_wo))
    db.execute("INSERT INTO work_orders (alert_id, machine_id, status, title, created_at, updated_at, "
               "maintenance_record_id) VALUES (?, 'mx', 'done', 'Second', ?, ?, ?)",
               (confirmed, _ts(560), _ts(620), direct))
    db.commit()

    incidents = {i["alert_id"]: i for i in explain.similar_incidents(db, _get_alert(db, this))}
    first = incidents[confirmed]
    assert first["feedback"]["outcome"] == "maintenance_prevented"
    assert first["feedback"]["actual_cause"] == "bearing_wear"
    assert len(first["feedback"]["notes"]) == 280
    assert first["cause_confirmed"] is True
    assert [w["title"] for w in first["work_orders"]] == ["Replace bearing", "Second"]
    assert sorted(m["id"] for m in first["maintenance"]) == sorted([direct, via_wo])
    assert incidents[no_cause]["cause_confirmed"] is None
    assert incidents[plain]["feedback"] is None and incidents[plain]["cause_confirmed"] is None
    assert incidents[plain]["work_orders"] == [] and incidents[plain]["maintenance"] == []


def test_similar_limit(db):
    this = _alert(db, "mx", _ts(1000))
    for i in range(4):
        _alert(db, "mx", _ts(100 + i), status="resolved")
    alert = _get_alert(db, this)
    assert len(explain.similar_incidents(db, alert, limit=2)) == 2
    assert explain.similar_incidents(db, alert, limit=0) == []


def test_synthetic_isolation(db):
    real = _alert(db, "mx", _ts(1000))
    demo_old = _alert(db, "mx", _ts(500), source="demo", status="resolved")
    real_old = _alert(db, "mx", _ts(400), status="resolved")
    demo_now = _alert(db, "my", _ts(1000), source="demo")

    real_ids = [i["alert_id"] for i in explain.similar_incidents(db, _get_alert(db, real))]
    assert real_ids == [real_old]

    demo_alert = _get_alert(db, demo_now)
    demo_list = {i["alert_id"]: i for i in explain.similar_incidents(db, demo_alert)}
    assert {demo_old, real_old} <= set(demo_list)
    assert demo_list[demo_old]["synthetic"] is True and demo_list[real_old]["synthetic"] is False
    body = explain.build_snapshot(db, demo_alert, predictor=None)
    assert body["synthetic"] is True


# --- record_snapshot / get_explanation (decisions 1, 4) -------------------------------

def test_record_snapshot_created_is_unique_escalated_is_not(db):
    ids = _seed(db, n=3)
    alert_id = _alert(db, reading_id=ids[-1])
    alert = _get_alert(db, alert_id)
    row_id = explain.record_snapshot(db, alert, "created", reading_id=ids[-1], prediction_id=None,
                                     predictor=None)
    assert row_id is not None
    row = db.execute("SELECT * FROM alert_explanations WHERE id = ?", (row_id,)).fetchone()
    assert (row["alert_id"], row["kind"], row["reading_id"]) == (alert_id, "created", ids[-1])
    assert json.loads(row["explanation_json"])["explanation_version"] == explain.EXPLANATION_VERSION
    assert "similar_incidents" not in json.loads(row["explanation_json"])

    assert explain.record_snapshot(db, alert, "created", predictor=None) is None
    assert explain.record_snapshot(db, alert, "escalated", reading_id=ids[0], predictor=None)
    assert explain.record_snapshot(db, alert, "escalated", predictor=None)
    kinds = [r[0] for r in db.execute("SELECT kind FROM alert_explanations ORDER BY id")]
    assert kinds == ["created", "escalated", "escalated"]


def test_record_snapshot_without_the_table(db):
    alert_id = _alert(db)
    db.execute("DROP TABLE alert_explanations")
    db.commit()
    assert explain.record_snapshot(db, _get_alert(db, alert_id), "created", predictor=None) is None


def test_get_explanation_serves_the_latest_snapshot(db):
    ids = _seed(db, n=6)
    alert_id = _alert(db, reading_id=ids[2])
    alert = _get_alert(db, alert_id)
    explain.record_snapshot(db, alert, "created", reading_id=ids[2], predictor=None)
    explain.record_snapshot(db, alert, "escalated", reading_id=ids[5], predictor=None)

    result = explain.get_explanation(db, alert_id, predictor=None)
    assert result["source"] == "snapshot" and result["snapshot_kind"] == "escalated"
    assert result["triggering_readings"]["trigger_reading_id"] == ids[5]
    assert [s["kind"] for s in result["snapshots"]] == ["created", "escalated"]
    assert result["snapshot_at"] == result["snapshots"][-1]["created_at"] == result["generated_at"]
    assert result["alert"]["id"] == alert_id and "feedback" in result["alert"]


def test_get_explanation_reconstructs_without_writing(db):
    ids = _seed(db, n=6)
    alert_id = _alert(db, reading_id=ids[3])
    result = explain.get_explanation(db, alert_id, predictor=None)
    assert result["source"] == "reconstructed" and result["snapshots"] == []
    assert result["snapshot_kind"] is None
    assert result["triggering_readings"]["trigger_reading_id"] == ids[3]
    assert any("rebuilt" in n for n in result["notes"])
    assert db.execute("SELECT COUNT(*) FROM alert_explanations").fetchone()[0] == 0


@pytest.mark.parametrize("payload", ["{not json", json.dumps({"explanation_version": 99})])
def test_get_explanation_reconstructs_a_corrupt_snapshot(db, payload):
    alert_id = _alert(db)
    db.execute("INSERT INTO alert_explanations (alert_id, kind, created_at, explanation_json) "
               "VALUES (?, 'created', ?, ?)", (alert_id, _ts(0), payload))
    db.commit()
    result = explain.get_explanation(db, alert_id, predictor=None)
    assert result["source"] == "reconstructed"
    assert len(result["snapshots"]) == 1
    assert any("could not be read" in n for n in result["notes"])


def test_get_explanation_unknown_alert(db):
    assert explain.get_explanation(db, 999, predictor=None) is None


def test_similar_incidents_are_recomputed_on_read(db):
    old = _alert(db, "mx", _ts(10), status="resolved")
    this = _alert(db, "mx", _ts(1000))
    explain.record_snapshot(db, _get_alert(db, this), "created", predictor=None)
    first = explain.get_explanation(db, this, predictor=None)
    assert first["similar_incidents"][0]["feedback"] is None
    _feedback(db, old, cause="bearing_wear")
    second = explain.get_explanation(db, this, predictor=None)
    assert second["source"] == "snapshot"
    assert second["similar_incidents"][0]["feedback"]["actual_cause"] == "bearing_wear"


# --- loaded_predictor (decision 3) ---------------------------------------------------

def test_loaded_predictor_never_loads(monkeypatch):
    from src.api.routes import predictions

    def must_not_load():
        raise AssertionError("loaded_predictor must not load the model")

    must_not_load.cache_info = lambda: type("Info", (), {"currsize": 0})()
    monkeypatch.setattr(predictions, "_cached_predictor", must_not_load)
    assert explain.loaded_predictor() is None


def test_loaded_predictor_returns_the_cached_instance(monkeypatch):
    from src.api.routes import predictions

    sentinel = object()

    def cached():
        return sentinel

    cached.cache_info = lambda: type("Info", (), {"currsize": 1})()
    monkeypatch.setattr(predictions, "_cached_predictor", cached)
    assert explain.loaded_predictor() is sentinel
