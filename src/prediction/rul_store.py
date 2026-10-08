"""Persistence for RUL inference: predictions rows, inference-log rows, the
model registry, and cold-start rehydration of predictor state.

This module owns the predictor-dict -> DB-column mapping. It never computes a
feature or a prediction: it maps an already-produced predictor result dict
(src.prediction.rul_realtime.RealTimeRULPredictor.predict) into rows, and
replays already-extracted stored features back through the predictor to
rebuild in-memory state after a restart. Feature math stays single-homed in
src.ingestion.xjtu_sy.extract_snapshot_features.

Persist is guarded by the machine's alert episode: a result whose episode was
overtaken by a reset or re-arm is not written. Rehydrate replays one health
epoch, bounded to the rows the predictor state depends on
(docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md D5, D6).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import weakref
from datetime import datetime, timezone

from src.prediction import health_epoch
from src.training.xjtu_rul import BASELINE_WINDOW, ROLLING_SOURCE_COLUMNS, ROLLING_WINDOWS

logger = logging.getLogger(__name__)


# The `source` stamped on every row the model produces — prediction rows and,
# via src/prediction/pipeline.py, the alerts they raise. One definition so an
# alert can always be traced back to the model run rather than a demo or a
# manual entry.
PREDICTION_SOURCE = "xjtu_rul"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_COLUMNS = (
    "machine_id", "reading_id", "timestamp", "health_state", "source",
    "predicted_rul_minutes", "rul_estimate_kind",
    "failure_within_horizon_probability", "prognostic_horizon_minutes",
    "prediction_interval_low", "prediction_interval_high", "model_version",
    "out_of_distribution", "history_snapshots", "warnings_json",
)
_HEALTH_COLUMNS = ("health_epoch", "health_episode", "instant_health_state")


def _legacy_insert(conn, row: dict) -> int:
    cur = conn.execute(
        f"""INSERT INTO predictions ({', '.join(_COLUMNS)})
            VALUES ({', '.join(':' + c for c in _COLUMNS)})""",
        {c: row[c] for c in _COLUMNS},
    )
    conn.commit()
    return cur.lastrowid


def _latches(result: dict) -> bool:
    """Whether the predictor held this result's state (the latch rule of
    rul_realtime): ratchet on, past commissioning (D3) and in distribution
    (D8). Only such a row may raise the DB held level."""
    return (bool(result.get("health_ratchet"))
            and not result.get("commissioning")
            and not result.get("out_of_distribution"))


def persist_prediction(conn, result: dict, reading_id: int | None = None) -> int | None:
    """Map a predictor result dict into one predictions row. Returns the row
    id, or None when the result is stale (plan D6).

    A result tagged with a health_episode is written by one conditional
    INSERT ... SELECT that matches only while the machine is still at that
    episode: after a reset or re-arm it inserts nothing, and the caller must
    then skip the alert fan-out. In the same transaction a latched result
    raises the DB held level (health_epoch.record_level, itself conditional
    on the episode). A result without an episode (fakes, the demo, an old
    DB) uses the legacy INSERT with NULL health columns."""
    interval = result.get("prediction_interval_90_minutes") or [None, None]
    low = interval[0] if len(interval) > 0 else None
    high = interval[1] if len(interval) > 1 else None
    row = {
        "machine_id": result["machine_id"],
        "reading_id": reading_id,
        "timestamp": _now_iso(),
        "health_state": result["health_state"],
        "source": PREDICTION_SOURCE,
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
    episode = result.get("health_episode")
    if episode is None:
        return _legacy_insert(conn, row)

    row.update({
        "health_epoch": result.get("health_epoch"),
        "health_episode": episode,
        "instant_health_state": result.get("instant_health_state"),
    })
    columns = (*_COLUMNS, *_HEALTH_COLUMNS)
    try:
        cur = conn.execute(
            f"""INSERT INTO predictions ({', '.join(columns)})
                SELECT {', '.join(':' + c for c in columns)}
                WHERE COALESCE((SELECT episode FROM machine_health_state
                                WHERE machine_id = :machine_id), 0) = :health_episode""",
            row,
        )
    except sqlite3.OperationalError as exc:
        # An old DB without migration 008 (opened outside get_db): the
        # statement fails to prepare, so nothing was written. Not cached per
        # connection (sqlite3 connections cannot be weak-referenced, and an
        # id() can be reused); the failed prepare is cheap.
        if "no such" not in str(exc):
            raise
        return _legacy_insert(conn, row)
    if cur.rowcount == 0:
        conn.rollback()
        return None
    try:
        if _latches(result):
            health_epoch.record_level(conn, row["machine_id"], episode,
                                      result["health_state"], row["model_version"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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


# Snapshot meta the predictor builds from the call arguments, not from the
# stored feature vector.
_SNAPSHOT_META = {"speed_rpm", "load_kn", "elapsed_minutes", "cycle", "bearing_id"}
# Trained columns that add_past_context derives from ROLLING_SOURCE_COLUMNS.
_DERIVED = {
    f"{column}{suffix}"
    for column in ROLLING_SOURCE_COLUMNS
    for suffix in ("_baseline_ratio", "_baseline_delta",
                   *(f"_{kind}_{w}" for kind in ("mean", "std", "trend") for w in ROLLING_WINDOWS))
}
_REQUIRED_KEYS = weakref.WeakKeyDictionary()


def _required_base_keys(predictor) -> frozenset:
    """The keys a stored features_json must carry for `predictor`, computed
    once per predictor: the rolling source columns plus every trained column
    that is neither derived by add_past_context nor snapshot meta."""
    keys = _REQUIRED_KEYS.get(predictor)
    if keys is None:
        trained = set(predictor.classifier_feature_columns) | set(predictor.regressor_feature_columns)
        keys = frozenset(set(ROLLING_SOURCE_COLUMNS) | (trained - _DERIVED - _SNAPSHOT_META))
        _REQUIRED_KEYS[predictor] = keys
    return keys


def _stored_base(row, required: frozenset) -> dict | None:
    """The row's stored feature vector, or None (logged) when it is empty,
    unparsable or missing a required key. Checked before predicting, so a
    bad row never half-mutates the predictor's deques."""
    try:
        base = json.loads(row["features_json"] or "{}")
    except (TypeError, ValueError):
        logger.warning("rehydrate: skipping reading %s: unparsable features_json", row["reading_id"])
        return None
    if not isinstance(base, dict) or not base:
        logger.warning("rehydrate: skipping reading %s: no stored feature vector", row["reading_id"])
        return None
    missing = required.difference(base)
    if missing:
        logger.warning("rehydrate: skipping reading %s: missing features %s",
                       row["reading_id"], sorted(missing))
        return None
    return base


