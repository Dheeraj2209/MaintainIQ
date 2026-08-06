"""Model observability endpoints: heartbeat + rolling telemetry (Phase 5).

Read-only. Mounted under Depends(get_current_user) in app.py, so any
authenticated user may read them (no PII in either response).
"""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_db
from src.observability import model_health, telemetry

router = APIRouter(prefix="/model", tags=["model"])

_HEARTBEAT_ENV = "MAINTAINIQ_MODEL_HEARTBEAT_SECONDS"


def _stale_after_seconds() -> float:
    raw = os.environ.get(_HEARTBEAT_ENV)
    if raw is None:
        return model_health.DEFAULT_STALE_AFTER_SECONDS
    try:
        return float(raw)
    except ValueError:
        return model_health.DEFAULT_STALE_AFTER_SECONDS


@router.get("/health")
def model_health_endpoint(db=Depends(get_db)):
    return model_health.compute_health(
        db,
        now=datetime.now(timezone.utc),
        stale_after_seconds=_stale_after_seconds(),
    )


@router.get("/telemetry")
def model_telemetry_endpoint(
    db=Depends(get_db),
    window_minutes: float = Query(default=telemetry.DEFAULT_WINDOW_MINUTES, gt=0),
):
    return telemetry.compute_telemetry(
        db,
        now=datetime.now(timezone.utc),
        window_minutes=window_minutes,
    )
