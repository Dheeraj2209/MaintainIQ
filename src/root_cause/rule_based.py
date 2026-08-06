"""Rule-based probable root cause classification.

Ground truth is only available for one category here: the IMS Bearing
Dataset's documented failures (inner race, outer race, roller element) all
fall under BEARING_WEAR. The other SRS-defined categories have no labeled
data in this dataset, so they stay heuristic/rule-based only — per the
SRS's own risk mitigation (design/SRS_Document.md Section 10: "show root
cause as probable, evaluate accuracy only if labeled data is available").

Per design/DESIGN_BASELINE.md, root cause is always reported as a probable
cause, never a final diagnosis.
"""
import pandas as pd

BEARING_WEAR = "bearing_wear"
IMBALANCE = "imbalance"
SENSOR_DATA_QUALITY = "sensor_or_data_quality_issue"
UNKNOWN = "unknown"

# Threshold is heuristic, not learned — see module docstring.
HIGH_KURTOSIS = 5.0


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
