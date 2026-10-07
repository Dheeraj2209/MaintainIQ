"""Sensor-node silence watchdog (design/2026-10-06-device-health-design.md, decisions 4-9).

Silence is the absence of messages, so ingest can never notice it; this job
runs on the background scheduler (src/background/scheduler.py) and, on each
tick, classifies every node with the shared rule (device_health) and:

* opens a device_incidents row for a node that is `offline` and has no open
  incident (kind 'lwt' when the stored flag says the node announced it is
  offline, else 'silent'), pages admins/supervisors and emits device_offline;
* resolves the open incident of a node that is heard again (online or stale)
  and emits device_online.

`stale` and `never_reported` never open anything (decision 4). The watchdog is
the only writer of status / resolved_at; humans only write the
acknowledged_* columns, so no lock is shared with the ack route.

Gating (decision 6): a tick does nothing at all — no open, no resolve, no
events — unless live MQTT ingest exists, is connected, and has been connected
for at least the grace window. When we are the ones who are deaf, every node
looks silent; and right after a (re)connect the retained-status replay and the
QoS-1 backlog would too.

Side effects never undo the incident: the email happens after the INSERT has
committed and is wrapped in try/except, and the partial unique index (one open
incident per device) makes a racing second app instance lose its INSERT and
send nothing.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from src.notifications import dispatch
from src.telemetry import device_health, protocol
from src.telemetry.ingest import ensure_telemetry_schema

logger = logging.getLogger(__name__)

# A node that re-opens an incident within this many seconds of its previous
# incident opening is recorded and broadcast but not emailed again: a node on
# marginal Wi-Fi must not mail-bomb supervisors (decision 9). Fixed in code to
# keep the configuration small.
REPAGE_COOLDOWN_S = 900
DEFAULT_GRACE_S = 30.0
GRACE_ENV = "DEVICE_SILENT_GRACE_S"
JOB_NAME = "device_silence"

_INCIDENT_COLUMNS = (
    "id", "device_id", "machine_id", "kind", "status", "opened_at", "last_seen_at",
    "resolved_at", "acknowledged_at", "acknowledged_by",
)
INCIDENT_SELECT = ", ".join(_INCIDENT_COLUMNS)


@dataclass(frozen=True)
class WatchdogSettings:
    grace_s: float = DEFAULT_GRACE_S

    @classmethod
    def from_env(cls) -> "WatchdogSettings":
        """DEVICE_SILENT_GRACE_S; unset/empty ⇒ default. Raises ValueError on a
        non-numeric or negative value (callers log it and use the default)."""
        raw = os.environ.get(GRACE_ENV, "").strip()
        if not raw:
            return cls()
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{GRACE_ENV} must be a number of seconds, got {raw!r}") from None
        if not (value >= 0) or value == float("inf"):
            raise ValueError(f"{GRACE_ENV} must be >= 0 and finite, got {raw!r}")
        return cls(grace_s=value)

    @classmethod
    def from_env_or_default(cls) -> "WatchdogSettings":
        try:
            return cls.from_env()
        except ValueError:
            logger.exception("Invalid %s; using %.0f s", GRACE_ENV, DEFAULT_GRACE_S)
            return cls()


def table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def incident_dict(row) -> dict:
    """A device_incidents row as the API / realtime shape (DeviceIncident)."""
    return {name: row[name] for name in _INCIDENT_COLUMNS}


class DeviceSilenceWatcher:
    """One instance per app; tick() is the scheduler job (job(conn, now))."""

    def __init__(
        self,
        *,
        grace_s: float,
        ingest_connected_for: Callable[[], float | None],
        notify: Callable[[sqlite3.Connection, dict], object] = dispatch.notify_device_incident,
        repage_cooldown_s: float = REPAGE_COOLDOWN_S,
        deliver: Callable[[Callable, str], None] | None = None,
    ) -> None:
        self.grace_s = float(grace_s)
        self._connected_for = ingest_connected_for
        self._notify = notify
        # With `deliver` (the app's NotificationWorker.submit) the page is
        # sent on the delivery thread, so a slow SMTP server never stalls the
        # scheduler tick; without it (unit tests) it is sent inline.
        self._deliver = deliver
        self._cooldown = timedelta(seconds=repage_cooldown_s)
        self._schema_ready = False
        self._lock = threading.Lock()
        self._last_tick_at: str | None = None
        self._last_error: str | None = None

    # --- Gate --------------------------------------------------------------------

    def armed(self) -> bool:
        try:
            connected_for = self._connected_for()
        except Exception:
            logger.exception("Could not read the MQTT connection age; watchdog stays idle")
            return False
        return connected_for is not None and connected_for >= self.grace_s

    def status(self) -> dict:
        with self._lock:
            return {
                "armed": self.armed(),
                "grace_s": self.grace_s,
                "last_tick_at": self._last_tick_at,
                "last_error": self._last_error,
            }

    # --- Tick --------------------------------------------------------------------

    def tick(self, conn: sqlite3.Connection, now: datetime) -> list[dict]:
        conn.row_factory = sqlite3.Row
        with self._lock:
            self._last_tick_at = protocol.format_utc(now)
        if not self.armed():
            return []
        if not self._ensure_schema(conn):
            return []
        if not (table_exists(conn, "device_status") and table_exists(conn, "device_incidents")):
            return []
        try:
            events, error = self._sweep(conn, now)
        except Exception as exc:
            with self._lock:
                self._last_error = repr(exc)
            raise
        with self._lock:
            self._last_error = error
        return events

    def _ensure_schema(self, conn) -> bool:
        """Lazy upgrade on the first armed tick (decision 8): a v2 DB whose
        nodes are all dead receives no message, so ingest would never migrate
        it — and silence would go undetected exactly when it matters."""
        if self._schema_ready:
            return True
        try:
            ensure_telemetry_schema(conn)
        except Exception as exc:
            logger.exception("Could not ensure the device-health tables exist")
            with self._lock:
                self._last_error = repr(exc)
            return False
        self._schema_ready = True
        return True

    def _open_incidents(self, conn) -> dict:
        rows = conn.execute(
            f"SELECT {INCIDENT_SELECT} FROM device_incidents WHERE status = 'open'"
        ).fetchall()
        return {row["device_id"]: row for row in rows}

    def _sweep(self, conn, now: datetime) -> tuple[list[dict], str | None]:
        """Returns the events plus the last per-device error (or None).

        Each open / resolve commits on its own, so a failure on one device
        (e.g. 'database is locked' while ingest writes) is logged and skipped
        rather than raised: raising would drop the events of incidents already
        committed earlier in this sweep, and since those incidents are no
        longer in the "needs action" set, no later tick would re-emit them.
        The failed device is simply retried on the next tick."""
        devices = conn.execute(
            """SELECT device_id, machine_id, online, last_seen_at, heartbeat_interval_s,
                      payload_json IS NOT NULL AS reported
               FROM device_status ORDER BY device_id"""
        ).fetchall()
        open_by_device = self._open_incidents(conn)
        events: list[dict] = []
        error: str | None = None
        for device in devices:
            try:
                event = self._step(conn, device, open_by_device.get(device["device_id"]), now)
            except Exception as exc:
                logger.exception("Device-health sweep failed for %s; retrying next tick",
                                 device["device_id"])
                error = repr(exc)
                try:
                    conn.rollback()
                except Exception:
                    logger.exception("Rollback after a device-health sweep failure failed")
                continue
            if event is not None:
                events.append(event)
        return events, error

    def _step(self, conn, device, current, now: datetime) -> dict | None:
        state = device_health.device_state(
            device["online"], device["last_seen_at"], device["heartbeat_interval_s"],
            reported=bool(device["reported"]), now=now,
        )
        if state == device_health.OFFLINE and current is None:
            return self._open(conn, device, now)
        if device_health.is_heard(state) and current is not None:
            return self._resolve(conn, current, now)
        return None

    def _open(self, conn, device, now: datetime) -> dict | None:
        at = protocol.format_utc(now)
        kind = "lwt" if not device["online"] else "silent"
        try:
            cur = conn.execute(
                """INSERT INTO device_incidents
                       (device_id, machine_id, kind, status, opened_at, last_seen_at, created_at)
                   VALUES (?, ?, ?, 'open', ?, ?, ?)""",
                (device["device_id"], device["machine_id"], kind, at, device["last_seen_at"], at),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Another instance opened it between our read and this INSERT; it
            # owns the page and the event.
            conn.rollback()
            return None
        # From here on the incident is committed: nothing below may stop the
        # event from being returned, or it is never broadcast (later ticks see
        # the incident as already open).
        written = {
            "id": cur.lastrowid, "device_id": device["device_id"],
            "machine_id": device["machine_id"], "kind": kind, "status": "open",
            "opened_at": at, "last_seen_at": device["last_seen_at"],
            "resolved_at": None, "acknowledged_at": None, "acknowledged_by": None,
        }
        incident = self._reread(conn, cur.lastrowid, written)
        logger.warning("Sensor node %s is offline (%s); incident %s opened",
                       device["device_id"], kind, incident["id"])

        try:
            recently_paged = self._recently_paged(conn, incident, now)
        except Exception:
            # Page anyway: a duplicate email beats a missed silent node.
            logger.exception("Re-page cooldown check failed for incident %s; paging",
                             incident["id"])
            recently_paged = False
        if recently_paged:
            logger.info("Not re-paging %s: previous incident opened within %.0f s",
                        device["device_id"], self._cooldown.total_seconds())
        else:
            self._page(conn, incident)
        return _event("device_offline", incident, at)

    def _page(self, conn, incident: dict) -> None:
        """Never undoes the committed incident over a paging failure."""
        notify = self._notify
        if self._deliver is not None:
            try:
                self._deliver(lambda delivery_conn: notify(delivery_conn, incident),
                              f"the page for device incident {incident['id']}")
            except Exception:
                logger.exception("Could not queue the page for device incident %s",
                                 incident["id"])
            return
        try:
            notify(conn, incident)
        except Exception:
            logger.exception("Paging failed for device incident %s", incident["id"])

    def _reread(self, conn, incident_id: int, written: dict) -> dict:
        """The committed row as stored, or what we wrote if the re-read fails."""
        try:
            row = conn.execute(
                f"SELECT {INCIDENT_SELECT} FROM device_incidents WHERE id = ?", (incident_id,)
            ).fetchone()
        except Exception:
            logger.exception("Could not re-read device incident %s; emitting it as written",
                             incident_id)
            return written
        return incident_dict(row) if row is not None else written

    def _recently_paged(self, conn, incident: dict, now: datetime) -> bool:
        # Timestamps are compared parsed: mixed 'Z' / '+00:00' suffixes make
        # string comparison unsafe.
        rows = conn.execute(
            "SELECT opened_at FROM device_incidents WHERE device_id = ? AND id != ?",
            (incident["device_id"], incident["id"]),
        ).fetchall()
        for row in rows:
            opened = device_health.parse_iso(row["opened_at"])
            if opened is not None and now - opened < self._cooldown:
                return True
        return False

    def _resolve(self, conn, current, now: datetime) -> dict | None:
        at = protocol.format_utc(now)
        changed = conn.execute(
            "UPDATE device_incidents SET status = 'resolved', resolved_at = ? "
            "WHERE id = ? AND status = 'open'",
            (at, current["id"]),
        ).rowcount
        conn.commit()
        if not changed:
            return None
        # Committed: as in _open, the event must survive a failed re-read.
        written = {**incident_dict(current), "status": "resolved", "resolved_at": at}
        incident = self._reread(conn, current["id"], written)
        logger.info("Sensor node %s heard again; incident %s resolved",
                    incident["device_id"], incident["id"])
        return _event("device_online", incident, at)


def _event(event_type: str, incident: dict, at: str) -> dict:
    return {
        "type": event_type,
        "device_id": incident["device_id"],
        "machine_id": incident["machine_id"],
        "incident": incident,
        "at": at,
    }


# --- Process-wide accessor (the /telemetry/status route reads it) ------------------

_watcher: DeviceSilenceWatcher | None = None


def set_watcher(watcher: DeviceSilenceWatcher | None) -> None:
    global _watcher
    _watcher = watcher


def get_watcher() -> DeviceSilenceWatcher | None:
    return _watcher
