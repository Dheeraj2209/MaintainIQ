"""Incremental (one-reading-at-a-time) counterpart to the batch pipeline in
run_pipeline.py. Built for the admin demo trigger
(src/api/routes/demo.py::simulate_fault), but not demo-only plumbing — a
real future M6 live telemetry feed would call evaluate_new_reading the same
way, once per incoming reading.

health_state is not re-derived via rule_based.py's z-score-against-baseline
classifier here: that classifier scores a reading against *that machine's
own* early-reading history, which a single synthesized demo reading has no
meaningful relationship to. Instead the caller states the target health_state
directly (that is the entire point of "simulate a fault at this severity"),
and this module's job is to synthesize sensor values consistent with that
state and run them through the real, single-row classify_probable_cause so
the reported root cause is still genuine, not hardcoded.
"""
from datetime import datetime, timezone

import pandas as pd

from src.root_cause.rule_based import ABNORMAL_STATES, classify_probable_cause
from src.storage.db import insert_predictions, insert_single_reading

# Applied to the machine's latest real reading to synthesize a demo reading
# at the requested severity. Vibration features have a near-zero healthy
# baseline, so they scale multiplicatively (same "documented heuristic
# constant" convention as root_cause/rule_based.py's thresholds).
SEVERITY_MULTIPLIERS = {
    "healthy": 1.0,
    "degrading": 2.5,
    "faulty": 5.0,
    "critical": 9.0,
}

# Used only when a machine's latest reading is missing a value (defensive).
_FALLBACK = {
    "vibration_h_rms": 0.1,
    "vibration_h_kurtosis": 2.5,
    "vibration_v_rms": 0.1,
    "vibration_v_kurtosis": 2.5,
    "cross_axis_rms_ratio": 1.0,
    "cross_axis_correlation": 0.0,
    "speed_rpm": 2100.0,
    "load_kn": 12.0,
    "sample_rate_hz": 25_600.0,
}


class UnknownMachineError(ValueError):
    pass


def _latest_reading(conn, machine_id: str) -> dict:
    row = conn.execute(
        """SELECT cycle, elapsed_minutes, speed_rpm, load_kn, sample_rate_hz,
                  vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
                  vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation
           FROM readings WHERE machine_id = ? ORDER BY timestamp DESC LIMIT 1""",
        (machine_id,),
    ).fetchone()
    if row is None:
        raise UnknownMachineError(f"no baseline reading for machine: {machine_id}")
    return dict(row)


def _scaled(baseline: dict, key: str, multiplier: float) -> float:
    return round((baseline.get(key) or _FALLBACK[key]) * multiplier, 4)


def evaluate_new_reading(conn, machine_id: str, target_state: str) -> dict:
    """Synthesize one reading for `machine_id` at `target_state` severity,
    persist it + its prediction, and return the prediction dict (reading_id,
    machine_id, timestamp, health_state, confidence, source, model_name,
    probable_cause, created_at)."""
    baseline = _latest_reading(conn, machine_id)
    multiplier = SEVERITY_MULTIPLIERS[target_state]
    timestamp = datetime.now(timezone.utc).isoformat()

    synthetic = {
        "machine_id": machine_id,
        "timestamp": timestamp,
        # Monotonic cycle from the latest reading keeps UNIQUE(machine_id, cycle).
        "cycle": int(baseline.get("cycle") or 0) + 1,
        "elapsed_minutes": float(baseline.get("elapsed_minutes") or 0.0) + 1.0,
        "speed_rpm": baseline.get("speed_rpm") or _FALLBACK["speed_rpm"],
        "load_kn": baseline.get("load_kn") if baseline.get("load_kn") is not None else _FALLBACK["load_kn"],
        "sample_rate_hz": baseline.get("sample_rate_hz") or _FALLBACK["sample_rate_hz"],
        "vibration_h_rms": _scaled(baseline, "vibration_h_rms", multiplier),
        "vibration_h_kurtosis": _scaled(baseline, "vibration_h_kurtosis", multiplier),
        "vibration_v_rms": _scaled(baseline, "vibration_v_rms", multiplier),
        "vibration_v_kurtosis": _scaled(baseline, "vibration_v_kurtosis", multiplier),
        "cross_axis_rms_ratio": baseline.get("cross_axis_rms_ratio") or _FALLBACK["cross_axis_rms_ratio"],
        "cross_axis_correlation": baseline.get("cross_axis_correlation") if baseline.get("cross_axis_correlation") is not None else _FALLBACK["cross_axis_correlation"],
        "rul_minutes": None,  # live rows carry predicted RUL in predictions, never a label
        "features_json": "{}",
        "dataset": "xjtu_sy",
    }

    reading_id = insert_single_reading(conn, synthetic)

    probable_cause = None
    if target_state in ABNORMAL_STATES:
        probable_cause = classify_probable_cause(pd.Series(synthetic))

    prediction = {
        "reading_id": reading_id,
        "machine_id": machine_id,
        "timestamp": timestamp,
        "health_state": target_state,
        "confidence": None,
        "source": "demo",
        "model_name": "demo_simulator",
        "probable_cause": probable_cause,
        "created_at": timestamp,
    }
    insert_predictions(conn, [prediction])
    return prediction