def rehydrate(predictor, conn, machine_id: str, *, epoch: int, full: bool = False,
              on_result=None) -> int:
    """Replay the stored readings of one health epoch back through the
    predictor so a cold instance rebuilds the state a warm one would have
    (plan D5). Call it on a freshly reset machine; restore_health afterwards
    sets the DB-authoritative held level.

    Only readings joined to predictions of `epoch` are replayed: an older
    epoch (the replaced component), pre-ratchet rows (health_epoch NULL) and
    readings that were never predicted are not. The state depends only on
    the epoch's first baseline_window rows and its last max_history rows, so
    only those are replayed, each at the cycle a warm predictor gave it
    (its ordinal - 1): the cost is at most baseline_window + max_history
    predictions whatever the epoch length. full=True replays every row (a
    model change, D10).

    Uses the already-extracted feature vector in readings.features_json (never
    recomputes a feature). A row that is empty, unparsable or missing a
    trained feature is skipped and logged. Returns the number replayed.
    on_result(row, result), if given, sees each replayed row (with its
    health_episode) and the result it produced (the D10 re-derive).
    """
    baseline_window = int(predictor.artifact.get("baseline_window", BASELINE_WINDOW))
    bound = "" if full else "WHERE n <= :head OR n > total - :tail"
    rows = conn.execute(
        f"""WITH e AS (
                SELECT r.id AS reading_id, r.speed_rpm, r.load_kn, r.sample_rate_hz,
                       r.features_json, p.health_episode,
                       ROW_NUMBER() OVER (ORDER BY p.id) AS n, COUNT(*) OVER () AS total
                FROM predictions p JOIN readings r ON r.id = p.reading_id
                WHERE p.machine_id = :m AND p.health_epoch = :epoch)
            SELECT * FROM e {bound} ORDER BY n""",
        {"m": machine_id, "epoch": epoch, "head": baseline_window,
         "tail": predictor.max_history},
    ).fetchall()
    required = _required_base_keys(predictor)
    usable = [(row, base) for row in rows
              if (base := _stored_base(row, required)) is not None]
    # The model's output only feeds the probability-smoothing and
    # warning-persistence deques (the held level is restored from the DB
    # afterwards), so only the rows that can still reach them need inference:
    # the last smoothing + persistence - 1. Earlier rows just rebuild the
    # feature history. A full replay or an on_result caller (the D10
    # re-derive) needs every row's result, so it predicts them all.
    if full or on_result is not None:
        first_predicted = 0
    else:
        smoothing = int(predictor.artifact.get("probability_smoothing_window", 1))
        persistence = int(predictor.artifact.get("warning_persistence_snapshots", 1))
        first_predicted = max(0, len(usable) - (smoothing + persistence - 1))
    replayed = 0
    for index, (row, base) in enumerate(usable):
        # Across the skipped middle of a long epoch (and any skipped row)
        # each row keeps the cycle number a warm predictor gave it.
        predictor._seek_cycle(machine_id, row["n"] - 1)
        if index < first_predicted:
            predictor._observe_base(
                machine_id, base, speed_rpm=row["speed_rpm"], load_kn=row["load_kn"]
            )
            replayed += 1
            continue
        result = predictor._predict_from_base(
            machine_id,
            base,
            sample_rate_hz=row["sample_rate_hz"],
            speed_rpm=row["speed_rpm"],
            load_kn=row["load_kn"],
        )
        if on_result is not None:
            on_result(row, result)
        replayed += 1
    if rows:
        predictor._seek_cycle(machine_id, rows[-1]["total"])
    return replayed
