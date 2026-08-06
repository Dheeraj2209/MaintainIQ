"""Export the M2b benchmark winner as a deployable artifact.

Writes two files to `models/`: the fitted sklearn estimator/pipeline
(joblib) and a JSON evaluation report covering every candidate's metrics,
not just the winner's — so the benchmark comparison stays auditable after
the fact instead of only existing in a console printout.

Also derives the ML/rule-based routing confidence threshold used by
src/prediction/router.py, from the winner's own test-set confidence-vs-
correctness split (see `confidence_threshold_analysis`). Doing this here,
at export time, keeps the threshold tied to the model it was measured
against instead of being a number pulled out of the router module in
isolation.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


def confidence_threshold_analysis(model, test_df: pd.DataFrame, feature_cols: list) -> dict:
    """Compare mean confidence of correct vs. incorrect predictions on the
    test split, and suggest a threshold between the two medians.

    This is a coarse, single-split estimate (not cross-validated) — good
    enough to justify a router constant, not a claim of a calibrated
    probability cutoff. Flagged here rather than presented as precise.
    """
    X_test, y_test = test_df[feature_cols], test_df["health_state"]
    y_pred = model.predict(X_test)
    if not hasattr(model, "predict_proba"):
        return {"available": False}

    confidences = model.predict_proba(X_test).max(axis=1)
    correct = pd.Series(y_pred, index=y_test.index) == y_test
    correct_conf = confidences[correct.values]
    incorrect_conf = confidences[~correct.values]

    median_correct = float(np.median(correct_conf)) if len(correct_conf) else None
    median_incorrect = float(np.median(incorrect_conf)) if len(incorrect_conf) else None
    if median_correct is not None and median_incorrect is not None:
        suggested_threshold = round((median_correct + median_incorrect) / 2, 3)
    elif median_correct is not None:
        suggested_threshold = round(median_correct * 0.75, 3)
    else:
        suggested_threshold = 0.5

    return {
        "available": True,
        "median_confidence_correct": median_correct,
        "median_confidence_incorrect": median_incorrect,
        "n_correct": int(correct.sum()),
        "n_incorrect": int((~correct).sum()),
        "suggested_threshold": suggested_threshold,
    }


def export_winner(results: dict, winner_name: str, feature_cols: list, test_df: pd.DataFrame,
                   output_dir: Path = MODELS_DIR) -> dict:
    """Persist the winning model + a full evaluation report to `output_dir`.

    Returns the evaluation report dict that was written (including the
    threshold analysis), so callers can use it immediately without a
    round-trip through disk.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    winner_model = results[winner_name]["model"]

    model_path = output_dir / "stage_classifier.joblib"
    joblib.dump(winner_model, model_path)

    threshold_info = confidence_threshold_analysis(winner_model, test_df, feature_cols)

    report = {
        "winner": winner_name,
        "feature_columns": feature_cols,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "candidates": {
            name: {
                "accuracy": float(r["accuracy"]),
                "false_alarm_count": r["false_alarm_count"],
                "missed_fault_count": r["missed_fault_count"],
                "mean_confidence": r["mean_confidence"],
                "confusion_matrix": r["confusion_matrix"],
                "labels": r["labels"],
            }
            for name, r in results.items()
        },
        "confidence_threshold_analysis": threshold_info,
    }

    report_path = output_dir / "evaluation_report.json"
    report_path.write_text(json.dumps(report, indent=2))

    return report
