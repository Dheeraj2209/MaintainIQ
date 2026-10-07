"""Alert endpoints: filterable list across the fleet, acknowledgement,
raising a work order from an alert, closing an alert / recording what
actually happened (design/2026-10-07-prediction-feedback-design.md, "API"),
and "Why this alert?" (design/2026-10-07-alert-explanation-design.md, "API").

Feedback events (alert_closed, alert_feedback_recorded) are broadcast from
here after the service has committed, never paged by email, and never
published to devices as such — protocol.device_alert_event only turns
alert_closed into the alert_resolved the device LED understands.

The mutating routes are async only so they can await the broadcast; the
service call itself (SQLite writes that may wait up to the busy timeout, and
live._TRANSITION_LOCK) always runs on a worker thread via asyncio.to_thread,
never on the event loop — otherwise one slow writer elsewhere would freeze
every websocket and request.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from src.alerts import live
from src.api.deps import get_db
from src.api.routes.work_orders import broadcast_order, broadcast_quietly, http_error
from src.api.schemas import (
    Alert,
    AlertCloseRequest,
    AlertCloseResponse,
    AlertExplanation,
    AlertFeedback,
    AlertFeedbackIn,
    WorkOrder,
    WorkOrderFromAlert,
)
from src.auth.deps import require_role
from src.feedback import service as feedback_service
from src.feedback.service import FeedbackError, FeedbackForbidden, FeedbackNotFound
from src.realtime.manager import manager
from src.root_cause import explain
from src.work_orders import service as work_orders
from src.work_orders.service import WorkOrderError

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
        f"""SELECT {live.API_ALERT_COLUMNS}
            FROM alerts {where}
            ORDER BY (status = 'open') DESC, opened_at DESC
            LIMIT ?""",
        params,
    ).fetchall()
    return [Alert(**live.api_alert(row)) for row in rows]


def _ack_event(alert: dict) -> dict:
    return {
        "type": "alert_acknowledged",
        "machine_id": alert["machine_id"],
        "alert": alert,
        "at": alert["acknowledged_at"],
    }


@router.post("/{alert_id}/acknowledge", response_model=Alert)
async def acknowledge_alert(
    alert_id: int,
    db=Depends(get_db),
    user: dict = Depends(require_role("admin", "supervisor", "operator")),
):
    # A conditional UPDATE under live._TRANSITION_LOCK: of two racing
    # acknowledgements the first one wins and the second is a no-op.
    alert, changed = await asyncio.to_thread(live.acknowledge_alert, db, alert_id, user["id"])
    if alert is None:
        raise HTTPException(status_code=404, detail=f"unknown alert: {alert_id}")
    if changed:  # already acknowledged: idempotent no-op, no re-broadcast
        await manager.broadcast(_ack_event(alert))
    return Alert(**alert)


@router.post("/{alert_id}/work-order", response_model=WorkOrder, status_code=status.HTTP_201_CREATED)
async def create_work_order_from_alert(
    alert_id: int,
    payload: Optional[WorkOrderFromAlert] = Body(None),
    db=Depends(get_db),
    user: dict = Depends(require_role("admin", "supervisor", "operator")),
):
    """Any signed-in user may raise an order from an alert; only admins and
    supervisors may assign it at the same time. Also acknowledges the alert
    if nobody has yet (work-orders design, decision 4)."""
    payload = payload or WorkOrderFromAlert()
    try:
        order, acknowledged = await asyncio.to_thread(
            work_orders.create_from_alert, db, alert_id, user, **payload.model_dump())
    except WorkOrderError as exc:
        raise http_error(exc)
    if acknowledged is not None:
        await broadcast_quietly(_ack_event(acknowledged))
    await broadcast_order(order)
    return WorkOrder(**order)


# --- Prediction feedback ---------------------------------------------------------------

_any_role = require_role("admin", "supervisor", "operator")


def _feedback_http_error(exc: FeedbackError) -> HTTPException:
    if isinstance(exc, FeedbackNotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, FeedbackForbidden):
        code = status.HTTP_403_FORBIDDEN
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


def _feedback_event(event_type: str, alert: dict, feedback: dict, at: str) -> dict:
    return {
        "type": event_type,
        "machine_id": alert["machine_id"],
        "alert": Alert(**alert).model_dump(),
        "feedback": AlertFeedback(**feedback).model_dump(),
        "at": at,
    }


@router.post("/{alert_id}/close", response_model=AlertCloseResponse)
async def close_alert(
    alert_id: int,
    payload: AlertCloseRequest,
    db=Depends(get_db),
    user: dict = Depends(_any_role),
):
    """Resolve an open alert by hand and record its outcome in one step. On
    an alert that is already resolved (e.g. it auto-resolved a moment ago)
    only the outcome is recorded and `closed` is false — not an error."""
    now = feedback_service._now()
    try:
        alert, feedback, closed = await asyncio.to_thread(
            feedback_service.close_alert, db, alert_id, user, now=now, **payload.model_dump())
    except FeedbackError as exc:
        raise _feedback_http_error(exc)
    event_type = "alert_closed" if closed else "alert_feedback_recorded"
    await broadcast_quietly(_feedback_event(event_type, alert, feedback, now))
    return AlertCloseResponse(alert=Alert(**alert, feedback=feedback), feedback=feedback, closed=closed)


@router.put("/{alert_id}/feedback", response_model=AlertFeedback)
async def put_alert_feedback(
    alert_id: int,
    payload: AlertFeedbackIn,
    db=Depends(get_db),
    user: dict = Depends(_any_role),
):
    """Create or replace the alert's outcome without changing its status.
    Replacing is limited to the recorder, an admin or a supervisor."""
    now = feedback_service._now()
    try:
        alert, feedback, _created = await asyncio.to_thread(
            feedback_service.record_feedback, db, alert_id, user, now=now, **payload.model_dump())
    except FeedbackError as exc:
        raise _feedback_http_error(exc)
    await broadcast_quietly(_feedback_event("alert_feedback_recorded", alert, feedback, now))
    return AlertFeedback(**feedback)


@router.get("/{alert_id}/feedback", response_model=Optional[AlertFeedback])
def get_alert_feedback(alert_id: int, db=Depends(get_db)):
    try:
        feedback = feedback_service.get_feedback(db, alert_id)
    except FeedbackError as exc:
        raise _feedback_http_error(exc)
    return AlertFeedback(**feedback) if feedback is not None else None


# --- Why this alert? ---------------------------------------------------------------------

@router.get("/{alert_id}/explanation", response_model=AlertExplanation)
def get_alert_explanation(
    alert_id: int,
    similar_limit: int = Query(explain.SIMILAR_DEFAULT_LIMIT, ge=0, le=explain.SIMILAR_MAX_LIMIT),
    db=Depends(get_db),
):
    """The evidence behind an alert, for any signed-in user: the latest
    snapshot (or a reconstruction from stored data) plus similar past
    incidents computed now. Read-only; a failing section degrades to null or
    an error status inside a 200, never a 500."""
    result = explain.get_explanation(db, alert_id, similar_limit=similar_limit,
                                     predictor=explain.loaded_predictor())
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown alert: {alert_id}")
    return AlertExplanation(**result)
