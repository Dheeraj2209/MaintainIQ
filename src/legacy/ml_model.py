"""Loads the M2b-exported model artifact and produces per-row predictions.

The artifact (models/stage_classifier.joblib + models/evaluation_report.json)
is written by src/training/export.py after the candidate benchmark in
src/training/stage_classifiers.py picks a winner. This module only knows how
to load and use that artifact — it has no training logic of its own.
"""
import json
from pathlib import Path

import pandas as pd

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


class DeployedModel:
    def __init__(self, model, feature_columns: list, winner_name: str):
        self.model = model
        self.feature_columns = feature_columns
        self.winner_name = winner_name

    def predict_row(self, row: pd.Series) -> tuple:
        """Return (predicted_health_state, confidence) for one feature row.

        confidence is the winning class's predicted probability, or None if
        the underlying estimator doesn't support predict_proba.
        """
        X = pd.DataFrame([row[self.feature_columns]])
        predicted_state = self.model.predict(X)[0]
        if hasattr(self.model, "predict_proba"):
            confidence = float(self.model.predict_proba(X).max())
        else:
            confidence = None
        return predicted_state, confidence


def load_deployed_model(models_dir: Path = MODELS_DIR) -> DeployedModel:
    """Load the exported model + its metadata. Returns None if no artifact
    has been exported yet — callers (src/prediction/router.py) must treat
    that as "fall back to rule-based", not an error.
    """
    model_path = models_dir / "stage_classifier.joblib"
    report_path = models_dir / "evaluation_report.json"
    if not model_path.exists() or not report_path.exists():
        return None

    import joblib
    model = joblib.load(model_path)
    report = json.loads(report_path.read_text())
    return DeployedModel(model, report["feature_columns"], report["winner"])
