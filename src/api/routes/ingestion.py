"""Streaming replay ingestion-control endpoints (Phase 4).

Drives stored readings back through the live predict+persist path via
src.ingestion.replay_service.ReplayService. start/stop are supervisor+
(ingestion control); status is any authenticated role (router mounted under
get_current_user in src/api/app.py). The service is a process singleton,
resolved via get_replay_service so tests can override it.
"""
from fastapi import APIRouter, Depends, HTTPException

from src.api.schemas import ReplayStartRequest, ReplayStopRequest
from src.auth.deps import require_role
from src.ingestion.replay_service import ReplayService
from src.storage.db import get_connection

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_service: ReplayService | None = None


def _default_predictor():
    # Imported lazily so importing this route module does not build the predictor
    # cache; the FileNotFoundError (no trained model) then surfaces only when a
    # replay actually runs, not at app import.
    from src.api.routes.predictions import _cached_predictor

    return _cached_predictor()


def get_replay_service() -> ReplayService:
    """FastAPI dependency. Overridable in tests via app.dependency_overrides."""
    global _service
    if _service is None:
        _service = ReplayService(
            predictor_provider=_default_predictor,
            connection_factory=get_connection,
        )
    return _service


@router.post("/replay/start")
def start_replay(
    payload: ReplayStartRequest,
    _user=Depends(require_role("admin", "supervisor")),
    svc: ReplayService = Depends(get_replay_service),
):
    try:
        svc.start(payload.machine_id, payload.speed_multiplier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "machine_id": payload.machine_id,
        "status": "started",
        "speed_multiplier": payload.speed_multiplier,
    }


@router.post("/replay/stop")
def stop_replay(
    payload: ReplayStopRequest,
    _user=Depends(require_role("admin", "supervisor")),
    svc: ReplayService = Depends(get_replay_service),
):
    svc.stop(payload.machine_id)
    return {"machine_id": payload.machine_id, "status": "stopped"}


@router.get("/replay/status")
def replay_status(svc: ReplayService = Depends(get_replay_service)):
    return svc.status()
