"""Maintenance endpoints: history read + log-a-record write (M5)."""
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_db
from src.api.schemas import MaintenanceCreate, MaintenanceRecord
from src.auth.deps import get_current_user
from src.maintenance import records as maintenance

# Who may restart a machine's health tracking from a free-standing record
# (plan D2); operators reset by completing their work order instead.
RESET_ROLES = ("admin", "supervisor")

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.get("/{machine_id}", response_model=list[MaintenanceRecord])
def get_maintenance_history(
    machine_id: str,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    return maintenance.get_history(db, machine_id, limit=limit, offset=offset)


@router.post("", response_model=MaintenanceRecord, status_code=201)
def log_maintenance(payload: MaintenanceCreate, db=Depends(get_db),
                    user: dict = Depends(get_current_user)):
    if payload.reset_health and user["role"] not in RESET_ROLES:
        raise HTTPException(status_code=403,
                            detail="only an admin or supervisor may reset health tracking")
    try:
        return maintenance.log_maintenance(
            db,
            machine_id=payload.machine_id,
            performed_at=payload.performed_at,
            description=payload.description,
            technician=payload.technician,
            alert_id=payload.alert_id,
            type=payload.type,
            reset_health=payload.reset_health,
        )
    except maintenance.MaintenanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
