"""Compare honest cross-bearing XJTU-SY RUL formulations.

This is a reproducible research utility, not the deployed training entry point.
It helps select a capped prognostic horizon because absolute early-life RUL is
not identifiable when visually similar bearings have radically different total
lifetimes.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline

from src.training.xjtu_rul import add_past_context, feature_columns


def _models():
    return {
        "hist_gradient_boosting": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", HistGradientBoostingRegressor(
                learning_rate=0.06, max_iter=350, max_leaf_nodes=31,
                l2_regularization=1.0, early_stopping=True, random_state=42,
            )),
        ]),
        "extra_trees": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", ExtraTreesRegressor(
                n_estimators=250, min_samples_leaf=3, max_features=0.8,
                n_jobs=-1, random_state=42,
            )),
        ]),
    }


def _metrics(y, prediction, groups):
    per_bearing_mae = []
    for group in groups.unique():
        mask = groups == group
        per_bearing_mae.append(mean_absolute_error(y[mask], prediction[mask]))
    return {
        "mae_minutes": float(mean_absolute_error(y, prediction)),
        "macro_bearing_mae_minutes": float(np.mean(per_bearing_mae)),
        "rmse_minutes": float(np.sqrt(mean_squared_error(y, prediction))),
        "error_90_minutes": float(np.quantile(np.abs(y - prediction), 0.9, method="higher")),
    }


def _add_causal_baseline_features(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize each bearing to its own first 20 observed snapshots, causally."""
    result = data.copy()
    source_columns = [
        "h_rms", "v_rms", "h_peak", "v_peak", "h_kurtosis", "v_kurtosis",
        "h_crest_factor", "v_crest_factor", "h_spectral_energy", "v_spectral_energy",
    ]
    for _, indices in result.groupby("bearing_id", sort=False).groups.items():
        idx = list(indices)
        for column in source_columns:
            values = result.loc[idx, column].astype(float).reset_index(drop=True)
            baseline = values.expanding(min_periods=1).median()
            if len(baseline) > 20:
                baseline.iloc[20:] = baseline.iloc[19]
            denominator = baseline.abs().clip(lower=1e-12)
            result.loc[idx, f"{column}_baseline_ratio"] = (values / denominator).to_numpy()
            result.loc[idx, f"{column}_baseline_delta"] = (values - baseline).to_numpy()
            rolled = values.rolling(60, min_periods=1)
            result.loc[idx, f"{column}_mean_60"] = rolled.mean().to_numpy()
            result.loc[idx, f"{column}_std_60"] = rolled.std(ddof=0).fillna(0).to_numpy()
            result.loc[idx, f"{column}_trend_60"] = (
                (values - values.shift(59)) / 59
            ).fillna(0).to_numpy()
    return result


def _two_stage_experiment(data, columns, raw_y, groups, splitter, horizon):
    X = data[columns]
    truth = raw_y <= horizon
    probabilities = np.full(len(data), np.nan)
    rul_predictions = np.full(len(data), np.nan)
    for train_idx, test_idx in splitter:
        classifier = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", ExtraTreesClassifier(
                n_estimators=350, min_samples_leaf=3, max_features=0.8,
                class_weight="balanced", n_jobs=-1, random_state=42,
            )),
        ])
        classifier.fit(X.iloc[train_idx], truth[train_idx])
        probabilities[test_idx] = classifier.predict_proba(X.iloc[test_idx])[:, 1]

        late_train = train_idx[raw_y[train_idx] <= horizon]
        regressor = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", ExtraTreesRegressor(
                n_estimators=350, min_samples_leaf=3, max_features=0.8,
                n_jobs=-1, random_state=42,
            )),
        ])
        regressor.fit(X.iloc[late_train], raw_y[late_train])
        rul_predictions[test_idx] = np.clip(
            regressor.predict(X.iloc[test_idx]), 0, horizon
        )

    predicted_failure_horizon = probabilities >= 0.5
    late = truth
    return {
        "model": "two_stage_extra_trees_causal_baseline",
        "horizon_minutes": horizon,
        "failure_precision": float(precision_score(truth, predicted_failure_horizon, zero_division=0)),
        "failure_recall": float(recall_score(truth, predicted_failure_horizon, zero_division=0)),
        "failure_f1": float(f1_score(truth, predicted_failure_horizon, zero_division=0)),
        "failure_average_precision": float(average_precision_score(truth, probabilities)),
        "failure_roc_auc": float(roc_auc_score(truth, probabilities)),
        "false_alarm_count": int(np.sum(predicted_failure_horizon & ~truth)),
        "missed_failure_window_count": int(np.sum(~predicted_failure_horizon & truth)),
        "within_horizon_rul_mae_minutes": float(
            mean_absolute_error(raw_y[late], rul_predictions[late])
        ),
        "within_horizon_rul_error_90_minutes": float(
            np.quantile(np.abs(raw_y[late] - rul_predictions[late]), 0.9, method="higher")
        ),
    }


def main():
    root = Path(__file__).resolve().parents[1]
    data = add_past_context(pd.read_csv(root / "outputs" / "xjtu_features.csv"))
    columns = feature_columns(data)
    X = data[columns]
    raw_y = data["rul_minutes"].to_numpy(dtype=float)
    groups = data["bearing_id"].astype(str).reset_index(drop=True)
    splitter = list(GroupKFold(n_splits=5).split(X, raw_y, groups))
    results = []

    for horizon in (120.0, 360.0, 720.0, None):
        y = np.minimum(raw_y, horizon) if horizon is not None else raw_y.copy()
        baseline = np.full(len(y), horizon if horizon is not None else np.median(y))
        results.append({
            "model": "constant_horizon_baseline" if horizon is not None else "constant_median_baseline",
            "horizon_minutes": horizon,
            **_metrics(y, baseline, groups),
        })
        for model_name, model in _models().items():
            predictions = np.full(len(y), np.nan)
            for train_idx, test_idx in splitter:
                model.fit(X.iloc[train_idx], y[train_idx])
                predictions[test_idx] = np.clip(model.predict(X.iloc[test_idx]), 0, horizon)
            result = {
                "model": model_name,
                "horizon_minutes": horizon,
                **_metrics(y, predictions, groups),
            }
            if horizon is not None:
                late = raw_y <= horizon
                result["within_horizon_mae_minutes"] = float(
                    mean_absolute_error(raw_y[late], predictions[late])
                )
            results.append(result)

    causal_data = _add_causal_baseline_features(data)
    causal_columns = feature_columns(causal_data)
    for horizon in (60.0, 120.0, 180.0, 360.0):
        results.append(_two_stage_experiment(
            causal_data, causal_columns, raw_y, groups, splitter, horizon
        ))

    output = root / "outputs" / "xjtu_rul_experiments.json"
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
