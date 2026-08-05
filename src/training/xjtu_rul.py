"""Leakage-resistant RUL training for the XJTU-SY bearing dataset."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor
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
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import Pipeline

from src.ingestion.xjtu_sy import build_feature_table

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT = REPO_ROOT / "models" / "xjtu_rul_model.joblib"
DEFAULT_REPORT = REPO_ROOT / "models" / "xjtu_rul_evaluation.json"

ROLLING_SOURCE_COLUMNS = [
    "h_rms", "v_rms", "m_rms", "h_peak", "v_peak", "m_peak",
    "h_kurtosis", "v_kurtosis", "m_kurtosis",
    "h_envelope_rms", "v_envelope_rms", "m_envelope_rms",
    "h_envelope_kurtosis", "v_envelope_kurtosis", "m_envelope_kurtosis",
    "h_spectral_entropy", "v_spectral_entropy", "m_spectral_entropy",
    "h_envelope_energy_100_200_hz_ratio", "v_envelope_energy_100_200_hz_ratio",
]
ROLLING_WINDOWS = (5, 20, 60)
BASELINE_WINDOW = 20
PROGNOSTIC_HORIZON_MINUTES = 120.0
FAILURE_PROBABILITY_THRESHOLD = 0.6
PROBABILITY_SMOOTHING_WINDOW = 3
WARNING_PERSISTENCE_SNAPSHOTS = 3
CLASSIFIER_ENSEMBLE_SEEDS = (42, 200, 1000)
CLASSIFIER_DIMENSIONLESS_TOKENS = (
    "_baseline_ratio", "_kurtosis", "_skewness", "_crest_factor",
    "_shape_factor", "_impulse_factor", "_clearance_factor",
    "_energy_", "_spectral_entropy", "cross_axis_",
)
NON_FEATURE_COLUMNS = {"bearing_id", "rul_minutes", "source_file", "condition", "cycle"}


def add_past_context(table: pd.DataFrame) -> pd.DataFrame:
    """Add causal rolling features; each row uses only that row and its past."""
    required = {"bearing_id", "cycle", *ROLLING_SOURCE_COLUMNS}
    missing = required.difference(table.columns)
    if missing:
        raise ValueError(f"feature table is missing columns: {sorted(missing)}")
    result = table.sort_values(["bearing_id", "cycle"]).reset_index(drop=True).copy()
    derived: dict[str, np.ndarray] = {}

    def store(name: str, indices: np.ndarray, values: np.ndarray) -> None:
        if name not in derived:
            derived[name] = np.zeros(len(result), dtype=float)
        derived[name][indices] = values

    for _, indices in result.groupby("bearing_id", sort=False).groups.items():
        idx = np.asarray(list(indices), dtype=int)
        for column in ROLLING_SOURCE_COLUMNS:
            values = result.loc[idx, column].astype(float).reset_index(drop=True)
            baseline = values.expanding(min_periods=1).median()
            if len(baseline) > BASELINE_WINDOW:
                baseline.iloc[BASELINE_WINDOW:] = baseline.iloc[BASELINE_WINDOW - 1]
            denominator = baseline.abs().clip(lower=1e-12)
            store(f"{column}_baseline_ratio", idx, (values / denominator).to_numpy())
            store(f"{column}_baseline_delta", idx, (values - baseline).to_numpy())
            for window in ROLLING_WINDOWS:
                rolled = values.rolling(window, min_periods=1)
                store(f"{column}_mean_{window}", idx, rolled.mean().to_numpy())
                store(
                    f"{column}_std_{window}", idx,
                    rolled.std(ddof=0).fillna(0).to_numpy(),
                )
                trend = (
                    (values - values.shift(window - 1)) / max(window - 1, 1)
                ).fillna(0).to_numpy()
                store(f"{column}_trend_{window}", idx, trend)
    return pd.concat([result, pd.DataFrame(derived)], axis=1)


def feature_columns(table: pd.DataFrame) -> list[str]:
    return [
        column for column in table.columns
        if column not in NON_FEATURE_COLUMNS and pd.api.types.is_numeric_dtype(table[column])
    ]


def classifier_feature_columns(table: pd.DataFrame) -> list[str]:
    """Select scale-robust features for failure-horizon classification."""
    available = feature_columns(table)
    selected = [column for column in ("speed_rpm", "load_kn") if column in available]
    selected.extend(
        column for column in available
        if any(token in column for token in CLASSIFIER_DIMENSIONLESS_TOKENS)
    )
    return list(dict.fromkeys(selected))


def smooth_probabilities(table: pd.DataFrame, probabilities: np.ndarray) -> np.ndarray:
    smoothed = np.zeros(len(table), dtype=float)
    for _, indices in table.groupby("bearing_id", sort=False).groups.items():
        idx = np.asarray(list(indices), dtype=int)
        smoothed[idx] = (
            pd.Series(probabilities[idx])
            .rolling(PROBABILITY_SMOOTHING_WINDOW, min_periods=1)
            .median()
            .to_numpy()
        )
    return smoothed


def sustained_warning_mask(table: pd.DataFrame, probabilities: np.ndarray) -> np.ndarray:
    """Require consecutive high probabilities before raising a live warning."""
    active = np.zeros(len(table), dtype=bool)
    for _, indices in table.groupby("bearing_id", sort=False).groups.items():
        idx = np.asarray(list(indices), dtype=int)
        above = pd.Series(probabilities[idx] >= FAILURE_PROBABILITY_THRESHOLD)
        active[idx] = (
            above.rolling(
                WARNING_PERSISTENCE_SNAPSHOTS,
                min_periods=WARNING_PERSISTENCE_SNAPSHOTS,
            )
            .sum()
            .eq(WARNING_PERSISTENCE_SNAPSHOTS)
            .to_numpy()
        )
    return active


def make_classifier(random_state: int = 42) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", ExtraTreesClassifier(
            n_estimators=350,
            min_samples_leaf=3,
            max_features=0.8,
            class_weight="balanced",
            n_jobs=-1,
            random_state=random_state,
        )),
    ])


def make_regressor(random_state: int = 42) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("regressor", ExtraTreesRegressor(
            n_estimators=350,
            min_samples_leaf=3,
            max_features=0.8,
            n_jobs=-1,
            random_state=random_state,
        )),
    ])


def train_and_export(
    table: pd.DataFrame,
    artifact_path: Path = DEFAULT_ARTIFACT,
    report_path: Path = DEFAULT_REPORT,
) -> dict:
    table = add_past_context(table)
    regressor_features = feature_columns(table)
    classifier_features = classifier_feature_columns(table)
    groups = table["bearing_id"].astype(str)
    unique_groups = groups.nunique()
    if unique_groups < 3:
        raise ValueError("at least three complete bearing trajectories are required")

    X_classifier = table[classifier_features]
    X_regressor = table[regressor_features]
    y = table["rul_minutes"].astype(float).to_numpy()
    in_horizon = y <= PROGNOSTIC_HORIZON_MINUTES
    failure_probabilities = np.full(len(table), np.nan)
    rul_predictions = np.full(len(table), np.nan)
    fold_results = []
    splitter = LeaveOneGroupOut()
    for fold, (train_idx, test_idx) in enumerate(
        splitter.split(X_regressor, y, groups), start=1
    ):
        fold_classifiers = []
        for seed in CLASSIFIER_ENSEMBLE_SEEDS:
            classifier = make_classifier(seed + fold)
            classifier.fit(X_classifier.iloc[train_idx], in_horizon[train_idx])
            fold_classifiers.append(classifier)
        fold_probability = np.mean([
            classifier.predict_proba(X_classifier.iloc[test_idx])[:, 1]
            for classifier in fold_classifiers
        ], axis=0)
        failure_probabilities[test_idx] = fold_probability

        late_train_idx = train_idx[in_horizon[train_idx]]
        regressor = make_regressor(42 + fold)
        regressor.fit(X_regressor.iloc[late_train_idx], y[late_train_idx])
        fold_rul = np.clip(
            regressor.predict(X_regressor.iloc[test_idx]), 0, PROGNOSTIC_HORIZON_MINUTES
        )
        rul_predictions[test_idx] = fold_rul
    smoothed_probabilities = smooth_probabilities(table, failure_probabilities)
    predicted_in_horizon = sustained_warning_mask(table, smoothed_probabilities)
    smoothed_predicted_in_horizon = (
        smoothed_probabilities >= FAILURE_PROBABILITY_THRESHOLD
    )
    raw_predicted_in_horizon = failure_probabilities >= FAILURE_PROBABILITY_THRESHOLD
    late_errors = np.abs(y[in_horizon] - rul_predictions[in_horizon])
    conformal_error_90 = float(np.quantile(late_errors, 0.90, method="higher"))
    final_classifiers = []
    for seed in CLASSIFIER_ENSEMBLE_SEEDS:
        classifier = make_classifier(seed)
        classifier.fit(X_classifier, in_horizon)
        final_classifiers.append(classifier)
    final_regressor = make_regressor(42)
    final_regressor.fit(
        X_regressor.iloc[np.flatnonzero(in_horizon)], y[in_horizon]
    )

    bounds = {
        column: [
            float(X_classifier[column].quantile(0.005)),
            float(X_classifier[column].quantile(0.995)),
        ]
        for column in classifier_features
        if np.isfinite(X_classifier[column].quantile(0.005))
        and np.isfinite(X_classifier[column].quantile(0.995))
    }
    artifact = {
        "classifiers": final_classifiers,
        "regressor": final_regressor,
        "classifier_feature_columns": classifier_features,
        "regressor_feature_columns": regressor_features,
        "feature_columns": regressor_features,
        "feature_bounds_99pct": bounds,
        "conformal_error_90_minutes": conformal_error_90,
        "sample_rate_hz": 25_600.0,
        "rolling_windows": list(ROLLING_WINDOWS),
        "baseline_window": BASELINE_WINDOW,
        "prognostic_horizon_minutes": PROGNOSTIC_HORIZON_MINUTES,
        "failure_probability_threshold": FAILURE_PROBABILITY_THRESHOLD,
        "probability_smoothing_window": PROBABILITY_SMOOTHING_WINDOW,
        "warning_persistence_snapshots": WARNING_PERSISTENCE_SNAPSHOTS,
        "health_thresholds_minutes": {"critical": 30.0, "faulty": 60.0, "degrading": 120.0},
        "dataset": "XJTU-SY",
        "model_version": datetime.now(timezone.utc).strftime("xjtu-rul-%Y%m%dT%H%M%SZ"),
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, artifact_path)

    per_bearing = []
    first_warnings = []
    for fold, (bearing_id, indices) in enumerate(
        table.groupby("bearing_id").groups.items(), start=1
    ):
        idx = np.asarray(list(indices), dtype=int)
        bearing_truth = in_horizon[idx]
        bearing_class = predicted_in_horizon[idx]
        warning_indices = idx[bearing_class]
        first_warning_rul = (
            None if len(warning_indices) == 0 else float(y[warning_indices[0]])
        )
        first_warnings.append({
            "bearing_id": bearing_id,
            "actual_rul_minutes_at_first_warning": first_warning_rul,
            "timing": (
                "no_warning" if first_warning_rul is None
                else "within_horizon" if first_warning_rul <= PROGNOSTIC_HORIZON_MINUTES
                else "early"
            ),
        })
        bearing_result = {
            "bearing_id": bearing_id,
            "samples": len(idx),
            "failure_precision": float(precision_score(bearing_truth, bearing_class, zero_division=0)),
            "failure_recall": float(recall_score(bearing_truth, bearing_class, zero_division=0)),
            "within_horizon_rul_mae_minutes": float(
                mean_absolute_error(y[idx][bearing_truth], rul_predictions[idx][bearing_truth])
            ),
        }
        per_bearing.append(bearing_result)
        fold_results.append({
            "fold": fold,
            "test_bearings": [bearing_id],
            **{key: value for key, value in bearing_result.items() if key != "bearing_id"},
        })
    report = {
        "dataset": "XJTU-SY",
        "approach": "two-stage prognostics: classify failure within horizon, then regress RUL",
        "validation": "leave-one-bearing-out; each of 15 test folds is one completely unseen bearing",
        "bearing_count": int(unique_groups),
        "sample_count": int(len(table)),
        "classifier_feature_count": len(classifier_features),
        "regressor_feature_count": len(regressor_features),
        "classifier_ensemble_size": len(CLASSIFIER_ENSEMBLE_SEEDS),
        "prognostic_horizon_minutes": PROGNOSTIC_HORIZON_MINUTES,
        "failure_probability_threshold": FAILURE_PROBABILITY_THRESHOLD,
        "probability_smoothing_window": PROBABILITY_SMOOTHING_WINDOW,
        "warning_persistence_snapshots": WARNING_PERSISTENCE_SNAPSHOTS,
        "failure_detection": {
            "precision": float(precision_score(in_horizon, predicted_in_horizon, zero_division=0)),
            "recall": float(recall_score(in_horizon, predicted_in_horizon, zero_division=0)),
            "f1": float(f1_score(in_horizon, predicted_in_horizon, zero_division=0)),
            "average_precision": float(average_precision_score(in_horizon, smoothed_probabilities)),
            "roc_auc": float(roc_auc_score(in_horizon, smoothed_probabilities)),
            "false_alarm_count": int(np.sum(predicted_in_horizon & ~in_horizon)),
            "missed_failure_window_count": int(np.sum(~predicted_in_horizon & in_horizon)),
        },
        "raw_failure_detection_without_temporal_smoothing": {
            "precision": float(precision_score(in_horizon, raw_predicted_in_horizon, zero_division=0)),
            "recall": float(recall_score(in_horizon, raw_predicted_in_horizon, zero_division=0)),
            "average_precision": float(average_precision_score(in_horizon, failure_probabilities)),
            "roc_auc": float(roc_auc_score(in_horizon, failure_probabilities)),
            "false_alarm_count": int(np.sum(raw_predicted_in_horizon & ~in_horizon)),
            "missed_failure_window_count": int(np.sum(~raw_predicted_in_horizon & in_horizon)),
        },
        "smoothed_failure_detection_without_persistence": {
            "precision": float(precision_score(
                in_horizon, smoothed_predicted_in_horizon, zero_division=0
            )),
            "recall": float(recall_score(
                in_horizon, smoothed_predicted_in_horizon, zero_division=0
            )),
            "false_alarm_count": int(np.sum(
                smoothed_predicted_in_horizon & ~in_horizon
            )),
            "missed_failure_window_count": int(np.sum(
                ~smoothed_predicted_in_horizon & in_horizon
            )),
        },
        "first_warning_events": {
            "within_horizon_bearings": int(sum(
                row["timing"] == "within_horizon" for row in first_warnings
            )),
            "early_warning_bearings": int(sum(
                row["timing"] == "early" for row in first_warnings
            )),
            "no_warning_bearings": int(sum(
                row["timing"] == "no_warning" for row in first_warnings
            )),
            "per_bearing": first_warnings,
        },
        "within_horizon_rul": {
            "mae_minutes": float(mean_absolute_error(y[in_horizon], rul_predictions[in_horizon])),
            "rmse_minutes": float(np.sqrt(mean_squared_error(y[in_horizon], rul_predictions[in_horizon]))),
            "error_90_minutes": conformal_error_90,
        },
        "folds": fold_results,
        "per_bearing": per_bearing,
        "artifact": str(artifact_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "outside the prognostic horizon the model returns a lower bound, not an invented exact RUL",
            "accelerated laboratory bearing failures are not the same distribution as field machines",
            "the interval is an empirical cross-validation error bound, not a safety guarantee",
            "deployment requires target-machine healthy-baseline and run-to-failure/failure-history validation",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the XJTU-SY bearing RUL model")
    parser.add_argument("--data-dir", type=Path, help="extracted raw XJTU-SY directory")
    parser.add_argument("--features-csv", type=Path, default=REPO_ROOT / "outputs" / "xjtu_features.csv")
    parser.add_argument("--rebuild-features", action="store_true")
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    if args.rebuild_features or not args.features_csv.exists():
        if args.data_dir is None:
            parser.error("--data-dir is required when the feature CSV does not exist")
        table = build_feature_table(args.data_dir, args.features_csv)
    else:
        table = pd.read_csv(args.features_csv)
    report = train_and_export(table, args.artifact, args.report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
