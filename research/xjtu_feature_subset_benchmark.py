"""LOBO classifier benchmark for scale-invariant feature subsets."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score

from src.training.xjtu_rul import FAILURE_PROBABILITY_THRESHOLD, PROGNOSTIC_HORIZON_MINUTES, add_past_context, feature_columns, make_classifier


def select(columns, strategy):
    if strategy == "all":
        return columns
    dimensionless_tokens = (
        "_baseline_ratio", "_kurtosis", "_skewness", "_crest_factor",
        "_shape_factor", "_impulse_factor", "_clearance_factor",
        "_energy_", "_spectral_entropy", "cross_axis_",
    )
    selected = ["speed_rpm", "load_kn"]
    selected.extend(c for c in columns if any(token in c for token in dimensionless_tokens))
    if strategy == "baseline_only":
        selected = ["speed_rpm", "load_kn"] + [c for c in columns if "_baseline_ratio" in c]
    return list(dict.fromkeys(c for c in selected if c in columns))


def persistence_filter(data, probability, threshold, consecutive):
    active = np.zeros(len(data), dtype=bool)
    for _, indices in data.groupby("bearing_id", sort=False).groups.items():
        idx = np.asarray(list(indices), dtype=int)
        above = pd.Series(probability[idx] >= threshold)
        active[idx] = (
            above.rolling(consecutive, min_periods=consecutive).sum().eq(consecutive).to_numpy()
        )
    return active


def summarize(data, y, probability, threshold=FAILURE_PROBABILITY_THRESHOLD, consecutive=1):
    late = y <= PROGNOSTIC_HORIZON_MINUTES
    predicted = persistence_filter(data, probability, threshold, consecutive)
    first_alarms = []
    for _, group in data.assign(probability=probability).groupby("bearing_id", sort=True):
        mask = predicted[group.index.to_numpy()]
        alarm = group.loc[mask]
        first_alarms.append(None if alarm.empty else float(alarm.iloc[0].rul_minutes))
    return {
        "precision": float(precision_score(late, predicted, zero_division=0)),
        "recall": float(recall_score(late, predicted, zero_division=0)),
        "average_precision": float(average_precision_score(late, probability)),
        "roc_auc": float(roc_auc_score(late, probability)),
        "false_alarm_windows": int(np.sum(predicted & ~late)),
        "missed_failure_windows": int(np.sum(~predicted & late)),
        "on_time_first_alarms": int(sum(value is not None and value <= 120 for value in first_alarms)),
        "early_first_alarms": int(sum(value is not None and value > 120 for value in first_alarms)),
        "no_alarm_bearings": int(sum(value is None for value in first_alarms)),
        "first_alarm_actual_rul_minutes": first_alarms,
    }


def evaluate_strategy(data, y, late, groups, columns, seed_offset):
    X = data[columns]
    probability = np.full(len(data), np.nan)
    for fold, bearing in enumerate(sorted(np.unique(groups)), start=1):
        train_idx = np.flatnonzero(groups != bearing)
        test_idx = np.flatnonzero(groups == bearing)
        classifier = make_classifier(seed_offset + fold)
        classifier.fit(X.iloc[train_idx], late[train_idx])
        probability[test_idx] = classifier.predict_proba(X.iloc[test_idx])[:, 1]
    smoothed = np.zeros(len(data), dtype=float)
    for _, indices in data.groupby("bearing_id", sort=False).groups.items():
        idx = np.asarray(list(indices), dtype=int)
        smoothed[idx] = pd.Series(probability[idx]).rolling(3, min_periods=1).median().to_numpy()
    return smoothed


def main():
    root = Path(__file__).resolve().parents[1]
    data = add_past_context(pd.read_csv(root / "outputs" / "xjtu_features.csv"))
    all_columns = feature_columns(data)
    y = data.rul_minutes.to_numpy(float)
    late = y <= PROGNOSTIC_HORIZON_MINUTES
    groups = data.bearing_id.astype(str).to_numpy()
    results = {}
    for strategy in ("dimensionless",):
        columns = select(all_columns, strategy)
        probabilities = [
            evaluate_strategy(data, y, late, groups, columns, seed_offset)
            for seed_offset in (42, 200, 1000)
        ]
        ensemble_probability = np.mean(probabilities, axis=0)
        tuning = {}
        for threshold in (0.5, 0.6, 0.7, 0.8):
            for consecutive in (1, 3, 5):
                key = f"threshold_{threshold:g}_consecutive_{consecutive}"
                tuning[key] = summarize(
                    data, y, ensemble_probability, threshold=threshold, consecutive=consecutive
                )
        results[strategy] = {
            "feature_count": len(columns),
            "individual_seeds": [summarize(data, y, p) for p in probabilities],
            "three_seed_ensemble": summarize(data, y, ensemble_probability),
            "operating_point_tuning": tuning,
        }
        pd.DataFrame({
            "bearing_id": data["bearing_id"],
            "cycle": data["cycle"],
            "rul_minutes": y,
            "failure_probability": ensemble_probability,
        }).to_csv(root / "outputs" / "xjtu_dimensionless_oof_predictions.csv", index=False)
    output = root / "outputs" / "xjtu_feature_subset_benchmark.json"
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
