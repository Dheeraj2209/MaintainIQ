"""Unsupervised anomaly detection candidate — IsolationForest.

Ported from research/ims_bearing_baseline/src/pipeline.py's
train_anomaly_detector, generalized to machine_id. Trained only on each
machine's own healthy baseline period, so it needs no fault labels — the
SRS's own risk mitigation for "limited labeled fault data"
(design/DESIGN_BASELINE.md).
"""
import pandas as pd
from sklearn.ensemble import IsolationForest

BASELINE_FRACTION = 0.1


def _feature_columns(df: pd.DataFrame) -> list:
    """Primary channel only — see src/training/stage_classifiers.py's
    _feature_columns for why (Test 1 has a second channel Tests 2/3 lack)."""
    return [c for c in df.columns if c.startswith("vibration_h_") or c == "temperature_c"]


def train_anomaly_detector(long_df: pd.DataFrame) -> tuple:
    baseline_frames = []
    for _, group in long_df.groupby("machine_id"):
        ordered = group.sort_values("timestamp")
        n_base = max(1, int(len(ordered) * BASELINE_FRACTION))
        baseline_frames.append(ordered.iloc[:n_base])
    baseline_df = pd.concat(baseline_frames)

    feature_cols = _feature_columns(long_df)
    model = IsolationForest(n_estimators=300, contamination=0.05, random_state=42)
    model.fit(baseline_df[feature_cols])

    scored = long_df.copy()
    scored["anomaly_score"] = -model.score_samples(scored[feature_cols])
    scored["is_anomaly"] = model.predict(scored[feature_cols]) == -1
    return model, scored
