"""Online prediction endpoints."""
import logging
import os
import time
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_db
from src.api.schemas import RULPredictionRequest, RULPredictionResponse
from src.prediction import health_epoch, pipeline, rul_store
from src.prediction.rul_realtime import RealTimeRULPredictor, StaleEpoch
from src.training.xjtu_rul import REPO_ROOT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictions", tags=["predictions"])

_STALE_DETAIL = "machine health was reset during this prediction; retry"


@lru_cache(maxsize=1)
def _cached_predictor() -> RealTimeRULPredictor:
    return RealTimeRULPredictor()


def get_predictor() -> RealTimeRULPredictor:
    """FastAPI dependency. Overridable in tests via app.dependency_overrides."""
    try:
        return _cached_predictor()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _relative_artifact_path(predictor: RealTimeRULPredictor) -> str:
    # model_registry.artifact_path must be repo-root-relative, never absolute.
    return os.path.relpath(predictor.artifact_path, REPO_ROOT).replace(os.sep, "/")


@router.post("/rul", response_model=RULPredictionResponse)
def predict_rul(payload: RULPredictionRequest, db=Depends(get_db), predictor=Depends(get_predictor)):
    model_version = predictor.artifact["model_version"]
    rul_store.register_active_model(
        db,
        model_version=model_version,
        artifact_path=_relative_artifact_path(predictor),
        algorithm=predictor.artifact.get("algorithm"),
    )
    start = time.perf_counter()
    try:
        # Synced to the machine's DB health epoch first (a restart, a reset or
        # a re-arm made elsewhere), atomically with the prediction.
        result = health_epoch.predict_synced(
            predictor, db, payload.machine_id,
            lambda: predictor.predict(
                machine_id=payload.machine_id,
                horizontal=np.asarray(payload.horizontal, dtype=np.float64),
                vertical=np.asarray(payload.vertical, dtype=np.float64),
                sample_rate_hz=payload.sample_rate_hz,
                speed_rpm=payload.speed_rpm,
                load_kn=payload.load_kn,
            ),
        )
    except StaleEpoch as exc:
        raise HTTPException(status_code=409, detail=_STALE_DETAIL) from exc
    except ValueError as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        rul_store.log_inference(
            db, machine_id=payload.machine_id, model_version=model_version,
            latency_ms=latency_ms, error=str(exc),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    latency_ms = (time.perf_counter() - start) * 1000.0
    prediction_id = rul_store.persist_prediction(db, result, reading_id=None)
    rul_store.log_inference(
        db, machine_id=payload.machine_id, model_version=model_version,
        latency_ms=latency_ms, result=result,
    )
    if prediction_id is None:
        # The machine was reset or re-armed while this prediction ran: the
        # result belongs to an old episode, so it was not stored and must not
        # open, resolve or page an alert (plan D6).
        raise HTTPException(status_code=409, detail=_STALE_DETAIL)
    # Same fan-out the replay ingestion path uses: the prediction row is the
    # durable record, but an operator only hears about a failing machine if it
    # also reaches the alert state machine, email paging and the realtime feed.
    # Failures there are logged and dropped rather than costing the caller the
    # prediction that already succeeded.
    try:
        pipeline.handle_prediction(
            db, result,
            source=rul_store.PREDICTION_SOURCE,
            features=result.get("input_features") or {},
            # No reading is stored on this route, so only the prediction links.
            prediction_id=prediction_id,
        )
    except Exception:
        logger.exception("Alert fan-out failed for %s", payload.machine_id)
    return result


@router.delete("/rul/{machine_id}/state")
def reset_rul_state(machine_id: str, predictor=Depends(get_predictor)):
    """Reset rolling context after maintenance, sensor movement, or replacement."""
    predictor.reset_machine(machine_id)
    return {"machine_id": machine_id, "status": "reset"}
