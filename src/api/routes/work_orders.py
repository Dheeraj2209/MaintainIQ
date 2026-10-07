"""Work-order endpoints (design/2026-10-07-work-orders-escalation-design.md, "API").

Every route needs a session (the router is mounted behind get_current_user).
Role and assignee checks live in src/work_orders/service.py; the routes only
add require_role where a whole route is admin/supervisor-only, so a 403 is
returned before the body is even looked at.

Mutations broadcast after the service has committed: work_order_created or
work_order_updated with a `change`. A failed broadcast is logged and never
fails the request. None of these events is republished to devices
(protocol.ALERT_EVENT_TYPES does not list them).

Mutating routes are async only to await the broadcast: the service call
(SQLite writes that can wait out the busy timeout) runs on a worker thread via
asyncio.to_thread, never on the event loop.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import get_db
from src.api.schemas import (
    Assignee,
    WorkOrder,
    WorkOrderAssign,
    WorkOrderCancel,
    WorkOrderComplete,
    WorkOrderCreate,
    WorkOrderDetail,
    WorkOrderUpdate,
)
from src.auth.deps import get_current_user, require_role
from src.realtime.manager import manager
from src.work_orders import service
from src.work_orders.service import (
    WorkOrderConflict,
    WorkOrderError,
    WorkOrderForbidden,
    WorkOrderNotFound,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

_supervising = require_role(*service.SUPERVISING_ROLES)


def http_error(exc: WorkOrderError) -> HTTPException:
    """Map a service exception to its status code (one place for all routes,
    including POST /api/alerts/{id}/work-order)."""
    if isinstance(exc, WorkOrderNotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, WorkOrderConflict):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, WorkOrderForbidden):
        code = status.HTTP_403_FORBIDDEN
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


async def broadcast_quietly(event: dict) -> None:
    try:
        await manager.broadcast(event)
    except Exception:
        logger.exception("Failed to broadcast %s", event.get("type"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def broadcast_order(order: dict, change: Optional[str] = None) -> None:
    event = {
        "type": "work_order_created" if change is None else "work_order_updated",
        "machine_id": order["machine_id"],
        "work_order": WorkOrder(**order).model_dump(),
        "at": _now(),
    }
    if change is not None:
        event["change"] = change
    await broadcast_quietly(event)


@router.get("", response_model=list[WorkOrder])
def list_work_orders(
    status_: Optional[str] = Query(None, alias="status",
                                   description="open | assigned | in_progress | done | cancelled | active"),
    machine_id: Optional[str] = Query(None),
    assigned_to: Optional[int] = Query(None),
    alert_id: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    try:
        rows = service.list_work_orders(db, status=status_, machine_id=machine_id,
                                        assigned_to=assigned_to, alert_id=alert_id, limit=limit)
    except WorkOrderError as exc:
        raise http_error(exc)
    return [WorkOrder(**row) for row in rows]


# Declared before /{wo_id} so "assignees" is never parsed as an id.
@router.get("/assignees", response_model=list[Assignee])
def list_assignees(db=Depends(get_db), _user: dict = Depends(_supervising)):
    return [Assignee(**row) for row in service.list_assignees(db)]


@router.get("/{wo_id}", response_model=WorkOrderDetail)
def get_work_order(wo_id: int, db=Depends(get_db)):
    try:
        return WorkOrderDetail(**service.get_work_order(db, wo_id, with_events=True))
    except WorkOrderError as exc:
        raise http_error(exc)


@router.post("", response_model=WorkOrder, status_code=status.HTTP_201_CREATED)
async def create_work_order(payload: WorkOrderCreate, db=Depends(get_db),
                            user: dict = Depends(_supervising)):
    try:
        order = await asyncio.to_thread(service.create, db, user, **payload.model_dump())
    except WorkOrderError as exc:
        raise http_error(exc)
    await broadcast_order(order)
    return WorkOrder(**order)


@router.patch("/{wo_id}", response_model=WorkOrder)
async def update_work_order(wo_id: int, payload: WorkOrderUpdate, db=Depends(get_db),
                            user: dict = Depends(_supervising)):
    try:
        order, changed = await asyncio.to_thread(
            service.edit, db, wo_id, user, **payload.model_dump(exclude_unset=True))
    except WorkOrderError as exc:
        raise http_error(exc)
    if changed:
        await broadcast_order(order, "edited")
    return WorkOrder(**order)


@router.post("/{wo_id}/assign", response_model=WorkOrder)
async def assign_work_order(wo_id: int, payload: WorkOrderAssign, db=Depends(get_db),
                            user: dict = Depends(_supervising)):
    try:
        order = await asyncio.to_thread(service.assign, db, wo_id, user, payload.assigned_to,
                                        note=payload.note)
    except WorkOrderError as exc:
        raise http_error(exc)
    await broadcast_order(order, "assigned")
    return WorkOrder(**order)


@router.post("/{wo_id}/start", response_model=WorkOrder)
async def start_work_order(wo_id: int, db=Depends(get_db), user: dict = Depends(get_current_user)):
    try:
        order = await asyncio.to_thread(service.start, db, wo_id, user)
    except WorkOrderError as exc:
        raise http_error(exc)
    await broadcast_order(order, "started")
    return WorkOrder(**order)


@router.post("/{wo_id}/complete", response_model=WorkOrder)
async def complete_work_order(wo_id: int, payload: Optional[WorkOrderComplete] = None,
                              db=Depends(get_db), user: dict = Depends(get_current_user)):
    payload = payload or WorkOrderComplete()
    try:
        order = await asyncio.to_thread(service.complete, db, wo_id, user, notes=payload.notes,
                                        performed_at=payload.performed_at,
                                        maintenance_type=payload.maintenance_type)
    except WorkOrderError as exc:
        raise http_error(exc)
    await broadcast_order(order, "completed")
    return WorkOrder(**order)


@router.post("/{wo_id}/cancel", response_model=WorkOrder)
async def cancel_work_order(wo_id: int, payload: Optional[WorkOrderCancel] = None,
                            db=Depends(get_db), user: dict = Depends(_supervising)):
    payload = payload or WorkOrderCancel()
    try:
        order = await asyncio.to_thread(service.cancel, db, wo_id, user, reason=payload.reason)
    except WorkOrderError as exc:
        raise http_error(exc)
    await broadcast_order(order, "cancelled")
    return WorkOrder(**order)
