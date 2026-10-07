"""Tests for the probable-cause rule trace
(design/2026-10-07-alert-explanation-design.md, decision 8).

explain_probable_cause is a second function beside classify_probable_cause,
not a refactor of it: the parity test below is what keeps the two in step.
"""
import itertools
import math

import pandas as pd
import pytest

from src.root_cause import rule_based
from src.root_cause.rule_based import (
    BEARING_WEAR,
    HIGH_KURTOSIS,
    IMBALANCE,
    SENSOR_DATA_QUALITY,
    UNKNOWN,
    add_probable_cause,
    classify_probable_cause,
    explain_probable_cause,
)

KURTOSIS_VALUES = [None, math.nan, 4.99, 5.0, 12.0]
RMS_VALUES = [None, 0.0, -0.1, 0.4]


@pytest.mark.parametrize("kurtosis,rms", list(itertools.product(KURTOSIS_VALUES, RMS_VALUES)))
def test_label_matches_classify_probable_cause(kurtosis, rms):
    row = pd.Series({"vibration_h_kurtosis": kurtosis, "vibration_h_rms": rms})
    assert explain_probable_cause(row)["label"] == classify_probable_cause(row)


def test_high_kurtosis_trace_has_one_passed_check():
    trace = explain_probable_cause({"vibration_h_kurtosis": 9.4, "vibration_h_rms": 1.0})
    assert trace["label"] == BEARING_WEAR
    assert trace["rule"] == rule_based.RULE_HIGH_KURTOSIS == "high_kurtosis"
    assert trace["checks"] == [{"feature": "vibration_h_kurtosis", "value": 9.4, "operator": ">=",
                                "threshold": HIGH_KURTOSIS, "passed": True}]
    assert "probable bearing wear" in trace["summary"]


def test_nonzero_rms_lists_the_failed_kurtosis_check_first():
    trace = explain_probable_cause({"vibration_h_kurtosis": 3.1, "vibration_h_rms": 0.42})
    assert (trace["label"], trace["rule"]) == (IMBALANCE, "nonzero_rms")
    assert [(c["feature"], c["passed"]) for c in trace["checks"]] == [
        ("vibration_h_kurtosis", False), ("vibration_h_rms", True)]
    assert trace["checks"][1]["operator"] == ">" and trace["checks"][1]["threshold"] == 0.0
    assert "not bearing wear" in trace["summary"]


def test_missing_and_no_match_rule_ids():
    missing = explain_probable_cause({"vibration_h_kurtosis": None, "vibration_h_rms": 0.4})
    assert (missing["label"], missing["rule"]) == (SENSOR_DATA_QUALITY, "missing_kurtosis")
    assert missing["checks"][0]["value"] is None and missing["checks"][0]["passed"] is True
    nan = explain_probable_cause({"vibration_h_kurtosis": math.nan})
    assert nan["rule"] == "missing_kurtosis" and nan["checks"][0]["value"] is None
    nothing = explain_probable_cause({"vibration_h_kurtosis": 2.0, "vibration_h_rms": 0.0})
    assert (nothing["label"], nothing["rule"]) == (UNKNOWN, "no_rule_matched")
    assert [c["passed"] for c in nothing["checks"]] == [False, False]


def test_threshold_is_read_from_the_module_constant(monkeypatch):
    monkeypatch.setattr(rule_based, "HIGH_KURTOSIS", 10.0)
    trace = explain_probable_cause({"vibration_h_kurtosis": 9.4, "vibration_h_rms": 1.0})
    assert trace["label"] == IMBALANCE
    assert trace["checks"][0]["threshold"] == 10.0


def test_cause_labels_are_human_readable():
    assert rule_based.CAUSE_LABELS[BEARING_WEAR] == "bearing wear"
    assert rule_based.CAUSE_LABELS[SENSOR_DATA_QUALITY] == "sensor or data-quality issue"


def test_classify_and_add_probable_cause_unchanged():
    frame = pd.DataFrame({
        "health_state": ["healthy", "degrading", "faulty", "critical", "critical"],
        "vibration_h_kurtosis": [9.0, 9.0, 3.0, None, 1.0],
        "vibration_h_rms": [1.0, 1.0, 0.5, 0.5, 0.0],
    })
    out = add_probable_cause(frame)
    assert pd.isna(out.loc[0, "probable_cause"])
    assert list(out.loc[1:, "probable_cause"]) == [BEARING_WEAR, IMBALANCE, SENSOR_DATA_QUALITY, UNKNOWN]
