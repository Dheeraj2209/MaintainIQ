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


def log_maintenance(conn, machine_id: str, performed_at: str,
                    description: str = None, technician: str = None,
                    alert_id: int = None, type: str = None) -> dict:
    """Insert one maintenance record and return it as a dict.

    Validates that the machine exists and performed_at parses before writing,
    so a bad request never leaves a partial row. If alert_id is given, it must
    reference an alert that belongs to this machine — otherwise a stale or
    cross-machine form submission could mislink a record.
    """
    if not _machine_exists(conn, machine_id):
        raise MaintenanceError(f"unknown machine_id: {machine_id!r}")
    if alert_id is not None and not _alert_exists_for_machine(conn, alert_id, machine_id):
        raise MaintenanceError(f"alert_id {alert_id} does not belong to machine_id {machine_id!r}")
    performed_at = _parse_timestamp(performed_at)
    created_at = datetime.now(timezone.utc).isoformat()

    cur = conn.execute(
        """INSERT INTO maintenance_records
           (machine_id, performed_at, description, technician, created_at, alert_id, type)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (machine_id, performed_at, description, technician, created_at, alert_id, type),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "machine_id": machine_id,
        "performed_at": performed_at,
        "description": description,
        "technician": technician,
        "created_at": created_at,
        "alert_id": alert_id,
        "type": type,
    }


def get_history(conn, machine_id: str, limit: int = None, offset: int = 0) -> list:
    """Maintenance records for a machine, most recent first.

    `limit=None` returns everything (today's behavior, still used by
    src/kpi/calculations.py); a numeric limit paginates for the
    machine-detail "Load more" UI.
    """
    query = """SELECT id, machine_id, performed_at, description, technician,
                      created_at, alert_id, type
               FROM maintenance_records
               WHERE machine_id = ?
               ORDER BY performed_at DESC"""
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
