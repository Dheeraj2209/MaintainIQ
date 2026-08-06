"""Stateful real-time RUL inference from dual-axis vibration snapshots."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

import joblib
import numpy as np
import pandas as pd

from src.ingestion.xjtu_sy import extract_snapshot_features
from src.training.xjtu_rul import (
    BASELINE_WINDOW,
    DEFAULT_ARTIFACT,
    ROLLING_WINDOWS,
    add_past_context,
)


class RealTimeRULPredictor:
    """Keep causal feature history per machine and predict one snapshot at a time.

    State is intentionally in memory for the academic service. A production
    deployment should persist the compact feature history in a time-series
    database so process restarts do not discard context.
    """

    def __init__(self, artifact_path: Path = DEFAULT_ARTIFACT, max_history: int | None = None):
        if not artifact_path.exists():
            raise FileNotFoundError(
                f"RUL model not found at {artifact_path}; run "
                "python -m src.training.xjtu_rul first"
            )
        self.artifact_path = Path(artifact_path)
        artifact = joblib.load(artifact_path)
        required = {
            "regressor",
            "conformal_error_90_minutes", "model_version",
            "prognostic_horizon_minutes", "failure_probability_threshold",
        }
        if "classifiers" not in artifact and "classifier" not in artifact:
            required.add("classifier")
        if not required.issubset(artifact):
            raise ValueError(f"invalid RUL artifact; missing {sorted(required.difference(artifact))}")
        self.artifact = artifact
        self.classifiers = artifact.get("classifiers", [artifact.get("classifier")])
        self.regressor = artifact["regressor"]
        legacy_features = artifact.get("feature_columns")
        self.classifier_feature_columns = artifact.get(
            "classifier_feature_columns", legacy_features
        )
        self.regressor_feature_columns = artifact.get(
            "regressor_feature_columns", legacy_features
        )
        if not self.classifier_feature_columns or not self.regressor_feature_columns:
            raise ValueError("invalid RUL artifact; missing trained feature columns")
        self.max_history = max_history or max(ROLLING_WINDOWS)
        if self.max_history < max(ROLLING_WINDOWS):
            raise ValueError(
                f"max_history must be at least {max(ROLLING_WINDOWS)} snapshots"
            )
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=self.max_history))
        self._baseline_history: dict[str, list] = defaultdict(list)
        self._cycles: dict[str, int] = defaultdict(int)
        smoothing_window = int(artifact.get("probability_smoothing_window", 1))
        self._probability_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=smoothing_window)
        )
        persistence = int(artifact.get("warning_persistence_snapshots", 1))
        self._warning_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=persistence)
        )
        self._locks: dict[str, Lock] = defaultdict(Lock)

    def reset_machine(self, machine_id: str) -> None:
        with self._locks[machine_id]:
            self._history.pop(machine_id, None)
            self._baseline_history.pop(machine_id, None)
            self._cycles.pop(machine_id, None)
            self._probability_history.pop(machine_id, None)
            self._warning_history.pop(machine_id, None)

    def _health_state(self, rul_minutes: float) -> str:
        thresholds = self.artifact.get(
            "health_thresholds_minutes",
            {"critical": 30.0, "faulty": 180.0, "degrading": 720.0},
        )
        if rul_minutes <= thresholds["critical"]:
            return "critical"
        if rul_minutes <= thresholds["faulty"]:
            return "faulty"
        if rul_minutes <= thresholds["degrading"]:
            return "degrading"
        return "healthy"

    def predict(
        self,
        machine_id: str,
        horizontal: np.ndarray,
        vertical: np.ndarray,
        sample_rate_hz: float,
        speed_rpm: float,
        load_kn: float,
    ) -> dict:
        if not machine_id.strip():
            raise ValueError("machine_id must not be empty")
        if speed_rpm <= 0 or load_kn < 0:
            raise ValueError("speed_rpm must be positive and load_kn must be non-negative")
        base = extract_snapshot_features(horizontal, vertical, sample_rate_hz)

        with self._locks[machine_id]:
            history = self._history[machine_id]
            cycle = self._cycles[machine_id]
            self._cycles[machine_id] += 1
            snapshot = {
                "bearing_id": machine_id,
                "cycle": cycle,
                "elapsed_minutes": float(cycle),
                "speed_rpm": float(speed_rpm),
                "load_kn": float(load_kn),
                **base,
            }
            history.append(snapshot)
            baseline_history = self._baseline_history[machine_id]
            baseline_window = int(self.artifact.get("baseline_window", BASELINE_WINDOW))
            if len(baseline_history) < baseline_window:
                baseline_history.append(snapshot)
            # Retain the commissioning baseline even after it falls out of the
            # recent rolling window. De-duplicate cycles while the two overlap.
            context_rows = {
                row["cycle"]: row for row in [*baseline_history, *history]
            }
            context = add_past_context(pd.DataFrame(context_rows.values()))
            latest = context.iloc[[-1]].copy()
            snapshots_seen = cycle + 1

        needed_features = set(self.classifier_feature_columns).union(
            self.regressor_feature_columns
        )
        missing = needed_features.difference(latest.columns)
        if missing:
            raise ValueError(f"live feature vector is missing trained features: {sorted(missing)}")
        X_classifier = latest[self.classifier_feature_columns]
        raw_failure_probability = float(np.mean([
            classifier.predict_proba(X_classifier)[0, 1]
            for classifier in self.classifiers
        ]))
        with self._locks[machine_id]:
            probability_history = self._probability_history[machine_id]
            probability_history.append(raw_failure_probability)
            failure_probability = float(np.median(probability_history))
            threshold = float(self.artifact["failure_probability_threshold"])
            warning_history = self._warning_history[machine_id]
            warning_history.append(failure_probability >= threshold)
            required_persistence = int(
                self.artifact.get("warning_persistence_snapshots", 1)
            )
            within_horizon = (
                len(warning_history) == required_persistence and all(warning_history)
            )
            confirmation_count = 0
            for value in reversed(warning_history):
                if not value:
                    break
                confirmation_count += 1
        horizon = float(self.artifact["prognostic_horizon_minutes"])
        if within_horizon:
            X_regressor = latest[self.regressor_feature_columns]
            raw_prediction = float(self.regressor.predict(X_regressor)[0])
            predicted = min(horizon, max(0.0, raw_prediction))
            estimate_kind = "point_estimate"
        else:
            predicted = horizon
            estimate_kind = "lower_bound"
        error = float(self.artifact["conformal_error_90_minutes"])

        bounds = self.artifact.get("feature_bounds_99pct", {})
        outside = []
        for feature, (low, high) in bounds.items():
            value = float(latest.iloc[0][feature])
            if np.isfinite(value) and (value < low or value > high):
                outside.append(feature)
        outside_fraction = len(outside) / max(len(bounds), 1)
        warnings = []
        if snapshots_seen < max(ROLLING_WINDOWS):
            warnings.append(
                f"warming_up: {snapshots_seen}/{max(ROLLING_WINDOWS)} snapshots; rolling context is incomplete"
            )
        expected_rate = float(self.artifact.get("sample_rate_hz", 25_600.0))
        if abs(sample_rate_hz - expected_rate) / expected_rate > 0.05:
            warnings.append(
                f"sample_rate_mismatch: model={expected_rate:g}Hz input={sample_rate_hz:g}Hz"
            )
        if outside_fraction > 0.10:
            warnings.append(
                "out_of_distribution: input differs materially from XJTU-SY; "
                "do not use this estimate for maintenance decisions until target-machine validation"
            )
        if failure_probability >= threshold and not within_horizon:
            warnings.append(
                "confirming_warning: late-life signature must persist for "
                f"{required_persistence} snapshots "
                f"({confirmation_count}/{required_persistence} confirmed)"
            )
        if not within_horizon:
            warnings.append(
                f"rul_lower_bound: no late-life signature detected; RUL is estimated as greater than {horizon:g} minutes"
            )

        interval = (
            [max(0.0, predicted - error), min(horizon, predicted + error)]
            if within_horizon else [horizon, None]
        )

        return {
            "machine_id": machine_id,
            "predicted_rul_minutes": predicted,
            "predicted_rul_hours": predicted / 60.0,
            "rul_estimate_kind": estimate_kind,
            "prognostic_horizon_minutes": horizon,
            "failure_within_horizon_probability": failure_probability,
            "raw_failure_within_horizon_probability": raw_failure_probability,
            "warning_persistence_snapshots": required_persistence,
            "prediction_interval_90_minutes": interval,
            "health_state": self._health_state(predicted) if within_horizon else "healthy",
            "model_version": self.artifact["model_version"],
            "history_snapshots": snapshots_seen,
            "out_of_distribution": outside_fraction > 0.10,
            "outside_training_features": outside[:20],
            "warnings": warnings,
        }
