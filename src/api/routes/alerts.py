"""Alert endpoints: filterable list across the fleet."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_db
from src.api.schemas import Alert

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
