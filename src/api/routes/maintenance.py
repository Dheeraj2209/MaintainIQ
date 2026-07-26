"""Maintenance endpoints: history read + log-a-record write (M5)."""
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_db
from src.api.schemas import MaintenanceCreate, MaintenanceRecord
from src.maintenance import records as maintenance

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.get("/{machine_id}", response_model=list[MaintenanceRecord])
def get_maintenance_history(machine_id: str, db=Depends(get_db)):
    return maintenance.get_history(db, machine_id)


@router.post("", response_model=MaintenanceRecord, status_code=201)
def log_maintenance(payload: MaintenanceCreate, db=Depends(get_db)):
    try:
        return maintenance.log_maintenance(
            db,
            machine_id=payload.machine_id,
            performed_at=payload.performed_at,
            description=payload.description,
            technician=payload.technician,
        )
    except maintenance.MaintenanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
