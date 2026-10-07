"""Model observability endpoints: heartbeat + rolling telemetry (Phase 5),
and real-world accuracy from operator feedback plus the retraining export
(design/2026-10-07-prediction-feedback-design.md).

Read-only. Mounted under Depends(get_current_user) in app.py, so any
authenticated user may read them (no PII in any response); the bulk feature
export is admin/supervisor-only, like the model_performance report.
"""
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.api.deps import get_db
from src.auth.deps import require_role
from src.feedback import accuracy as field_accuracy
from src.feedback import export as feedback_export
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


@router.get("/feedback-accuracy")
def feedback_accuracy_endpoint(
    db=Depends(get_db),
    model_version: Optional[str] = Query(None),
    period_start: Optional[str] = Query(None, description="ISO-8601, on alerts.created_at"),
    period_end: Optional[str] = Query(None, description="ISO-8601, on alerts.created_at"),
):
    try:
        return field_accuracy.real_world_accuracy(
            db, model_version=model_version, period_start=period_start, period_end=period_end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/feedback/export", dependencies=[Depends(require_role("admin", "supervisor"))])
def feedback_export_endpoint(db=Depends(get_db), model_version: Optional[str] = Query(None)):
    """Confirmed-failure field episodes as a build_feature_table-shaped CSV.
    Retraining stays a manual CLI step (README)."""
    result = feedback_export.build_export(db, model_version=model_version)
    filename = f"maintainiq-feedback-features-{datetime.now(timezone.utc):%Y%m%d}.csv"
    return Response(
        content=feedback_export.to_csv(result),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Episode-Count": str(result.episode_count),
            "X-Row-Count": str(len(result.rows)),
        },
    )
