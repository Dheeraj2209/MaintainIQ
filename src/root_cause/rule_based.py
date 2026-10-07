"""Rule-based probable root cause classification.

Ground truth is only available for one category here: the IMS Bearing
Dataset's documented failures (inner race, outer race, roller element) all
fall under BEARING_WEAR. The other SRS-defined categories have no labeled
data in this dataset, so they stay heuristic/rule-based only — per the
SRS's own risk mitigation (design/SRS_Document.md Section 10: "show root
cause as probable, evaluate accuracy only if labeled data is available").

Per design/DESIGN_BASELINE.md, root cause is always reported as a probable
cause, never a final diagnosis.

explain_probable_cause returns the same label plus the rule trace that
produced it, for the "Why this alert?" view
(design/2026-10-07-alert-explanation-design.md, decision 8). It is a second
function rather than a refactor of classify_probable_cause, so the batch
path's behaviour cannot move; tests/root_cause/test_rule_based.py pins the
two to the same label over a grid of rows.
"""
import pandas as pd

BEARING_WEAR = "bearing_wear"
IMBALANCE = "imbalance"
SENSOR_DATA_QUALITY = "sensor_or_data_quality_issue"
UNKNOWN = "unknown"

# Threshold is heuristic, not learned — see module docstring.
HIGH_KURTOSIS = 5.0

# Rule ids reported by explain_probable_cause, one per classify_probable_cause
# branch, in the same order.
RULE_MISSING_KURTOSIS = "missing_kurtosis"
RULE_HIGH_KURTOSIS = "high_kurtosis"
RULE_NONZERO_RMS = "nonzero_rms"
RULE_NO_MATCH = "no_rule_matched"

CAUSE_LABELS = {
    BEARING_WEAR: "bearing wear",
    IMBALANCE: "imbalance",
    SENSOR_DATA_QUALITY: "sensor or data-quality issue",
    UNKNOWN: "unknown",
}


def classify_probable_cause(row: pd.Series) -> str:
    """Return a single probable root cause label for one machine-timestamp record.

    Only called for records already flagged degrading/faulty/critical — a healthy
    reading has no root cause to report. Uses the canonical XJTU-SY vibration
    channels (horizontal RMS + kurtosis); the IMS-era thermal and high-band-energy
    heuristics are gone with the schema. Root cause is refined alongside RUL in a
    later phase.
    """
    kurtosis = row.get("vibration_h_kurtosis")
    rms = row.get("vibration_h_rms")

    if kurtosis is None or pd.isna(kurtosis):
        return SENSOR_DATA_QUALITY

    if kurtosis >= HIGH_KURTOSIS:
        return BEARING_WEAR

    if rms is not None and not pd.isna(rms) and rms > 0:
        return IMBALANCE

    return UNKNOWN


def _present(value) -> bool:
    return value is not None and not pd.isna(value)


def _fmt(value: float) -> str:
    return f"{value:.3g}"


def explain_probable_cause(row) -> dict:
    """classify_probable_cause's label plus the rule trace behind it:
    {"label", "rule", "summary", "checks": [{"feature", "value", "operator",
    "threshold", "passed"}]}. Walks the same branches in the same order and
    reads HIGH_KURTOSIS at call time, so the trace can never name a threshold
    the classifier did not use. Each branch that was tried and failed is
    listed before the one that fired. `row` is anything with .get (a dict or
    a pd.Series); missing/NaN values are reported as None.
    """
    kurtosis = row.get("vibration_h_kurtosis")
    rms = row.get("vibration_h_rms")

    if not _present(kurtosis):
        return {
            "label": SENSOR_DATA_QUALITY,
            "rule": RULE_MISSING_KURTOSIS,
            "summary": "Horizontal kurtosis is missing, so probable sensor or data-quality issue.",
            "checks": [{"feature": "vibration_h_kurtosis", "value": None, "operator": "is missing",
                        "threshold": None, "passed": True}],
        }

    kurtosis = float(kurtosis)
    threshold = float(HIGH_KURTOSIS)
    kurtosis_check = {"feature": "vibration_h_kurtosis", "value": kurtosis, "operator": ">=",
                      "threshold": threshold, "passed": kurtosis >= threshold}
    if kurtosis_check["passed"]:
        return {
            "label": BEARING_WEAR,
            "rule": RULE_HIGH_KURTOSIS,
            "summary": f"Horizontal kurtosis {_fmt(kurtosis)} >= {_fmt(threshold)}, so probable bearing wear.",
            "checks": [kurtosis_check],
        }

    rms_value = float(rms) if _present(rms) else None
    rms_check = {"feature": "vibration_h_rms", "value": rms_value, "operator": ">",
                 "threshold": 0.0, "passed": rms_value is not None and rms_value > 0}
    not_wear = f"Horizontal kurtosis {_fmt(kurtosis)} < {_fmt(threshold)}, so not bearing wear"
    if rms_check["passed"]:
        return {
            "label": IMBALANCE,
            "rule": RULE_NONZERO_RMS,
            "summary": f"{not_wear}; horizontal RMS {_fmt(rms_value)} > 0, so probable imbalance.",
            "checks": [kurtosis_check, rms_check],
        }
    return {
        "label": UNKNOWN,
        "rule": RULE_NO_MATCH,
        "summary": f"{not_wear}; horizontal RMS is not above 0, so no rule matched.",
        "checks": [kurtosis_check, rms_check],
    }


ABNORMAL_STATES = {"degrading", "faulty", "critical"}


def add_probable_cause(long_df: pd.DataFrame) -> pd.DataFrame:
    """Add a `probable_cause` column: classify_probable_cause's output for
    every abnormal (degrading/faulty/critical) row, `pd.NA` for healthy rows.
    """
    long_df = long_df.copy()
    abnormal = long_df["health_state"].isin(ABNORMAL_STATES)
    long_df["probable_cause"] = pd.NA
    long_df.loc[abnormal, "probable_cause"] = long_df.loc[abnormal].apply(classify_probable_cause, axis=1)
    return long_df
