"""Candidate model training and benchmarking for health-state classification.

Trains every candidate in CANDIDATE_MODELS on the same time-based
train/test split (per machine_id) and benchmarks them on the SRS's own KPIs
(design/SRS_Document.md): prediction accuracy, false alarm count, missed
fault count, confidence score. The winner is exported for
src/prediction/ml_model.py to load; the rule-based classifier
(src/prediction/rule_based.py) stays as the always-available fallback.

RandomForest here is carried over from research/ims_bearing_baseline/src/
pipeline.py's stage classifier, which validated well against real IMS
bearing fault data. GradientBoosting, SVM, and LogisticRegression are added
as comparison candidates per design/DESIGN_BASELINE.md's candidate list.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

HEALTH_STATES = ["healthy", "degrading", "faulty", "critical"]
FAULT_STATES = {"faulty", "critical"}

# Feature scales vary by orders of magnitude (RMS ~0.1 vs. spectral energy).
# Tree ensembles are scale-invariant, but logistic regression and SVM are
# not — leaving them unscaled understates their real accuracy and makes the
# benchmark comparison unfair, so they get a StandardScaler step.
CANDIDATE_MODELS = {
    "random_forest": lambda: RandomForestClassifier(
        n_estimators=300, max_depth=12, class_weight="balanced", random_state=42
    ),
    "gradient_boosting": lambda: GradientBoostingClassifier(random_state=42),
    "logistic_regression": lambda: make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42),
    ),
    "svm": lambda: make_pipeline(
        StandardScaler(),
        SVC(class_weight="balanced", probability=True, random_state=42),
    ),
}


def time_based_split(long_df: pd.DataFrame, test_fraction: float = 0.2):
    """Split each machine's own trajectory chronologically — the last
    `test_fraction` of its readings become test data. Mirrors the split used
    in the research baseline so results stay comparable.
    """
    train_idx, test_idx = [], []
    for _, group in long_df.groupby("machine_id"):
        ordered = group.sort_values("timestamp")
        n_test = max(1, int(len(ordered) * test_fraction))
        train_idx.extend(ordered.index[:-n_test])
        test_idx.extend(ordered.index[-n_test:])
    return long_df.loc[train_idx], long_df.loc[test_idx]


def feature_columns(df: pd.DataFrame) -> list:
    """Only the primary ('vibration_h_') channel plus temperature. Test 1 has
    a second ('vibration_v_') channel that Tests 2/3 don't, so including it
    would introduce NaNs once all three tests are combined — same tradeoff
    the research baseline made for its cross-test models."""
    return [c for c in df.columns if c.startswith("vibration_h_") or c == "temperature_c"]


def benchmark_candidate(model, train_df: pd.DataFrame, test_df: pd.DataFrame) -> dict:
    feature_cols = feature_columns(train_df)
    X_train, y_train = train_df[feature_cols], train_df["health_state"]
    X_test, y_test = test_df[feature_cols], test_df["health_state"]

    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    confidences = model.predict_proba(X_test).max(axis=1) if hasattr(model, "predict_proba") else None

    labels = [s for s in HEALTH_STATES if s in set(y_test) | set(y_pred)]
    report = classification_report(y_test, y_pred, digits=3, labels=labels, output_dict=True)
    accuracy = report["accuracy"]

    actual_fault = y_test.isin(FAULT_STATES)
    predicted_fault = pd.Series(y_pred, index=y_test.index).isin(FAULT_STATES)
    false_alarm_count = int((predicted_fault & ~actual_fault).sum())
    missed_fault_count = int((~predicted_fault & actual_fault).sum())

    return {
        "accuracy": accuracy,
        "false_alarm_count": false_alarm_count,
        "missed_fault_count": missed_fault_count,
        "mean_confidence": float(np.mean(confidences)) if confidences is not None else None,
        "confusion_matrix": confusion_matrix(y_test, y_pred, labels=labels).tolist(),
        "labels": labels,
        "model": model,
    }


def run_benchmark(long_df: pd.DataFrame) -> dict:
    """Train and benchmark every candidate; return {name: benchmark_result}."""
    train_df, test_df = time_based_split(long_df)
    return {
        name: benchmark_candidate(factory(), train_df, test_df)
        for name, factory in CANDIDATE_MODELS.items()
    }


def select_winner(results: dict) -> str:
    """Pick the candidate with the highest accuracy, breaking ties by fewer
    missed faults (a missed fault is worse than a false alarm for a
    maintenance safety-net use case).
    """
    return max(
        results,
        key=lambda name: (results[name]["accuracy"], -results[name]["missed_fault_count"]),
    )
