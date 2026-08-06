"""ML-first, rule-based-fallback prediction routing (M3).

Per design/DESIGN_BASELINE.md's "Alert triggering" decision: use the
deployed ML model when it's available and confident, otherwise fall back to
the always-available rule-based classifier (src/prediction/rule_based.py).

The rule-based state for a reading has to be computed with the rest of its
machine's trajectory (it's a z-score against that machine's own healthy
baseline — see rule_based.py), so this router does not recompute it itself.
Callers pass in the already-computed rule-based state for the row alongside
an optional ML prediction, and get back whichever one wins plus which path
was used (needed downstream for prediction/alert provenance).
"""
import json
from pathlib import Path

# Fallback threshold if no exported evaluation report is available to derive
# one from (see src/training/export.py's confidence_threshold_analysis).
# 0.6 sits above the ~0.25 a 4-class model would get from guessing alone,
# without demanding near-certainty — the rule-based fallback exists
# specifically to catch the readings the ML model is genuinely unsure about.
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


def get_confidence_threshold(models_dir: Path = MODELS_DIR) -> float:
    """Read the data-driven threshold from the exported evaluation report
    (the midpoint between median confidence of correct vs. incorrect test
    predictions for the winning model), falling back to
    DEFAULT_CONFIDENCE_THRESHOLD if no report exists or the analysis wasn't
    available (e.g. winning model has no predict_proba).
    """
    report_path = models_dir / "evaluation_report.json"
    if not report_path.exists():
        return DEFAULT_CONFIDENCE_THRESHOLD
    report = json.loads(report_path.read_text())
    analysis = report.get("confidence_threshold_analysis", {})
    if analysis.get("available") and analysis.get("suggested_threshold") is not None:
        return analysis["suggested_threshold"]
    return DEFAULT_CONFIDENCE_THRESHOLD


def route_prediction(rule_based_state: str, ml_prediction: tuple = None,
                      threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> dict:
    """Pick the final health_state for one reading.

    ml_prediction: (predicted_state, confidence) from
    src/prediction/ml_model.py, or None if no model is deployed.
    """
    if ml_prediction is not None:
        ml_state, ml_confidence = ml_prediction
        if ml_confidence is not None and ml_confidence >= threshold:
            return {"health_state": ml_state, "confidence": ml_confidence, "source": "ml"}

    return {"health_state": rule_based_state, "confidence": None, "source": "rule_based"}
