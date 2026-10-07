"""Prediction feedback: what actually happened after an alert, and what that
says about the model (design/2026-10-07-prediction-feedback-design.md).

service  — close an alert / record its outcome (one editable row per alert)
accuracy — real-world accuracy from those outcomes
export   — field episodes as a build_feature_table-shaped CSV for retraining
"""
