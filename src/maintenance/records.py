"""Maintenance history (M5).

Reads/writes the `maintenance_records` table defined (but until now unused)
in src/storage/db.py. This is the first module that populates it: the M1-M3
pipeline never produced maintenance events because the IMS dataset has none,
so records are created only via the dashboard/API POST path here.

Shared by the KPI module (src/kpi/calculations.py) and the API
(src/api/routes/maintenance.py) so that "days since last maintenance" and
"maintenance history" have a single definition.
"""
from datetime import datetime, timezone


class MaintenanceError(ValueError):
    """Raised for invalid maintenance input (unknown machine, bad date)."""


def _machine_exists(conn, machine_id: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM machines WHERE machine_id = ?", (machine_id,)
    )
    return cur.fetchone() is not None


def _parse_timestamp(value: str) -> str:
    """Validate an ISO-8601 timestamp string and return it normalized.

    Accepts a trailing 'Z' (the browser's toISOString output) which
    datetime.fromisoformat rejects on older Pythons; normalize it to +00:00.
    """
    if not value:
        raise MaintenanceError("performed_at is required")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MaintenanceError(f"performed_at is not a valid ISO-8601 timestamp: {value!r}") from exc
    return parsed.isoformat()


def _alert_exists_for_machine(conn, alert_id: int, machine_id: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM alerts WHERE id = ? AND machine_id = ?", (alert_id, machine_id)
    )
    return cur.fetchone() is not None


# Service-side caps, enforced on every write path. The API schema
# (schemas.MaintenanceCreate) is stricter on description (2000); this leaves
# room for the description a completed work order writes ("Work order #n:
# <title up to 200> — <notes up to 2000>") and for the technician it names
# (the assignee's user name, which has no length limit of its own). The point
# is to refuse multi-megabyte input, not to second-guess those writers.
MAX_DESCRIPTION_LENGTH = 4000
MAX_TECHNICIAN_LENGTH = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _has_resets_column(conn) -> bool:
    return any(row[1] == "resets_health"
               for row in conn.execute("PRAGMA table_info(maintenance_records)"))


def _date_guard(conn, machine_id: str, performed_at: str, alert_id, now: str, *,
                explicit: bool) -> bool:
    """Whether a record dated performed_at may reset machine_id's health
    (plan D2). A record more than FUTURE_SKEW ahead of `now` is refused. One
    dated before the current epoch began or before its linked alert opened is
    history, not today's repair: refused if the reset was asked for
    explicitly, otherwise it simply does not reset."""
    from src.feedback.service import FUTURE_SKEW
    from src.prediction import health_epoch

    performed = _as_utc(performed_at)
    if performed > _as_utc(now) + FUTURE_SKEW:
        raise MaintenanceError("performed_at is in the future")
    row = health_epoch.current(conn, machine_id)
    if row is None:  # no health table: nothing to reset
        return False
    floors = [row.epoch_started_at]
    if alert_id is not None:
        floors.append(conn.execute("SELECT opened_at FROM alerts WHERE id = ?",
                                   (alert_id,)).fetchone()[0])
    if any(floor and performed < _as_utc(floor) for floor in floors):
        if explicit:
            raise MaintenanceError(
                "cannot reset health tracking with a record dated before the current fault")
        return False
    return True


