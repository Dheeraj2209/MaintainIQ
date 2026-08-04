"""Notification log endpoints: read-only view of emails the system has sent.

Admin + supervisor only — operators aren't paged (see
src/notifications/dispatch.py), so the log isn't relevant to their role.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_db
from src.api.schemas import NotificationOut
from src.auth.deps import require_role

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_role("admin", "supervisor"))],
)


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    status: Optional[str] = Query(None, description="sent | failed"),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    rows = db.execute(
        f"""SELECT id, alert_id, recipient_email, recipient_role, subject, body, status, created_at
            FROM notifications {where}
            ORDER BY created_at DESC
            LIMIT ?""",
        params,
    ).fetchall()
    return [NotificationOut(**dict(row)) for row in rows]
