"""Admin-only demo trigger: synthesizes one new reading for a machine at a
requested severity and drives it through the same live prediction -> alert
-> notify -> broadcast pipeline a real future M6 telemetry feed would use.
This is what makes "realtime email" and "realtime dashboard" demoable
without hardware.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from src.alerts.live import apply_reading
from src.api.deps import get_db
from src.api.schemas import Alert, SimulateFaultRequest, SimulateFaultResponse
from src.auth.deps import require_role
from src.notifications.dispatch import notify_alert
from src.prediction.live import UnknownMachineError, evaluate_new_reading
from src.realtime.manager import manager

router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(require_role("admin"))])


def _machine_exists(conn, machine_id: str) -> bool:
    return conn.execute("SELECT 1 FROM machines WHERE machine_id = ?", (machine_id,)).fetchone() is not None


@router.post("/simulate-fault", response_model=SimulateFaultResponse)
async def simulate_fault(payload: SimulateFaultRequest, db=Depends(get_db)):
    if not _machine_exists(db, payload.machine_id):
        raise HTTPException(status_code=404, detail=f"unknown machine: {payload.machine_id}")

    try:
        prediction = evaluate_new_reading(db, payload.machine_id, payload.severity)
    except UnknownMachineError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    result = apply_reading(
        db,
        machine_id=payload.machine_id,
        health_state=prediction["health_state"],
        probable_cause=prediction["probable_cause"],
        source="demo",
        timestamp=prediction["timestamp"],
    )

    alert_out = None
    emails_sent = 0

    if result is not None:
        event_type, alert = result
        alert_out = Alert(**alert)
        await manager.broadcast({
            "type": event_type,
            "machine_id": payload.machine_id,
            "alert": alert,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        # Resolutions don't get an email: the alert dict's severity/health_state
        # still reflect its last abnormal state, so composing a notification off
        # of it here would read as "CRITICAL" for an issue that just closed.
        if event_type in ("alert_created", "alert_escalated"):
            emails_sent = notify_alert(db, alert)

    return SimulateFaultResponse(
        machine_id=payload.machine_id,
        health_state=prediction["health_state"],
        probable_cause=prediction["probable_cause"],
        alert=alert_out,
        emails_sent=emails_sent,
    )
