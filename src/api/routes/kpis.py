"""KPI endpoints: fleet-wide summary + per-machine drill-down."""
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_db
from src.api.schemas import KpiSummary
from src.kpi import calculations as kpi

router = APIRouter(prefix="/kpis", tags=["kpis"])


def _machine_exists(conn, machine_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM machines WHERE machine_id = ?", (machine_id,)
    ).fetchone() is not None


@router.get("", response_model=KpiSummary)
def get_kpi_summary(db=Depends(get_db)):
    return kpi.summary(db)


@router.get("/detail")
def get_kpi_detail(db=Depends(get_db)):
    """Full KPI breakdown across all SRS categories, including the
    not_applicable markers, for a KPI-explorer view."""
    return {
        "machine_health": kpi.machine_health_kpis(db),
        "maintenance": kpi.maintenance_kpis(db),
        "prediction": kpi.prediction_kpis(),
        "operational": kpi.operational_kpis(db),
        "system": kpi.system_kpis(),
    }


@router.get("/{machine_id}")
def get_machine_kpis(machine_id: str, db=Depends(get_db)):
    if not _machine_exists(db, machine_id):
        raise HTTPException(status_code=404, detail=f"unknown machine: {machine_id}")
    return {
        "machine_health": kpi.machine_health_kpis(db, machine_id)[0],
        "maintenance": kpi.maintenance_kpis(db, machine_id)[0],
    }
