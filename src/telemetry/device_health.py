"""The one sensor-node state rule (design/2026-10-06-device-health-design.md, decision 3).

Before device health the M6 online rule (contract §4) lived twice — in
ingest.device_online and in the KPI module — each with its own constants, and
the two could disagree. Everything that judges a node now comes here: ingest's
device_online wrapper, the /telemetry/devices route, the system KPIs and the
silence watchdog.

States, evaluated in this order (hb = the node's reported heartbeat interval,
or 10 s when it never reported a usable one; "heard" = the newer of
device_status.last_seen_at and, when the caller has it, the newest telemetry
message):

* never_reported — no status message was ever stored (payload_json IS NULL).
  Such a row was created by a snapshot (ingest._touch_device); without a
  heartbeat contract there is nothing to judge silence against.
* offline — the latest status said online: false (LWT or clean shutdown), or
  nothing usable was heard, or nothing for >= 3 x hb.
* stale — heard 1.5 x hb .. 3 x hb ago. Starts at 1.5x, not 1x, so a node that
  beats exactly every hb does not flicker on every poll.
* online — heard less than 1.5 x hb ago.

"online" in the legacy boolean sense (the devices route's `online` field,
cloud_sync_health.devices_online) is is_heard(): online or stale.

Imports nothing from ingest or kpi, so both can import it without a cycle.
"""
from __future__ import annotations

from datetime import datetime, timezone

DEFAULT_HEARTBEAT_S = 10.0
STALE_AFTER_HEARTBEATS = 1.5
OFFLINE_AFTER_HEARTBEATS = 3

ONLINE, STALE, OFFLINE, NEVER_REPORTED = "online", "stale", "offline", "never_reported"
DEVICE_STATES = (ONLINE, STALE, OFFLINE, NEVER_REPORTED)


def _utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def parse_iso(value) -> datetime | None:
    """ISO-8601 → aware UTC datetime. Accepts 'Z' and '+00:00' suffixes and
    treats naive values as UTC; None for anything missing or unparseable. A
    datetime passes straight through (normalised to UTC)."""
    if isinstance(value, datetime):
        return _utc(value)
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def effective_heartbeat_s(heartbeat_interval_s) -> float:
    """The node's reported heartbeat if usable (> 0), else the §4 default."""
    try:
        if heartbeat_interval_s is not None and float(heartbeat_interval_s) > 0:
            return float(heartbeat_interval_s)
    except (TypeError, ValueError):
        pass
    return DEFAULT_HEARTBEAT_S


def last_heard(last_seen_at, last_message_at=None) -> datetime | None:
    """The newer of the two "heard from" times; None if neither parses."""
    heard = [t for t in (parse_iso(last_seen_at), parse_iso(last_message_at)) if t is not None]
    return max(heard) if heard else None


def silent_for_s(last_seen_at, *, now: datetime | None = None, last_message_at=None) -> float | None:
    """Seconds since the node was last heard; None if that is unknown."""
    heard = last_heard(last_seen_at, last_message_at)
    if heard is None:
        return None
    return (_utc(now) - heard).total_seconds()


def device_state(online, last_seen_at, heartbeat_interval_s, *, reported: bool,
                 now: datetime | None = None, last_message_at=None) -> str:
    """Classify one node; see the module docstring for the rule."""
    if not reported:
        return NEVER_REPORTED
    if not online:
        return OFFLINE
    silent = silent_for_s(last_seen_at, now=now, last_message_at=last_message_at)
    if silent is None:
        return OFFLINE
    heartbeat = effective_heartbeat_s(heartbeat_interval_s)
    if silent >= OFFLINE_AFTER_HEARTBEATS * heartbeat:
        return OFFLINE
    if silent >= STALE_AFTER_HEARTBEATS * heartbeat:
        return STALE
    return ONLINE


def is_heard(state: str) -> bool:
    """The legacy "online" boolean: heard recently enough (online or stale)."""
    return state in (ONLINE, STALE)
