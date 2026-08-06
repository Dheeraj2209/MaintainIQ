"""Rule-based health classifier — the permanent fallback prediction path.

Ported from research/ims_bearing_baseline/src/pipeline.py's
add_health_score_and_stage, generalized to MaintainIQ's machine_id schema
and extended from that research code's 3-state labeling (Normal/Degrading/
Critical) to the design baseline's 4 health states (healthy/degrading/
faulty/critical) by splitting the upper band at a second threshold.

This stays in the system permanently per design/DESIGN_BASELINE.md — not a
placeholder to delete once a model is trained. src/prediction/router.py
(M3) falls back to this path whenever no ML model is deployed or its
confidence is too low.
"""
import pandas as pd

# Z-score thresholds. HEALTHY/DEGRADING/FAULTY boundaries are inherited from
# the research baseline's Normal/Degrading/Critical thresholds (calibrated on
# IMS Test 1's own healthy-period distribution — see that project's README
# for known cross-rig calibration limitations). The FAULTY/CRITICAL split is
# new here, chosen as a mid-point of the original "Critical" band.
HEALTHY_MAX = 3
DEGRADING_MAX = 15
FAULTY_MAX = 30
BASELINE_FRACTION = 0.1
MIN_BASELINE_ROWS = 20


def _zscore_health(group: pd.DataFrame) -> pd.Series:
    group = group.sort_values("timestamp")
    n_base = max(MIN_BASELINE_ROWS, int(len(group) * BASELINE_FRACTION))
    baseline = group.iloc[:n_base]

    rms_mu, rms_sd = baseline["vibration_h_rms"].mean(), baseline["vibration_h_rms"].std()
    kur_mu, kur_sd = baseline["vibration_h_kurtosis"].mean(), baseline["vibration_h_kurtosis"].std()

    z_rms = (group["vibration_h_rms"] - rms_mu) / (rms_sd + 1e-9)
    z_kurtosis = (group["vibration_h_kurtosis"] - kur_mu) / (kur_sd + 1e-9)
    return (z_rms.abs() + z_kurtosis.abs()) / 2


def _stage(score: float) -> str:
    if score < HEALTHY_MAX:
        return "healthy"
    if score < DEGRADING_MAX:
        return "degrading"
    if score < FAULTY_MAX:
        return "faulty"
    return "critical"


def add_health_score_and_stage(long_df: pd.DataFrame) -> pd.DataFrame:
    """Add `health_score` (z-score based severity) and `health_state` columns,
    computed independently per machine_id using that machine's own early
    readings as its healthy baseline.
    """
    long_df = long_df.copy()
    long_df["health_score"] = long_df.groupby("machine_id", group_keys=False).apply(_zscore_health)
    long_df["health_state"] = long_df["health_score"].apply(_stage)
    return long_df
