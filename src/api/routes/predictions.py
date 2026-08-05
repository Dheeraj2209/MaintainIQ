"""Online prediction endpoints."""
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, HTTPException

from src.api.schemas import RULPredictionRequest, RULPredictionResponse
from src.prediction.rul_realtime import RealTimeRULPredictor

router = APIRouter(prefix="/predictions", tags=["predictions"])


@lru_cache(maxsize=1)
def _predictor() -> RealTimeRULPredictor:
    return RealTimeRULPredictor()


@router.post("/rul", response_model=RULPredictionResponse)
def predict_rul(payload: RULPredictionRequest):
    try:
        predictor = _predictor()
        return predictor.predict(
            machine_id=payload.machine_id,
            horizontal=np.asarray(payload.horizontal, dtype=np.float64),
            vertical=np.asarray(payload.vertical, dtype=np.float64),
            sample_rate_hz=payload.sample_rate_hz,
            speed_rpm=payload.speed_rpm,
            load_kn=payload.load_kn,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/rul/{machine_id}/state")
def reset_rul_state(machine_id: str):
    """Reset rolling context after maintenance, sensor movement, or replacement."""
    try:
        _predictor().reset_machine(machine_id)
        return {"machine_id": machine_id, "status": "reset"}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
