"""Leave-one-bearing-out global vs condition-specific XJTU-SY benchmark."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, mean_absolute_error, precision_score, recall_score, roc_auc_score

from src.training.xjtu_rul import (
    FAILURE_PROBABILITY_THRESHOLD,
    PROGNOSTIC_HORIZON_MINUTES,
    add_past_context,
    feature_columns,
    make_classifier,
    make_regressor,
)


def metrics(y, probability, rul):
    late = y <= PROGNOSTIC_HORIZON_MINUTES
    predicted = probability >= FAILURE_PROBABILITY_THRESHOLD
    return {
        "precision": float(precision_score(late, predicted, zero_division=0)),
        "recall": float(recall_score(late, predicted, zero_division=0)),
        "average_precision": float(average_precision_score(late, probability)),
        "roc_auc": float(roc_auc_score(late, probability)),
        "false_alarms": int(np.sum(predicted & ~late)),
        "missed_failure_windows": int(np.sum(~predicted & late)),
        "late_rul_mae_minutes": float(mean_absolute_error(y[late], rul[late])),
        "late_rul_error_90_minutes": float(np.quantile(np.abs(y[late] - rul[late]), 0.9, method="higher")),
    }


def causal_smooth(values: np.ndarray, groups: pd.Series, window: int) -> np.ndarray:
    result = np.zeros(len(values), dtype=float)
    for _, indices in groups.groupby(groups, sort=False).groups.items():
        idx = np.asarray(list(indices), dtype=int)
        result[idx] = pd.Series(values[idx]).rolling(window, min_periods=1).median().to_numpy()
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    data = add_past_context(pd.read_csv(root / "outputs" / "xjtu_features.csv"))
    columns = feature_columns(data)
    X = data[columns]
    y = data["rul_minutes"].to_numpy(float)
    groups = data["bearing_id"].astype(str).reset_index(drop=True)
    condition = data["condition"].to_numpy(int)
    late = y <= PROGNOSTIC_HORIZON_MINUTES
    outputs = {}

    for strategy in ("global", "same_condition"):
        probability = np.full(len(data), np.nan)
        rul = np.full(len(data), np.nan)
        for fold, held_bearing in enumerate(sorted(groups.unique()), start=1):
            test_idx = np.flatnonzero(groups.to_numpy() == held_bearing)
            train_mask = groups.to_numpy() != held_bearing
            if strategy == "same_condition":
                train_mask &= condition == condition[test_idx[0]]
            train_idx = np.flatnonzero(train_mask)
            classifier = make_classifier(100 + fold)
            classifier.fit(X.iloc[train_idx], late[train_idx])
            probability[test_idx] = classifier.predict_proba(X.iloc[test_idx])[:, 1]
            late_train = train_idx[late[train_idx]]
            regressor = make_regressor(100 + fold)
            regressor.fit(X.iloc[late_train], y[late_train])
            rul[test_idx] = np.clip(
                regressor.predict(X.iloc[test_idx]), 0, PROGNOSTIC_HORIZON_MINUTES
            )
        outputs[strategy] = {"raw": metrics(y, probability, rul)}
        for window in (3, 5):
            smoothed = causal_smooth(probability, groups, window)
            outputs[strategy][f"causal_median_{window}"] = metrics(y, smoothed, rul)
        pd.DataFrame({
            "bearing_id": groups,
            "condition": condition,
            "rul_minutes": y,
            "failure_probability": probability,
            "predicted_rul_minutes": rul,
        }).to_csv(root / "outputs" / f"xjtu_lobo_{strategy}_predictions.csv", index=False)

    output = root / "outputs" / "xjtu_lobo_benchmark.json"
    output.write_text(json.dumps(outputs, indent=2), encoding="utf-8")
    print(json.dumps(outputs, indent=2))


if __name__ == "__main__":
    main()
