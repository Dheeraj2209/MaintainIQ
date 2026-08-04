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

# Temperature has a nonzero ambient baseline (~30-40C), so it scales
# additively (extra degrees) rather than multiplicatively like vibration.
TEMP_DELTA_C = {
    "healthy": 0.0,
    "degrading": 10.0,
    "faulty": 25.0,
    "critical": 40.0,
}

_FALLBACK = {
    "vibration_h_rms": 0.1,
    "vibration_h_kurtosis": 2.5,
    "vibration_h_high_band_energy_ratio": 0.05,
    "temperature_c": 35.0,
}


class UnknownMachineError(ValueError):
    pass


def _latest_reading(conn, machine_id: str) -> dict:
    row = conn.execute(
        """SELECT sensor_id, vibration_h_rms, vibration_h_kurtosis,
                  vibration_h_high_band_energy_ratio, temperature_c, rul_hours
           FROM readings WHERE machine_id = ? ORDER BY timestamp DESC LIMIT 1""",
        (machine_id,),
    ).fetchone()
    if row is None:
        raise UnknownMachineError(f"no baseline reading for machine: {machine_id}")
    return dict(row)


def evaluate_new_reading(conn, machine_id: str, target_state: str) -> dict:
    """Synthesize one reading for `machine_id` at `target_state` severity,
    persist it + its prediction, and return the prediction dict (reading_id,
    machine_id, timestamp, health_state, confidence, source, model_name,
    probable_cause, created_at)."""
    baseline = _latest_reading(conn, machine_id)
    multiplier = SEVERITY_MULTIPLIERS[target_state]
    temp_delta = TEMP_DELTA_C[target_state]
    timestamp = datetime.now(timezone.utc).isoformat()

    synthetic = {
        "machine_id": machine_id,
        "timestamp": timestamp,
        "sensor_id": baseline["sensor_id"],
        "vibration_h_rms": round((baseline["vibration_h_rms"] or _FALLBACK["vibration_h_rms"]) * multiplier, 4),
        "vibration_h_kurtosis": round((baseline["vibration_h_kurtosis"] or _FALLBACK["vibration_h_kurtosis"]) * multiplier, 4),
        "vibration_h_high_band_energy_ratio": round(
            (baseline["vibration_h_high_band_energy_ratio"] or _FALLBACK["vibration_h_high_band_energy_ratio"]) * multiplier, 4
        ),
        "temperature_c": round((baseline["temperature_c"] or _FALLBACK["temperature_c"]) + temp_delta, 2),
        "temperature_is_synthetic": 1,
        "rul_hours": baseline["rul_hours"],
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
