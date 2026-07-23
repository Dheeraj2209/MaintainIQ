"""Remaining-useful-life (RUL) regression — bonus prediction, not required MVP scope.

Ported from research/ims_bearing_baseline/src/pipeline.py's leave-one-
trajectory-out RUL training. Kept here because it already works and adds a
genuinely useful KPI (predicted time to failure), but it is not a blocker
for the M2b milestone — only 4 real failure trajectories exist across the
whole IMS dataset, which the README flags as too small a sample for a
production-grade cross-machine RUL model. See
research/ims_bearing_baseline/README.md's "Known limitations" section
before presenting any RUL number as final, especially: Test 1 and Test 3's
RUL hours include wall-clock acquisition-gap downtime (not real operating
time), which the source project explicitly left uncorrected.
"""
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
from xgboost import XGBRegressor


def _feature_columns(df: pd.DataFrame) -> list:
    return [c for c in df.columns if c.startswith("vibration_h_") or c == "temperature_c"]


def add_rul(long_df: pd.DataFrame) -> pd.DataFrame:
    """Add rul_hours (hours remaining until end of trajectory) for machines
    flagged is_documented_failure. Non-failing machines get NaN — there is no
    known failure point to count down to.
    """
    long_df = long_df.copy()
    long_df["rul_hours"] = np.nan
    for machine_id, group in long_df[long_df["is_documented_failure"]].groupby("machine_id"):
        end_time = group["timestamp"].max()
        mask = long_df["machine_id"] == machine_id
        long_df.loc[mask, "rul_hours"] = (end_time - long_df.loc[mask, "timestamp"]).dt.total_seconds() / 3600.0
    return long_df


def train_leave_one_trajectory_out(long_df: pd.DataFrame) -> dict:
    """Train one RUL model per known failure trajectory, tested on that
    trajectory while trained on every other known trajectory — never on data
    from the rig/bearing being evaluated.
    """
    rul_df = long_df.dropna(subset=["rul_hours"])
    trajectories = sorted(rul_df["machine_id"].unique())
    feature_cols = _feature_columns(rul_df)

    results = {}
    for held_out in trajectories:
        train_df = rul_df[rul_df["machine_id"] != held_out]
        test_df = rul_df[rul_df["machine_id"] == held_out]
        if train_df.empty or test_df.empty:
            continue

        model = XGBRegressor(
            n_estimators=400, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
        )
        model.fit(train_df[feature_cols], train_df["rul_hours"])
        y_pred = np.clip(model.predict(test_df[feature_cols]), 0, None)
        y_test = test_df["rul_hours"]

        results[held_out] = {
            "model": model,
            "trained_on": [t for t in trajectories if t != held_out],
            "mae": mean_absolute_error(y_test, y_pred),
            "rmse": np.sqrt(mean_squared_error(y_test, y_pred)),
        }
    return results