def _log_maintenance_locked(conn, machine_id: str, performed_at: str, description=None,
                            technician=None, *, alert_id=None, type=None, reset_health=None,
                            now: str, default_reset: bool = False):
    """Validate and insert one maintenance record and, if it resets, reset the
    machine's health epoch (plan D1, D2). reset_health None resolves to
    default_reset; either way the date guard applies to a reset. The caller
    holds live._TRANSITION_LOCK and commits (or rolls back). Returns
    (record, alerts the reset resolved)."""
    if description is not None and len(description) > MAX_DESCRIPTION_LENGTH:
        raise MaintenanceError(f"description is longer than {MAX_DESCRIPTION_LENGTH} characters")
    if technician is not None and len(technician) > MAX_TECHNICIAN_LENGTH:
        raise MaintenanceError(f"technician is longer than {MAX_TECHNICIAN_LENGTH} characters")
    if not _machine_exists(conn, machine_id):
        raise MaintenanceError(f"unknown machine_id: {machine_id!r}")
    if alert_id is not None and not _alert_exists_for_machine(conn, alert_id, machine_id):
        raise MaintenanceError(f"alert_id {alert_id} does not belong to machine_id {machine_id!r}")
    performed_at = _parse_timestamp(performed_at)
    created_at = now

    if _has_resets_column(conn):
        resets = default_reset if reset_health is None else bool(reset_health)
        if resets:
            resets = _date_guard(conn, machine_id, performed_at, alert_id, now,
                                 explicit=reset_health is not None)
        cur = conn.execute(
            """INSERT INTO maintenance_records
               (machine_id, performed_at, description, technician, created_at, alert_id, type,
                resets_health)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (machine_id, performed_at, description, technician, created_at, alert_id, type,
             int(resets)),
        )
    else:  # an old DB opened outside get_db: no health tracking to reset
        resets = False
        cur = conn.execute(
            """INSERT INTO maintenance_records
               (machine_id, performed_at, description, technician, created_at, alert_id, type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (machine_id, performed_at, description, technician, created_at, alert_id, type),
        )
    resolved = []
    if resets:
        from src.prediction import health_epoch

        _, resolved = health_epoch._reset_locked(
            conn, machine_id, reason=f"maintenance:{cur.lastrowid}", now=now)
    record = {
        "id": cur.lastrowid,
        "machine_id": machine_id,
        "performed_at": performed_at,
        "description": description,
        "technician": technician,
        "created_at": created_at,
        "alert_id": alert_id,
        "type": type,
        "resets_health": bool(resets),
    }
    return record, resolved


def log_maintenance(conn, machine_id: str, performed_at: str,
                    description: str = None, technician: str = None,
                    alert_id: int = None, type: str = None, *,
                    reset_health: bool = None, now: str = None) -> dict:
    """Insert one maintenance record and return it as a dict.

    Validates that the machine exists and performed_at parses before writing,
    so a bad request never leaves a partial row. If alert_id is given, it must
    reference an alert that belongs to this machine — otherwise a stale or
    cross-machine form submission could mislink a record.

    A free-standing record resets the machine's health epoch only with an
    explicit reset_health=True (plan D2); the record and the reset are one
    transaction under live._TRANSITION_LOCK, and the alerts the reset
    resolved are announced after the commit. `now` (wall clock by default)
    stamps created_at and the resolve, never performed_at.
    """
    from src.alerts import live
    from src.prediction import health_epoch

    now = now or _now()
    with live._TRANSITION_LOCK:
        try:
            record, resolved = _log_maintenance_locked(
                conn, machine_id, performed_at, description, technician,
                alert_id=alert_id, type=type, reset_health=reset_health, now=now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    health_epoch.announce_resolved(conn, resolved, at=now)
    return record


def get_history(conn, machine_id: str, limit: int = None, offset: int = 0) -> list:
    """Maintenance records for a machine, most recent first.

    `limit=None` returns everything (today's behavior, still used by
    src/kpi/calculations.py); a numeric limit paginates for the
    machine-detail "Load more" UI.
    """
    query = """SELECT id, machine_id, performed_at, description, technician,
                      created_at, alert_id, type, resets_health
               FROM maintenance_records
               WHERE machine_id = ?
               ORDER BY performed_at DESC, id DESC"""
    params = [machine_id]
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    cur = conn.execute(query, params)
    return [dict(row) for row in cur.fetchall()]


def last_maintenance_at(conn, machine_id: str):
    """ISO timestamp of the most recent maintenance, or None if never serviced."""
    cur = conn.execute(
        "SELECT MAX(performed_at) AS last FROM maintenance_records WHERE machine_id = ?",
        (machine_id,),
    )
    row = cur.fetchone()
    return row["last"] if row else None


def days_since_last_maintenance(conn, machine_id: str, now: datetime = None):
    """Whole days since the last maintenance, or None if never serviced.

    `now` is injectable for testing; defaults to the current UTC time.
    """
    last = last_maintenance_at(conn, machine_id)
    if last is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(last.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).days
