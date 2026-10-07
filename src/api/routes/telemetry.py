"""Live MQTT telemetry visibility (design/M6_LIVE_TELEMETRY.md §8) and sensor-node
health (design/2026-10-06-device-health-design.md).

Any authenticated role (mounted under get_current_user in src/api/app.py).
/status reports the in-process ingest service — its connection and counters
live in memory, not the DB, because they describe this process's MQTT
session — plus whether the silence watchdog is running. /devices reads
device_status, which outlives restarts, and classifies every node with the
shared state rule (src/telemetry/device_health.py). /incidents lists the
device_incidents the watchdog opened, and any role may acknowledge one.

The service singleton is created and started by the app lifespan only when
MQTT_BROKER_HOST is set; get_mqtt_service returns None otherwise, and tests
override it via app.dependency_overrides.

Migrations are not run at app start, so every reader here guards against a
DB that predates the table it reads (pre-M6: no device_status; pre-v3: no
device_incidents) and answers as if nothing had happened yet.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_db
from src.api.schemas import DeviceDetail, DeviceHealth, DeviceIncident
from src.auth.deps import require_role
from src.background.scheduler import get_scheduler
from src.telemetry import device_health, protocol
from src.telemetry.config import TelemetrySettings
from src.telemetry.mqtt_service import MqttIngestService, disabled_status
from src.telemetry.watchdog import (
    INCIDENT_SELECT,
    WatchdogSettings,
    get_watcher,
    incident_dict,
    table_exists,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

_service: MqttIngestService | None = None

# How many past incidents GET /devices/{id} returns.
RECENT_INCIDENTS = 10
_INCIDENT_STATUSES = ("open", "resolved")


def set_mqtt_service(service: MqttIngestService | None) -> None:
    """Called by the app lifespan when the service starts / stops."""
    global _service
    _service = service


def get_mqtt_service() -> MqttIngestService | None:
    """FastAPI dependency. None ⇒ MQTT ingest disabled."""
    return _service


def _watchdog_status() -> dict:
    """Whether silence alerting is actually running, so the UI can say so
    honestly. Built here rather than in MqttIngestService: the watchdog is a
    scheduler job, not part of the MQTT session."""
    scheduler = get_scheduler()
    watcher = get_watcher()
    if scheduler is None or watcher is None:
        return {"enabled": False, "interval_s": None,
                "grace_s": WatchdogSettings.from_env_or_default().grace_s,
                "armed": False, "last_tick_at": None}
    status = watcher.status()
    return {"enabled": True, "interval_s": scheduler.interval_s, "grace_s": status["grace_s"],
            "armed": status["armed"], "last_tick_at": status["last_tick_at"]}


@router.get("/status")
def telemetry_status(svc: MqttIngestService | None = Depends(get_mqtt_service)):
    if svc is None:
        # Report the configured prefix even while disabled, so the UI's
        # "how to enable" hint matches what the simulator must publish to.
        # A malformed MQTT_* value already got logged by the lifespan; fall
        # back to the defaults rather than failing a read-only status call.
        try:
            settings = TelemetrySettings.from_env()
        except ValueError:
            settings = None
        body = disabled_status(settings)
    else:
        body = svc.status()
    return {**body, "device_watchdog": _watchdog_status()}


_DEVICE_COLUMNS = (
    "device_id", "machine_id", "online", "last_seen_at", "firmware", "uptime_s",
    "buffer_depth", "buffer_capacity", "buffer_dropped_total",
    "publish_attempts_total", "publish_failures_total", "wifi_rssi_dbm",
    "snapshot_interval_s", "heartbeat_interval_s",
)


def _device_rows(db, *, machine_id: Optional[str] = None, device_id: Optional[str] = None):
    """device_status rows plus `reported` and the open incident id (if the
    incident table exists yet). None ⇒ pre-M6 DB without device_status."""
    if not table_exists(db, "device_status"):
        return None
    columns = ", ".join(f"d.{name}" for name in _DEVICE_COLUMNS)
    if table_exists(db, "device_incidents"):
        incident_col = "i.id AS open_incident_id"
        join = "LEFT JOIN device_incidents i ON i.device_id = d.device_id AND i.status = 'open'"
    else:
        incident_col = "NULL AS open_incident_id"
        join = ""
    clauses, params = [], []
    if machine_id is not None:
        clauses.append("d.machine_id = ?")
        params.append(machine_id)
    if device_id is not None:
        clauses.append("d.device_id = ?")
        params.append(device_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return db.execute(
        f"""SELECT {columns}, d.payload_json IS NOT NULL AS reported, {incident_col}
            FROM device_status d {join} {where}
            ORDER BY d.device_id""",
        params,
    ).fetchall()


def _device_health(row, now: datetime) -> dict:
    device = {name: row[name] for name in _DEVICE_COLUMNS}
    # The stored flag is what the device last said; the API reports the
    # shared rule, which also catches a device that died without an LWT.
    state = device_health.device_state(
        row["online"], row["last_seen_at"], row["heartbeat_interval_s"],
        reported=bool(row["reported"]), now=now,
    )
    silent = device_health.silent_for_s(row["last_seen_at"], now=now)
    device.update(
        online=device_health.is_heard(state),
        state=state,
        silent_for_s=round(silent, 1) if silent is not None else None,
        expected_heartbeat_s=device_health.effective_heartbeat_s(row["heartbeat_interval_s"]),
        open_incident_id=row["open_incident_id"],
    )
    return device


@router.get("/devices", response_model=list[DeviceHealth])
def telemetry_devices(machine_id: Optional[str] = Query(None), db=Depends(get_db)):
    now = datetime.now(timezone.utc)
    # Pre-M6 DB not yet migrated by live ingest (see ingest._connect): no
    # device has ever reported, which is an empty list, not a 500.
    rows = _device_rows(db, machine_id=machine_id)
    if rows is None:
        return []
    return [_device_health(row, now) for row in rows]


@router.get("/devices/{device_id}", response_model=DeviceDetail)
def telemetry_device(device_id: str, db=Depends(get_db)):
    now = datetime.now(timezone.utc)
    rows = _device_rows(db, device_id=device_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown device: {device_id}")
    device = _device_health(rows[0], now)
    recent = []
    if table_exists(db, "device_incidents"):
        recent = [incident_dict(r) for r in db.execute(
            f"""SELECT {INCIDENT_SELECT} FROM device_incidents WHERE device_id = ?
                ORDER BY opened_at DESC, id DESC LIMIT ?""",
            (device_id, RECENT_INCIDENTS),
        ).fetchall()]
    return {**device, "recent_incidents": recent}


@router.get("/incidents", response_model=list[DeviceIncident])
def list_incidents(
    status: Optional[str] = Query(None, description="open | resolved (omit for all)"),
    device_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    if status is not None and status not in _INCIDENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")
    if not table_exists(db, "device_incidents"):
        return []
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if device_id:
        clauses.append("device_id = ?")
        params.append(device_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    # The watchdog writes every opened_at through protocol.format_utc, so
    # they sort as strings (same ordering as the alerts list).
    rows = db.execute(
        f"""SELECT {INCIDENT_SELECT} FROM device_incidents {where}
            ORDER BY (status = 'open') DESC, opened_at DESC, id DESC
            LIMIT ?""",
        params,
    ).fetchall()
    return [incident_dict(r) for r in rows]


@router.post("/incidents/{incident_id}/acknowledge", response_model=DeviceIncident)
def acknowledge_incident(
    incident_id: int,
    db=Depends(get_db),
    user: dict = Depends(require_role("admin", "supervisor", "operator")),
):
    """Idempotent: an already-acknowledged incident is returned unchanged.
    Resolved incidents can be acknowledged too. The conditional UPDATE is
    race-free without a lock: the watchdog never writes acknowledged_*. No
    realtime broadcast — the Devices page polls (design decision 10)."""
    if not table_exists(db, "device_incidents"):
        raise HTTPException(status_code=404, detail=f"unknown incident: {incident_id}")
    db.execute(
        "UPDATE device_incidents SET acknowledged_at = ?, acknowledged_by = ? "
        "WHERE id = ? AND acknowledged_at IS NULL",
        (protocol.format_utc(datetime.now(timezone.utc)), user["id"], incident_id),
    )
    db.commit()
    row = db.execute(
        f"SELECT {INCIDENT_SELECT} FROM device_incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown incident: {incident_id}")
    return incident_dict(row)
