"""Machine endpoints: fleet summary, per-machine detail, metric trends."""
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_db
from src.api.schemas import Alert, MachineDetail, MachineSummary, MaintenanceRecord, TrendPoint
from src.kpi import calculations as kpi
from src.maintenance import records as maintenance

router = APIRouter(prefix="/machines", tags=["machines"])

# Metrics exposable as trends map to real, queryable columns in `readings`.
_TREND_COLUMNS = {
    "vibration_h_rms": "vibration_h_rms",
    "vibration_h_kurtosis": "vibration_h_kurtosis",
    "vibration_h_high_band_energy_ratio": "vibration_h_high_band_energy_ratio",
    "temperature_c": "temperature_c",
}


def _machine_exists(conn, machine_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM machines WHERE machine_id = ?", (machine_id,)
    ).fetchone() is not None


@router.get("", response_model=list[MachineSummary])
def list_machines(db=Depends(get_db)):
    return kpi.machine_health_kpis(db)


@router.get("/{machine_id}", response_model=MachineDetail)
def get_machine(machine_id: str, db=Depends(get_db)):
    if not _machine_exists(db, machine_id):
        raise HTTPException(status_code=404, detail=f"unknown machine: {machine_id}")

    health = kpi.machine_health_kpis(db, machine_id)[0]
    maint = kpi.maintenance_kpis(db, machine_id)[0]

    alert_rows = db.execute(
        """SELECT id, machine_id, opened_at, resolved_at, severity, health_state,
                  probable_cause, message, status, source,
                  acknowledged_at, acknowledged_by
           FROM alerts WHERE machine_id = ? ORDER BY opened_at DESC""",
        (machine_id,),
    ).fetchall()
    alerts = [Alert(**dict(row)) for row in alert_rows]

    history = [MaintenanceRecord(**rec) for rec in maintenance.get_history(db, machine_id)]

    return MachineDetail(
        health=MachineSummary(**health),
        maintenance=maint,
        alerts=alerts,
        maintenance_history=history,
    )


@router.get("/{machine_id}/trends", response_model=list[TrendPoint])
def get_trends(
    machine_id: str,
    metric: str = Query("vibration_h_rms"),
    limit: int = Query(500, ge=1, le=5000),
    db=Depends(get_db),
):
    if not _machine_exists(db, machine_id):
        raise HTTPException(status_code=404, detail=f"unknown machine: {machine_id}")
    column = _TREND_COLUMNS.get(metric)
    if column is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown metric: {metric}; valid: {sorted(_TREND_COLUMNS)}",
        )

    # Newest `limit` rows, returned oldest-first so charts read left-to-right.
    rows = db.execute(
        f"""SELECT timestamp, {column} AS value FROM readings
            WHERE machine_id = ? ORDER BY timestamp DESC LIMIT ?""",
        (machine_id, limit),
    ).fetchall()
    return [TrendPoint(timestamp=r["timestamp"], value=r["value"]) for r in reversed(rows)]
