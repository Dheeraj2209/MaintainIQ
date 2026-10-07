"""Admin-only demo trigger: synthesizes one new reading for a machine at a
requested severity and drives it through the same live prediction -> alert
-> notify -> broadcast pipeline a real future M6 telemetry feed would use.
This is what makes "realtime email" and "realtime dashboard" demoable
without hardware.

Both routes go through the shared fan-out (src/prediction/pipeline.fan_out,
work-orders design decision 9) rather than paging and broadcasting
themselves, so demo alerts get exactly what model alerts get: the level-0
email and its last_paged_at stamp, the realtime event, and eligibility for
the paging ladder. They are sync so FastAPI runs them in its threadpool; the
broadcast is the manager's thread-safe, fire-and-forget one, as for replay.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from src.alerts.live import apply_reading
from src.api.deps import get_db
from src.api.schemas import Alert, SimulateFaultRequest, SimulateFaultResponse
from src.auth.deps import require_role
from src.prediction import pipeline
from src.prediction.live import UnknownMachineError, evaluate_new_reading

router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(require_role("admin"))])


def _machine_exists(conn, machine_id: str) -> bool:
    return conn.execute("SELECT 1 FROM machines WHERE machine_id = ?", (machine_id,)).fetchone() is not None


@router.post("/simulate-fault", response_model=SimulateFaultResponse)
def simulate_fault(payload: SimulateFaultRequest, db=Depends(get_db)):
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

    # Resolutions don't get an email (pipeline._PAGING_EVENTS): the alert
    # dict's severity/health_state still reflect its last abnormal state, so
    # a notification composed off of it would read as "CRITICAL" for an issue
    # that just closed.
    # The demo reading is the explanation's trigger; there is no prediction
    # id, because insert_predictions returns none.
    outcome = pipeline.fan_out(db, result, at=_now(), reading_id=prediction["reading_id"])
    alert = outcome["alert"]

    return SimulateFaultResponse(
        machine_id=payload.machine_id,
        health_state=prediction["health_state"],
        probable_cause=prediction["probable_cause"],
        alert=Alert(**alert) if alert is not None else None,
        emails_sent=outcome["emails_sent"],
    )


@router.post("/reset-machine/{machine_id}", response_model=SimulateFaultResponse)
def reset_machine(machine_id: str, db=Depends(get_db)):
    """Resolve machine_id's open *demo* alert (if any) by replaying a
    'healthy' reading through the same state machine simulate_fault uses.
    Demo and real readings keep separate episodes (src/alerts/live.py), so a
    real alert on the same machine is never touched. Lets a demo
    re-trigger a fresh email for a machine without waiting for a real
    escalation — apply_reading's escalate-only-mid-episode rule otherwise
    makes repeat same/lower-severity simulations on an already-open alert a
    silent no-op (see src/alerts/live.py)."""
    if not _machine_exists(db, machine_id):
        raise HTTPException(status_code=404, detail=f"unknown machine: {machine_id}")

    try:
        prediction = evaluate_new_reading(db, machine_id, "healthy")
    except UnknownMachineError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    result = apply_reading(
        db,
        machine_id=machine_id,
        health_state=prediction["health_state"],
        probable_cause=prediction["probable_cause"],
        source="demo",
        timestamp=prediction["timestamp"],
    )

    # alert_resolved is not a paging event, so this sends no email.
    outcome = pipeline.fan_out(db, result, at=_now())
    alert = outcome["alert"]

    return SimulateFaultResponse(
        machine_id=machine_id,
        health_state=prediction["health_state"],
        probable_cause=prediction["probable_cause"],
        alert=Alert(**alert) if alert is not None else None,
        emails_sent=outcome["emails_sent"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
