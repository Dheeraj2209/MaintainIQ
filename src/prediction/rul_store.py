"""Persistence for RUL inference: predictions rows, inference-log rows, the
model registry, and cold-start rehydration of predictor state.

This module owns the predictor-dict -> DB-column mapping. It never computes a
feature or a prediction: it maps an already-produced predictor result dict
(src.prediction.rul_realtime.RealTimeRULPredictor.predict) into rows, and (in a
later task) replays already-extracted stored features back through the predictor
to rebuild in-memory state after a restart. Feature math stays single-homed in
src.ingestion.xjtu_sy.extract_snapshot_features.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_prediction(conn, result: dict, reading_id: int | None = None) -> int:
    """Map a predictor result dict into one predictions row. Returns the row id."""
    interval = result.get("prediction_interval_90_minutes") or [None, None]
    low = interval[0] if len(interval) > 0 else None
    high = interval[1] if len(interval) > 1 else None
    row = {
        "machine_id": result["machine_id"],
        "reading_id": reading_id,
        "timestamp": _now_iso(),
        "health_state": result["health_state"],
        "source": "xjtu_rul",
        "predicted_rul_minutes": result.get("predicted_rul_minutes"),
        "rul_estimate_kind": result.get("rul_estimate_kind"),
        "failure_within_horizon_probability": result.get("failure_within_horizon_probability"),
        "prognostic_horizon_minutes": result.get("prognostic_horizon_minutes"),
        "prediction_interval_low": low,
        "prediction_interval_high": high,
        "model_version": result.get("model_version"),
        "out_of_distribution": int(bool(result.get("out_of_distribution", False))),
        "history_snapshots": result.get("history_snapshots"),
        "warnings_json": json.dumps(result.get("warnings", [])),
    }
    cur = conn.execute(
        """INSERT INTO predictions
               (machine_id, reading_id, timestamp, health_state, source,
                predicted_rul_minutes, rul_estimate_kind,
                failure_within_horizon_probability, prognostic_horizon_minutes,
                prediction_interval_low, prediction_interval_high, model_version,
                out_of_distribution, history_snapshots, warnings_json)
           VALUES (:machine_id, :reading_id, :timestamp, :health_state, :source,
                   :predicted_rul_minutes, :rul_estimate_kind,
                   :failure_within_horizon_probability, :prognostic_horizon_minutes,
                   :prediction_interval_low, :prediction_interval_high, :model_version,
                   :out_of_distribution, :history_snapshots, :warnings_json)""",
        row,
    )
    conn.commit()
    return cur.lastrowid


def _warming_up(result: dict) -> int:
    return int(any(str(w).startswith("warming_up") for w in result.get("warnings", [])))


def log_inference(conn, *, machine_id: str, model_version: str, latency_ms: float,
                  result: dict | None = None, error: str | None = None) -> int:
    """Write one model_inference_log row. Pass `result` for a success row or
    `error` for a failure row (exactly one)."""
    if result is not None:
        row = {
            "machine_id": machine_id,
            "timestamp": _now_iso(),
            "model_version": model_version,
            "latency_ms": float(latency_ms),
            "failure_probability": result.get("failure_within_horizon_probability"),
            "predicted_rul_minutes": result.get("predicted_rul_minutes"),
            "out_of_distribution": int(bool(result.get("out_of_distribution", False))),
            "warming_up": _warming_up(result),
            "warnings_count": len(result.get("warnings", [])),
            "status": "ok",
            "error_message": None,
        }
    else:
        row = {
            "machine_id": machine_id,
            "timestamp": _now_iso(),
            "model_version": model_version,
            "latency_ms": float(latency_ms),
            "failure_probability": None,
            "predicted_rul_minutes": None,
            "out_of_distribution": 0,
            "warming_up": 0,
            "warnings_count": 0,
            "status": "error",
            "error_message": error,
        }
    cur = conn.execute(
        """INSERT INTO model_inference_log
               (machine_id, timestamp, model_version, latency_ms, failure_probability,
                predicted_rul_minutes, out_of_distribution, warming_up, warnings_count,
                status, error_message)
           VALUES (:machine_id, :timestamp, :model_version, :latency_ms,
                   :failure_probability, :predicted_rul_minutes, :out_of_distribution,
                   :warming_up, :warnings_count, :status, :error_message)""",
        row,
    )
    conn.commit()
    return cur.lastrowid


def register_active_model(conn, *, model_version: str, artifact_path: str,
                          algorithm: str | None = None,
                          metrics_json: str | None = None) -> None:
    """Idempotently record the active model and flag it active (deactivating all
    others). artifact_path MUST be repo-root-relative."""
    conn.execute(
        """INSERT INTO model_registry
               (model_version, artifact_path, algorithm, metrics_json, deployed_at, is_active)
           VALUES (:model_version, :artifact_path, :algorithm, :metrics_json, :deployed_at, 1)
           ON CONFLICT(model_version) DO UPDATE SET
               artifact_path = excluded.artifact_path,
               algorithm = excluded.algorithm,
               metrics_json = excluded.metrics_json,
               is_active = 1""",
        {
            "model_version": model_version,
            "artifact_path": artifact_path,
            "algorithm": algorithm,
            "metrics_json": metrics_json,
            "deployed_at": _now_iso(),
        },
    )
    conn.execute(
        "UPDATE model_registry SET is_active = 0 WHERE model_version != ?",
        (model_version,),
    )
    conn.commit()


def rehydrate(predictor, conn, machine_id: str) -> int:
    """Replay stored readings for machine_id back through the predictor so a
    cold instance rebuilds the same in-memory state a warm one would have.

    Uses the already-extracted feature vector in readings.features_json (never
    recomputes a feature). Readings without a stored feature vector (live/demo
    rows with features_json '{}') are skipped. Returns the number replayed.
    """
    cur = conn.execute(
        """SELECT speed_rpm, load_kn, sample_rate_hz, features_json
           FROM readings WHERE machine_id = ? ORDER BY cycle ASC""",
        (machine_id,),
    )
    replayed = 0
    for row in cur.fetchall():
        base = json.loads(row["features_json"] or "{}")
        if not base:
            continue
        predictor._predict_from_base(
            machine_id,
            base,
            sample_rate_hz=row["sample_rate_hz"],
            speed_rpm=row["speed_rpm"],
            load_kn=row["load_kn"],
        )
        replayed += 1
    return replayed
