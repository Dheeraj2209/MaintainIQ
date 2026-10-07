"""Alert paging ladder (design/2026-10-07-work-orders-escalation-design.md, decisions 7-8).

"Paging", not "escalation": alert_escalated already means a severity increase
(and is republished to devices), so this feature uses its own names —
alerts.page_level, alerts.last_paged_at and the alert_paged event.

Level 0 is the email (and push) sent when an alert opens or its severity
rises (src/prediction/pipeline.fan_out). This job, run on the background
scheduler (src/background/scheduler.py), adds the levels above it for alerts
that are still open, unacknowledged and have no active work order:

* level 1 after ESCALATION_L1_MINUTES (default 15): re-page supervisors;
* level 2 after ESCALATION_L2_MINUTES (default 30): page admins. Top level.

Each ladder page goes out by email and push to the same roles
(src/notifications/dispatch.py); operators are pushed at level 0 only.

Age is measured from alerts.created_at (always the wall clock), not
opened_at, which is the reading time — replayed dataset alerts carry 2003
dates and would otherwise page instantly. Timestamps are parsed in Python;
mixed 'Z' / '+00:00' suffixes make string comparison unsafe.

An alert that crossed several levels since the last tick (app was down)
jumps straight to the highest one, with one email to the union of the
skipped levels' roles and one event. No double paging: the level is moved by
a compare-and-set UPDATE under live._TRANSITION_LOCK, and only the caller
whose UPDATE changed the row emails and emits. The email is sent after the
commit, outside the lock, and its failure never lowers the level.

Delivery never runs inside the tick. With a `deliver` callable (the app
passes src.background.delivery.NotificationWorker.submit) each page is handed
to the delivery thread, which opens its own connection, so the tick only does
the quick compare-and-set UPDATEs and its alert_paged events are broadcast
straight away — a slow or unreachable SMTP server can no longer hold every
event until the sweep ends, delay newer pages behind a backlog, or keep the
device watchdog from ticking. Without `deliver` (unit tests) the pages are
sent on the tick's connection after the whole sweep has committed.

Alerts that predate the ladder are not paged by it: migration 4 marks the
open alerts it finds as already at MAX_PAGE_LEVEL (with last_paged_at left
NULL, which the UI reads as "never paged by the ladder"), and the legacy batch
seed path (storage.db.insert_alerts) does the same for the historical alerts
it writes. Otherwise the first tick after an upgrade would page admins and
supervisors at level 2 for every months-old open alert.
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from src.alerts import live
from src.notifications import dispatch
from src.storage.migrations import ensure_current_schema
from src.telemetry import protocol
from src.telemetry.device_health import parse_iso

logger = logging.getLogger(__name__)

JOB_NAME = "alert_paging"
L1_ENV, L2_ENV = "ESCALATION_L1_MINUTES", "ESCALATION_L2_MINUTES"
DEFAULT_L1_MINUTES, DEFAULT_L2_MINUTES = 15.0, 30.0
MAX_PAGE_LEVEL = 2
LADDER = {1: ("supervisor",), 2: ("admin",)}

_ACTIVE_WORK_ORDER = (
    "EXISTS (SELECT 1 FROM work_orders w WHERE w.alert_id = alerts.id "
    "AND w.status IN ('open','assigned','in_progress'))"
)


def _minutes(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number of minutes, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be > 0 and finite, got {raw!r}")
    return value


@dataclass(frozen=True)
class PagingPolicy:
    l1_minutes: float = DEFAULT_L1_MINUTES
    l2_minutes: float = DEFAULT_L2_MINUTES

    def level_for(self, age: timedelta) -> int:
        """The level an alert of this age should be at: 0, 1 or 2."""
        if age >= timedelta(minutes=self.l2_minutes):
            return 2
        if age >= timedelta(minutes=self.l1_minutes):
            return 1
        return 0

    def roles_between(self, old: int, new: int) -> tuple[str, ...]:
        """Deduplicated union of LADDER[old+1 .. new], in ladder order."""
        roles: list[str] = []
        for level in range(old + 1, new + 1):
            for role in LADDER.get(level, ()):
                if role not in roles:
                    roles.append(role)
        return tuple(roles)

    @classmethod
    def from_env(cls) -> "PagingPolicy":
        """ESCALATION_L1_MINUTES / ESCALATION_L2_MINUTES; unset ⇒ defaults.
        Raises ValueError for non-numeric, <= 0 or non-finite values, or
        L2 <= L1."""
        l1 = _minutes(L1_ENV, DEFAULT_L1_MINUTES)
        l2 = _minutes(L2_ENV, DEFAULT_L2_MINUTES)
        if l2 <= l1:
            raise ValueError(f"{L2_ENV} ({l2:g}) must be greater than {L1_ENV} ({l1:g})")
        return cls(l1_minutes=l1, l2_minutes=l2)

    @classmethod
    def from_env_or_default(cls) -> "PagingPolicy":
        """Never disables paging: a bad value is logged and the defaults used."""
        try:
            return cls.from_env()
        except ValueError:
            logger.exception("Invalid %s/%s; using %g/%g minutes", L1_ENV, L2_ENV,
                             DEFAULT_L1_MINUTES, DEFAULT_L2_MINUTES)
            return cls()


def _before_page(conn, alert_id: int) -> None:
    """Test hook: runs between choosing a candidate and its compare-and-set."""


class AlertPager:
    """One instance per app; tick() is the scheduler job (job(conn, now))."""

    def __init__(self, *, policy: PagingPolicy, notify=dispatch.notify_alert,
                 deliver: Callable[[Callable, str], None] | None = None) -> None:
        self.policy = policy
        self._notify = notify
        self._deliver = deliver
        self._schema_ready = False
        self._lock = threading.Lock()
        self._last_tick_at: str | None = None
        self._last_error: str | None = None

    def status(self) -> dict:
        with self._lock:
            return {
                "l1_minutes": self.policy.l1_minutes,
                "l2_minutes": self.policy.l2_minutes,
                "last_tick_at": self._last_tick_at,
                "last_error": self._last_error,
            }

    def tick(self, conn: sqlite3.Connection, now: datetime) -> list[dict]:
        conn.row_factory = sqlite3.Row
        with self._lock:
            self._last_tick_at = protocol.format_utc(now)
        if not self._ensure_schema(conn):
            return []
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
        if "page_level" not in columns:
            return []
        events, error = self._sweep(conn, now)
        with self._lock:
            self._last_error = error
        return events

    def _ensure_schema(self, conn) -> bool:
        """A pre-v4 file is upgraded on the first tick, like the watchdog's."""
        if self._schema_ready:
            return True
        try:
            ensure_current_schema(conn)
        except Exception as exc:
            logger.exception("Could not bring the database schema up to date for paging")
            with self._lock:
                self._last_error = repr(exc)
            return False
        self._schema_ready = True
        return True

    def _sweep(self, conn, now: datetime) -> tuple[list[dict], str | None]:
        """Each alert is paged and committed on its own; a failure on one is
        logged and retried next tick instead of dropping the events of alerts
        already paged in this sweep."""
        candidates = conn.execute(
            f"""SELECT id, created_at, page_level FROM alerts
                WHERE status = 'open' AND acknowledged_at IS NULL AND page_level < ?
                  AND NOT {_ACTIVE_WORK_ORDER}
                ORDER BY id""",
            (MAX_PAGE_LEVEL,),
        ).fetchall()
        events: list[dict] = []
        deferred: list[tuple[Callable, str]] = []
        error: str | None = None
        for row in candidates:
            created = parse_iso(row["created_at"])
            if created is None:
                logger.debug("Alert %s has an unparseable created_at %r; not paging it",
                             row["id"], row["created_at"])
                continue
            target = self.policy.level_for(now - created)
            if target <= row["page_level"]:
                continue
            try:
                paged = self._page(conn, row["id"], row["page_level"], target, now)
            except Exception as exc:
                logger.exception("Paging alert %s failed; retrying next tick", row["id"])
                error = repr(exc)
                try:
                    conn.rollback()
                except Exception:
                    logger.exception("Rollback after a paging failure failed")
                continue
            if paged is None:
                continue
            event, task, description = paged
            events.append(event)
            self._hand_off(task, description, deferred)
        for task, description in deferred:  # inline mode: after every commit
            self._run_delivery(task, conn, description)
        return events, error

    def _hand_off(self, task, description: str, deferred: list) -> None:
        if self._deliver is None:
            deferred.append((task, description))
            return
        try:
            self._deliver(task, description)
        except Exception:
            logger.exception("Could not queue %s", description)

    @staticmethod
    def _run_delivery(task, conn, description: str) -> None:
        try:
            task(conn)
        except Exception:
            logger.exception("Failed to send %s", description)

    def _page(self, conn, alert_id: int, old: int, target: int, now: datetime):
        """Compare-and-set the level; returns (event, delivery task,
        description), or None when another caller won the row."""
        _before_page(conn, alert_id)
        paged_at = now.isoformat()
        with live._TRANSITION_LOCK:
            cur = conn.execute(
                f"""UPDATE alerts SET page_level = ?, last_paged_at = ?
                    WHERE id = ? AND page_level = ? AND status = 'open'
                      AND acknowledged_at IS NULL AND NOT {_ACTIVE_WORK_ORDER}""",
                (target, paged_at, alert_id, old),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None  # acknowledged, resolved, given an order, or paged by someone else
            conn.commit()
            alert = live.get_alert(conn, alert_id)
        logger.warning("Alert %s unacknowledged; paged level %s", alert_id, target)

        # Committed: nothing below may stop the event from being returned.
        roles = self.policy.roles_between(old, target)
        notify = self._notify

        def task(delivery_conn):
            notify(delivery_conn, alert, roles=roles, page_level=target)

        event = {
            "type": "alert_paged",
            "machine_id": alert["machine_id"],
            "alert": alert,
            "page_level": target,
            "at": protocol.format_utc(now),
        }
        return event, task, f"the level-{target} page for alert {alert_id}"


_pager: AlertPager | None = None


def set_pager(pager: AlertPager | None) -> None:
    """Called by the app lifespan when the scheduler starts / stops."""
    global _pager
    _pager = pager


def get_pager() -> AlertPager | None:
    """The running pager, or None when background jobs are disabled."""
    return _pager
