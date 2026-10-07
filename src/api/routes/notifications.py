"""Notification log endpoints: read-only view of the emails and pushes the
system has sent.

Admin + supervisor only. Operators are paged by push since the mobile
operator view (src/notifications/dispatch.py), but they check their own
devices with the test push on /m/settings rather than through this log.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

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
    channel: Optional[str] = Query(None, description="email | push"),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    if channel is not None and channel not in ("email", "push"):
        raise HTTPException(400, "channel must be 'email' or 'push'")
    columns = {row[1] for row in db.execute("PRAGMA table_info(notifications)")}
    # notifications.channel arrives with migration 7; on an older DB every
    # row is an email.
    has_channel_col = "channel" in columns
    channel_expr = "channel" if has_channel_col else "'email'"

    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if channel:
        clauses.append(f"{channel_expr} = ?")
        params.append(channel)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    # notifications.device_incident_id arrives with migration 3, which the
    # app does not run at startup; an older DB just reports NULL.
    has_incident_col = "device_incident_id" in columns
    incident_col = "device_incident_id" if has_incident_col else "NULL AS device_incident_id"
    rows = db.execute(
        f"""SELECT id, alert_id, {incident_col}, recipient_email, recipient_role, subject, body,
                   status, {channel_expr} AS channel, created_at
            FROM notifications {where}
            ORDER BY created_at DESC
            LIMIT ?""",
        params,
    ).fetchall()
    return [NotificationOut(**dict(row)) for row in rows]
