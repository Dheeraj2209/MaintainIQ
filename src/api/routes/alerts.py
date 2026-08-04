"""Alert endpoints: filterable list across the fleet."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_db
from src.api.schemas import Alert
from src.auth.deps import require_role
from src.realtime.manager import manager

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[Alert])
def list_alerts(
    status: Optional[str] = Query(None, description="open | resolved"),
    machine_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if machine_id:
        clauses.append("machine_id = ?")
        params.append(machine_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    rows = db.execute(
        f"""SELECT id, machine_id, opened_at, resolved_at, severity, health_state,
                   probable_cause, message, status, source
            FROM alerts {where}
            ORDER BY (status = 'open') DESC, opened_at DESC
            LIMIT ?""",
        params,
    ).fetchall()
    return [Alert(**dict(row)) for row in rows]


@router.post("/{alert_id}/acknowledge", response_model=Alert)
async def acknowledge_alert(
    alert_id: int,
    db=Depends(get_db),
    user: dict = Depends(require_role("admin", "supervisor", "operator")),
):
    row = db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown alert: {alert_id}")

    alert = dict(row)
    if alert["acknowledged_at"] is not None:
        return Alert(**alert)  # already acknowledged: idempotent no-op, no re-broadcast

    acknowledged_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
        (acknowledged_at, user["id"], alert_id),
    )
    db.commit()

    alert["acknowledged_at"] = acknowledged_at
    alert["acknowledged_by"] = user["id"]

    await manager.broadcast({
        "type": "alert_acknowledged",
        "machine_id": alert["machine_id"],
        "alert": alert,
        "at": acknowledged_at,
    })

    return Alert(**alert)
