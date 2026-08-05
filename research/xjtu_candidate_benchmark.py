"""Candidate model comparison on cached rich XJTU-SY features."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, mean_absolute_error, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier, XGBRegressor

from src.training.xjtu_rul import PROGNOSTIC_HORIZON_MINUTES, add_past_context, feature_columns


def candidates():
    return {
        "extra_trees": (
            ExtraTreesClassifier(n_estimators=350, min_samples_leaf=3, max_features=0.8,
                                 class_weight="balanced", n_jobs=-1, random_state=42),
            ExtraTreesRegressor(n_estimators=350, min_samples_leaf=3, max_features=0.8,
                                n_jobs=-1, random_state=42),
        ),
        "hist_gradient_boosting": (
            HistGradientBoostingClassifier(max_iter=350, learning_rate=0.06, max_leaf_nodes=31,
                                           l2_regularization=1.0, class_weight="balanced", random_state=42),
            HistGradientBoostingRegressor(max_iter=350, learning_rate=0.06, max_leaf_nodes=31,
                                          l2_regularization=1.0, random_state=42),
        ),
        "xgboost": (
            XGBClassifier(n_estimators=450, max_depth=5, learning_rate=0.04, subsample=0.8,
                          colsample_bytree=0.8, min_child_weight=3, reg_lambda=2.0,
                          eval_metric="logloss", n_jobs=-1, random_state=42),
            XGBRegressor(n_estimators=450, max_depth=5, learning_rate=0.04, subsample=0.8,
                         colsample_bytree=0.8, min_child_weight=3, reg_lambda=2.0,
                         n_jobs=-1, random_state=42),
        ),
    }


def main():
    root = Path(__file__).resolve().parents[1]
    data = add_past_context(pd.read_csv(root / "outputs" / "xjtu_features.csv"))
    columns = feature_columns(data)
    X = data[columns]
    y = data["rul_minutes"].to_numpy(float)
    groups = data["bearing_id"].astype(str)
    late = y <= PROGNOSTIC_HORIZON_MINUTES
    splits = list(GroupKFold(n_splits=5).split(X, late, groups))
    results = []
    for name, (classifier, regressor) in candidates().items():
        probability = np.full(len(data), np.nan)
        rul = np.full(len(data), np.nan)
        for train_idx, test_idx in splits:
            classifier_pipeline = Pipeline([("imputer", SimpleImputer(strategy="median")), ("model", classifier)])
            regressor_pipeline = Pipeline([("imputer", SimpleImputer(strategy="median")), ("model", regressor)])
            if name == "xgboost":
                positives = max(int(late[train_idx].sum()), 1)
                negatives = len(train_idx) - positives
                classifier.set_params(scale_pos_weight=negatives / positives)
            classifier_pipeline.fit(X.iloc[train_idx], late[train_idx])
            probability[test_idx] = classifier_pipeline.predict_proba(X.iloc[test_idx])[:, 1]
            late_train = train_idx[late[train_idx]]
            regressor_pipeline.fit(X.iloc[late_train], y[late_train])
            rul[test_idx] = np.clip(regressor_pipeline.predict(X.iloc[test_idx]), 0, PROGNOSTIC_HORIZON_MINUTES)
        predicted = probability >= 0.5
        results.append({
            "model": name,
            "precision": float(precision_score(late, predicted, zero_division=0)),
            "recall": float(recall_score(late, predicted, zero_division=0)),
            "average_precision": float(average_precision_score(late, probability)),
            "roc_auc": float(roc_auc_score(late, probability)),
            "false_alarms": int(np.sum(predicted & ~late)),
            "missed_failure_windows": int(np.sum(~predicted & late)),
            "late_rul_mae_minutes": float(mean_absolute_error(y[late], rul[late])),
            "late_rul_error_90_minutes": float(np.quantile(np.abs(y[late] - rul[late]), 0.9, method="higher")),
        })
    output = root / "outputs" / "xjtu_candidate_benchmark.json"
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
