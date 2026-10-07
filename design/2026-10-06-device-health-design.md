# Device Health (sensor-node status + silence alerting) — Design

Status: Approved (decisions below are final for implementation)
Date: 2026-10-06
Source: user requirement (verbatim) "Device health: show the status of each sensor node, and alert when one goes silent."; `design/_integration_map.md` §2 (feature 3, migration 3); contract `design/M6_LIVE_TELEMETRY.md` §4 (online rule), §6 (storage), §8 (API).

## Problem

M6 stores the latest status of every sensor node in `device_status` and `GET /api/telemetry/devices` reports a boolean `online` per node, but:

- Only admins and supervisors can see node status: it sits inside `LiveTelemetryPanel` on the role-gated Ingestion page (`frontend/src/pages/IngestionPage.tsx:79`).
- A node that goes silent raises nothing. Nobody is paged, nothing appears in realtime, and no record shows when it went dark, who noticed or when it came back.
- The online rule is implemented twice: `src/telemetry/ingest.py:141 device_online` and `src/kpi/calculations.py:598 _device_online`, each with its own constants (`ingest.py:67-68`, `calculations.py:323-324`). The two can disagree.
- There is no background scheduler. Silence is the absence of messages, so nothing can notice it.

This feature makes node health a first-class concept: one state rule, a persisted incident per silence episode, a background watchdog that opens and resolves incidents, email paging, realtime events, an acknowledge action, and a Devices page that every role can open.

## Goal

1. Every authenticated user can see each sensor node's state (`online` / `stale` / `offline` / `never_reported`), when it was last heard, and whether it has an open incident.
2. When a node goes silent or disconnects, a `device_incidents` row is opened. Admins and supervisors are emailed, and every dashboard gets a `device_offline` event.
3. When the node is heard again, the incident is resolved automatically and a `device_online` event is sent.
4. Any role can acknowledge an incident.
5. Ingest, the devices route and the KPIs all compute state through one shared rule.

## Non-goals

- Paging escalation for unacknowledged device incidents. Feature 1 owns the paging ladder and may later consume `device_incidents.acknowledged_at`.
- SMS or web push (feature 5 adds a push channel to `notify_device_incident`).
- Device registry management: renaming, labels or location (the optional `device_status.label/location` ALTERs from the map are **not** added), decommissioning, deleting nodes, or reassigning a node to a machine.
- Configurable per-device thresholds. The threshold comes from the node's own reported `heartbeat_interval_s`.
- Silence detection for `never_reported` nodes, i.e. nodes that sent snapshots but never a status message. With no heartbeat contract there is nothing to judge silence against (see decision 4).
- Letting device health affect machine health or RUL. Device incidents never touch `alerts`, machine health or the ML path.
- Republishing device events to devices over MQTT.

## Decisions

1. **Separate `device_incidents` table, not `alerts`.** `live._open_alert` (`src/alerts/live.py:28-33`) has no kind filter: a device incident would become the machine's "open alert", a healthy reading would auto-resolve it, and severity escalation would rewrite it. `SEVERITY_RANK` has no device severity. `alerts.machine_id` is NOT NULL, but `device_status.machine_id` is nullable. A separate table keeps every existing `alerts` query, KPI and report unchanged.

2. **One shared state rule in a new module, `src/telemetry/device_health.py`.**
   - `ingest.device_online` becomes a thin wrapper around it, keeping its signature for existing callers and tests.
   - `kpi._device_online` delegates to it too, passing its extra `last_message_at` input, which the rule accepts as an optional keyword.
   - The constants `DEFAULT_HEARTBEAT_S` and `OFFLINE_AFTER_HEARTBEATS` move into the new module. `ingest.py` re-exports them under the same names, and the KPI copies at `calculations.py:323-324` are deleted.
   - Rationale: one definition, no import cycle (the new module imports nothing from `ingest` or `kpi`), and the module is unit-testable on its own.

3. **States**, evaluated in this order with `hb` = `heartbeat_interval_s` if > 0, else 10 s, and `heard` = max(`last_seen_at`, `last_message_at` if given):

   | State | Condition |
   |---|---|
   | `never_reported` | no status message ever stored for the node (`device_status.payload_json IS NULL`) |
   | `offline` | the latest status said `online: false` (LWT or clean shutdown), OR `heard` is missing or unparseable, OR `now − heard ≥ 3 × hb` |
   | `stale` | `1.5 × hb ≤ now − heard < 3 × hb` |
   | `online` | `now − heard < 1.5 × hb` |

   - **Stale starts at 1.5 × hb, not the map's 1 ×.** A node beating exactly every `hb` would otherwise flicker to `stale` on every poll that lands just before the next heartbeat. The 3 × offline boundary stays exactly as in contract §4.
   - **Back-compat:** "online" in the old boolean sense means `state ∈ {online, stale}`. `device_online()`, the devices route's existing `online` field and `cloud_sync_health.devices_online` keep that meaning, so the KPI numbers and their response shape do not change.
   - **Why `payload_json IS NULL` marks never_reported:** `_touch_device` (`ingest.py:622`) inserts rows with `online = 0` and no payload, while `_upsert_status` (`ingest.py:586`) always writes `payload_json`, including for an LWT. That makes it the only reliable marker. An LWT-only node counts as "reported", so it is `offline`.

4. **What opens an incident:**
   - A node in state `offline` with no open incident. `kind = 'lwt'` if the stored flag is `online = 0` (LWT or clean shutdown, which the protocol cannot tell apart); otherwise `kind = 'silent'`.
   - LWT incidents therefore open on the first armed tick, without waiting 3 × hb.
   - `stale` never opens an incident. It only shows in the UI.
   - `never_reported` never opens one either: without a heartbeat contract we cannot tell "silent" from "configured not to send status".

5. **What resolves it:** the incident is resolved when the node's state is `online` or `stale` again (it has been heard). The watchdog sets `status = 'resolved'` and `resolved_at = now`, and acknowledgement fields are kept.
   - **The watchdog is the only writer of `status`/`resolved_at`.** We do not also resolve from `ingest.handle_status`. Resolution latency is at most one sweep interval (5 s recommended), which is fine for a silence alarm. One writer means the open and resolve paths never race, and ingest worker threads get no new side effects.
   - **A later silence opens a new incident** (new id), so every episode is auditable.

6. **Gating: no false alarms when we are the ones who are deaf.** A tick does nothing at all (no open, no resolve, no events) unless:
   - live ingest is enabled (an `MqttIngestService` exists),
   - it is connected now, and
   - it has been continuously connected for at least `DEVICE_SILENT_GRACE_S` (default 30 s).

   For this the service gets a new public `connected_for_s() -> float | None`, built from the existing `_connected_mono` (`src/telemetry/mqtt_service.py:179`, set in `_on_connect` at :273-283). It returns `None` while not started or disconnected.

   The grace window covers app start and every reconnect, during which retained-status replay and the queued QoS-1 backlog would make live nodes look stale. Resolving is skipped too while gated: nothing new can be heard while we are deaf, and skipping keeps the gate a single, simple rule.

7. **Generic background scheduler, `src/background/scheduler.py`.**
   - It is built in the app lifespan and is **disabled unless `MAINTAINIQ_SWEEP_INTERVAL_S > 0`**. Feature 1 (paging escalation) reuses it.
   - Jobs are plain sync callables `job(conn, now) -> list[dict]`. They run via `asyncio.to_thread`, each tick on its own connection from an injectable `connection_factory` (default `src.storage.db.get_connection`).
   - **Events** returned by a job are broadcast from the loop with `await manager.broadcast(...)`.
   - **Ticks never overlap.**
   - **The first tick happens one interval after start, not immediately.** This avoids a startup race, and a lifespan test with a long interval never touches a DB.
   - **Errors:** a job exception is logged (`logger.exception`) and does not stop the loop or the other jobs.
   - **Jobs do no slow I/O** (fix, 2026-10-07). Jobs run one after another and their events are broadcast only when the job returns, so email/push from a job goes through the delivery worker (`src/background/delivery.py`: one daemon thread, FIFO, own connection per task, drained with a bound on shutdown) rather than inside the tick.
   - **Accessors:** `set_scheduler()` / `get_scheduler()`.
   - **Lifespan wiring:** start it right after `_start_mqtt_ingest()` (`src/api/app.py:81`). Stop it first in `finally`, before MQTT stops: its gate reads the MQTT service.

8. **Lazy schema upgrade, like ingest.** Migrations do not run at app start (map §1). The first *armed* watchdog tick calls `ingest.ensure_telemetry_schema(conn)` (`ingest.py:172`), which runs the normal migration runner on a DB at v ≥ 1. This is the same lazy precedent ingest already uses when the first MQTT message arrives.
   - **Why the tick needs it:** a v2 DB whose nodes are all dead receives no message, so ingest would never migrate it, and silence would go undetected exactly when it matters.
   - **v0 (legacy) DBs:** `ensure_telemetry_schema`'s below-v1 branch also applies `DEVICE_HEALTH_SCHEMA`.
   - **Missing table:** if `device_incidents` still does not exist, the tick returns `[]`. The routes guard against it with a `sqlite_master` check.

9. **Email paging:**
   - **Who:** every active admin and supervisor, using the generalised `dispatch._recipients(conn, roles=('admin', 'supervisor'))`. The default keeps `notify_alert` behaviour byte-identical.
   - **When:** only when an incident *opens*. Recovery is not emailed (resolved alerts are not emailed either: `pipeline._PAGING_EVENTS`, `src/prediction/pipeline.py:27`).
   - **Flap guard:** if the same device had another incident *opened* within the last 15 minutes (`REPAGE_COOLDOWN_S = 900`, a module constant), the new incident still opens and broadcasts, but no email is sent. A marginal-Wi-Fi node must not mail-bomb supervisors. This is fixed in code rather than read from env, to keep the configuration small.
   - **Order:** email happens only after the incident INSERT has committed. The INSERT is protected by a partial unique index (one open incident per device). When two app instances race, the loser's INSERT fails and it sends nothing, so there are no duplicate pages.
   - **Notification log:** `notifications` gains `device_incident_id`. Device rows have `alert_id = NULL` (already nullable).
   - **Failures:** a failed delivery is logged as a `failed` row. Any exception is caught with `logger.exception` and never rolls back the committed incident (pattern at `pipeline.py:91-113`).

10. **Acknowledge.**
    - **Endpoint:** `POST /api/telemetry/incidents/{id}/acknowledge`, available to all roles via `require_role("admin", "supervisor", "operator")`, which also provides the user id. This mirrors `src/api/routes/alerts.py:45-77`.
    - **Behaviour:** idempotent. Acknowledging an already-acknowledged incident returns 200 unchanged. Resolved incidents can be acknowledged, which keeps a future time-to-acknowledge KPI unbiased (same reasoning as the alert-ack design, decision 3).
    - **Race-free write:** `UPDATE … SET acknowledged_at, acknowledged_by WHERE id = ? AND acknowledged_at IS NULL`. The watchdog never writes these columns, so no lock is needed. `live._TRANSITION_LOCK` covers `alerts` only and is not involved.
    - **No realtime broadcast on ack.** The decided event set is `device_offline`/`device_online`, and the Devices page polls every 5 s, so another viewer sees the ack within one poll. This also avoids a third toast type.

11. **Realtime events.**
    - **Shape:** `device_offline` and `device_online`, shaped `{type, device_id, machine_id: str | null, incident: DeviceIncident, at}`.
    - **Not republished:** `protocol.ALERT_EVENT_TYPES` (`src/telemetry/protocol.py:65`) is left alone, so `is_alert_event` (:659) filters these out and the MQTT republisher (`mqtt_service._on_realtime_event`) never sends them to devices.
    - **Naming:** `alert_escalated` is not reused.

12. **`LiveEvent` becomes a discriminated union now.** It is `AlertLiveEvent | DeviceLiveEvent`, with `LiveEventType = LiveEvent['type']`.
    - **Unknown types:** frames whose `type` is not in a `KNOWN_EVENT_TYPES` set are ignored by the provider: no `lastEvent` update and no toast. A newer server, such as feature 1's `work_order_*` events, cannot then crash `describe()`, which today dereferences `event.alert` (`frontend/src/realtime/LiveEventsProvider.tsx:28-41`).

13. **Devices page for every role, under Monitor.**
    - **Route:** `devices` in the ungated block (`frontend/src/App.tsx:43-50`), with a nav entry after Alerts (`frontend/src/layout/AppShell.tsx:27-34`, lucide `RadioTower`).
    - **Ingestion page:** keeps `LiveTelemetryPanel`, which now renders the shared `DevicesTable`.

14. **Migration 3** (map §1 numbering).
    - The DDL lives in `DEVICE_HEALTH_SCHEMA` in `src/storage/db.py`, folded into `SCHEMA`.
    - `notifications.device_incident_id` is added to the base `notifications` CREATE (`db.py:172`) and also via a guarded ALTER in the step.
    - **One mixed step:** `executescript` followed by a guarded `ALTER`. This is acceptable: the ALTER is idempotent and `executescript`'s implicit COMMIT runs before it, so a partial failure re-runs cleanly (map §3, same justification as migration 4).

## Data model

### `src/storage/db.py`

```python
# Device-health incidents (design/2026-10-06-device-health-design.md). One row
# per silence episode of a sensor node; opened and resolved only by the
# background watchdog (src/telemetry/watchdog.py), acknowledged by a human.
# Separate from `alerts` on purpose: alerts are machine-health episodes and
# the live alert path would auto-resolve or escalate a device incident.
DEVICE_HEALTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id        TEXT NOT NULL,
    machine_id       TEXT,
    kind             TEXT NOT NULL CHECK(kind IN ('silent','lwt')),
    status           TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
    opened_at        TEXT NOT NULL,
    last_seen_at     TEXT,
    resolved_at      TEXT,
    acknowledged_at  TEXT,
    acknowledged_by  INTEGER,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_device_incidents_device_status ON device_incidents(device_id, status);
CREATE INDEX IF NOT EXISTS idx_device_incidents_opened ON device_incidents(opened_at);
-- At most one open incident per device: makes the watchdog's open path
-- idempotent and stops a second app instance from double-paging.
CREATE UNIQUE INDEX IF NOT EXISTS uq_device_incidents_one_open
    ON device_incidents(device_id) WHERE status = 'open';
"""

SCHEMA = SCHEMA + TELEMETRY_SCHEMA + DEVICE_HEALTH_SCHEMA
```

Column semantics:
- **`opened_at`** is the watchdog's detection time, the same as `created_at`. The two are kept separate for parity with `alerts`.
- **`last_seen_at`** copies the node's last-heard time when the incident opened, so "silent since" is preserved even after `device_status` moves on.
- **`machine_id`** is a snapshot of `device_status.machine_id` and may be NULL.
- **`acknowledged_by`** is a `users.id` with no FK, matching `alerts.acknowledged_by`.
- **Conventions:** all timestamps are ISO-8601 UTC TEXT written by `protocol.format_utc`.

Add `device_incident_id INTEGER REFERENCES device_incidents(id)` to the `notifications` CREATE (after `alert_id`).

### `src/storage/migrations.py`

```python
def _migration_003_device_health(conn: sqlite3.Connection) -> None:
    # Additive: a new table plus one nullable column. executescript() COMMITs
    # first; the guarded ALTER after it is idempotent, so a crash between the
    # two re-runs cleanly.
    conn.executescript(DEVICE_HEALTH_SCHEMA)
    try:
        conn.execute("ALTER TABLE notifications ADD COLUMN device_incident_id INTEGER "
                     "REFERENCES device_incidents(id)")
    except sqlite3.OperationalError:
        pass  # fresh DB: SCHEMA already has the column

MIGRATIONS = [
    (1, _migration_001_canonical_baseline),
    (2, _migration_002_live_telemetry),
    (3, _migration_003_device_health),
]
```

The module docstring gains a "Migration 3" paragraph.

## State rule — `src/telemetry/device_health.py` (new)

```python
DEFAULT_HEARTBEAT_S = 10.0
STALE_AFTER_HEARTBEATS = 1.5
OFFLINE_AFTER_HEARTBEATS = 3
ONLINE, STALE, OFFLINE, NEVER_REPORTED = "online", "stale", "offline", "never_reported"
DEVICE_STATES = (ONLINE, STALE, OFFLINE, NEVER_REPORTED)

def effective_heartbeat_s(heartbeat_interval_s) -> float: ...
def last_heard(last_seen_at, last_message_at=None) -> datetime | None: ...
def device_state(online, last_seen_at, heartbeat_interval_s, *, reported: bool,
                 now: datetime | None = None, last_message_at: datetime | None = None) -> str: ...
def silent_for_s(last_seen_at, *, now=None, last_message_at=None) -> float | None: ...
def is_heard(state: str) -> bool:  # state in (ONLINE, STALE) — the legacy "online" boolean
```

- `ingest.device_online(online, last_seen_at, heartbeat_interval_s, *, now=None)` becomes `is_heard(device_state(online, last_seen_at, heartbeat_interval_s, reported=True, now=now))`, with the signature unchanged. `reported=True` is safe there because the old boolean only ever cared about online-ness, and `online = 1` implies a status was stored.
- `kpi._device_online(status, last_message_at, now)` becomes `is_heard(device_state(status["online"], status["last_seen_at"], status["heartbeat_interval_s"], reported=True, now=now, last_message_at=last_message_at))`.
- Timestamp parsing accepts `Z` and `+00:00` suffixes and treats naive values as UTC, like `ingest._parse_iso`. That helper moves into `device_health` and `ingest` imports it.

## Watchdog — `src/telemetry/watchdog.py` (new)

```python
class DeviceSilenceWatcher:
    def __init__(self, *, grace_s: float, ingest_connected_for: Callable[[], float | None],
                 notify=dispatch.notify_device_incident, repage_cooldown_s: float = REPAGE_COOLDOWN_S): ...
    def armed(self) -> bool                       # gate (decision 6)
    def tick(self, conn, now: datetime) -> list[dict]   # scheduler job signature
    def status(self) -> dict                      # {"armed", "grace_s", "last_tick_at", "last_error"}
```

`tick` algorithm (sync, runs on a scheduler thread with its own connection):

1. If not `armed()`: record `last_tick_at` and return `[]`.
2. On the first armed tick, call `ensure_telemetry_schema(conn)`. If `device_incidents` is still missing, return `[]`.
3. Load `device_id, machine_id, online, last_seen_at, heartbeat_interval_s, payload_json IS NOT NULL AS reported` from `device_status`, and all `status = 'open'` incidents keyed by `device_id`.
4. For each device, compute `state = device_state(..., reported=row["reported"], now=now)`:
   - `state == OFFLINE` with no open incident: INSERT the incident and commit. If the INSERT raises `sqlite3.IntegrityError` (another instance won the race), skip. Otherwise:
     - check the flap guard (`SELECT 1 FROM device_incidents WHERE device_id = ? AND id != ? AND opened_at` within cooldown, comparing parsed timestamps in Python);
     - if not suppressed, call `notify(conn, incident)` inside try/except — or, when the watcher has a `deliver` callable (the app passes the shared `NotificationWorker.submit`, `src/background/delivery.py`), queue it for the delivery thread so a slow SMTP server never stalls the tick (fix, 2026-10-07);
     - append `{"type": "device_offline", "device_id", "machine_id", "incident", "at": format_utc(now)}`.
   - `is_heard(state)` with an open incident: `UPDATE … SET status = 'resolved', resolved_at = ? WHERE id = ? AND status = 'open'`, commit, and append `device_online` if a row changed.
   - Anything else: nothing.
5. Return the events. The scheduler broadcasts them.

## Scheduler — `src/background/scheduler.py` (new)

```python
@dataclass(frozen=True)
class SchedulerSettings:
    interval_s: float            # 0 ⇒ disabled
    @classmethod
    def from_env(cls) -> "SchedulerSettings"   # ValueError on non-numeric/negative

class BackgroundScheduler:
    def __init__(self, *, interval_s: float, connection_factory=get_connection,
                 broadcast=manager.broadcast, now_fn=lambda: datetime.now(timezone.utc)): ...
    def register(self, name: str, job: Callable[[sqlite3.Connection, datetime], list[dict]]) -> None
    async def run_once(self) -> list[dict]   # one tick of every job; used by tests
    def start(self) -> None                  # creates the asyncio task on the running loop
    async def stop(self) -> None             # idempotent; cancels and awaits the task
    @property
    def jobs(self) -> tuple[str, ...]

def set_scheduler(s: BackgroundScheduler | None) -> None
def get_scheduler() -> BackgroundScheduler | None
```

Lifespan (`src/api/app.py:70-96`):

```python
mqtt_service = _start_mqtt_ingest()
scheduler = _start_background_jobs(mqtt_service)   # None unless MAINTAINIQ_SWEEP_INTERVAL_S > 0
try:
    yield
finally:
    if scheduler is not None:
        await scheduler.stop(); set_scheduler(None)
    ... existing MQTT stop, replay stop, manager.bind_loop(None)
```

`_start_background_jobs` works like `_start_mqtt_ingest`:
- It is never fatal: invalid env is logged and the scheduler stays off.
- It registers the `device_silence` job with `ingest_connected_for = mqtt_service.connected_for_s if mqtt_service else (lambda: None)`.
- It uses a module-level `_scheduler_connection_factory = get_connection` that tests monkeypatch.

## API

All routes live in `src/api/routes/telemetry.py`. That router is already mounted behind `get_current_user` (`src/api/app.py:113`), so anonymous requests get 401.

| Method & path | Roles | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/telemetry/devices?machine_id=` | any | optional `machine_id` filter | `list[DeviceHealth]` ordered by `device_id` | — (pre-M6 DB ⇒ `[]`) |
| `GET /api/telemetry/devices/{device_id}` | any | — | `DeviceDetail` = `DeviceHealth` + `recent_incidents: list[DeviceIncident]` (newest 10) | 404 `unknown device: {device_id}` |
| `GET /api/telemetry/incidents?status=&device_id=&limit=` | any | `status` ∈ `open`/`resolved` (omit = all), `limit` 1–2000 (default 200) | `list[DeviceIncident]` ordered `(status='open') DESC, opened_at DESC` | 400 `invalid status: {s}`; 422 for a `limit` out of range; missing table ⇒ `[]` |
| `POST /api/telemetry/incidents/{id}/acknowledge` | admin, supervisor, operator | — | `DeviceIncident` | 404 `unknown incident: {id}` (also when the table is missing) |
| `GET /api/telemetry/status` | any | — | existing §8 body + `device_watchdog` block (below) | — |

`src/api/schemas.py`:

```python
DeviceState = Literal["online", "stale", "offline", "never_reported"]

class DeviceIncident(BaseModel):
    id: int
    device_id: str
    machine_id: Optional[str] = None
    kind: Literal["silent", "lwt"]
    status: Literal["open", "resolved"]
    opened_at: str
    last_seen_at: Optional[str] = None
    resolved_at: Optional[str] = None
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[int] = None

class DeviceHealth(BaseModel):
    # every existing _DEVICE_COLUMNS field (src/api/routes/telemetry.py:52-57), unchanged
    device_id: str; machine_id: Optional[str]; online: bool; last_seen_at: str; firmware: Optional[str]
    uptime_s/buffer_*/publish_*/wifi_rssi_dbm/snapshot_interval_s/heartbeat_interval_s: Optional[...]
    # new
    state: DeviceState
    silent_for_s: Optional[float]        # now − last heard, 1 decimal; None if never parseable
    expected_heartbeat_s: float          # effective heartbeat (10.0 when unreported)
    open_incident_id: Optional[int]

class DeviceDetail(DeviceHealth):
    recent_incidents: list[DeviceIncident]

class NotificationOut(BaseModel):  # + device_incident_id: Optional[int] = None
```

- `online` keeps its meaning (`state ∈ {online, stale}`), so the existing route test and `LiveTelemetryPanel` stay valid.
- The devices query LEFT JOINs the open incident on `device_id` (guarded: no join if the table is missing).

`device_watchdog` block on `/telemetry/status`, in both the enabled and the disabled body:

```json
"device_watchdog": {"enabled": true, "interval_s": 5.0, "grace_s": 30.0,
                    "armed": false, "last_tick_at": "2026-10-06T12:00:05Z"}
```

`enabled = false` (with `interval_s: null`) when the scheduler is off. It is built in the route from `get_scheduler()` and the watcher's `status()`, not inside `MqttIngestService`, so `disabled_status()` stays as it is. The UI uses it to say honestly whether silence alerting is active.

`src/api/routes/notifications.py` adds `device_incident_id` to its SELECT. `NotificationsPage` needs no visual change; it does not render `alert_id`.

## Realtime events

```json
{"type": "device_offline", "device_id": "esp32-a1b2c3", "machine_id": "sim-01",
 "incident": {"id": 7, "device_id": "esp32-a1b2c3", "machine_id": "sim-01", "kind": "silent",
              "status": "open", "opened_at": "…", "last_seen_at": "…", "resolved_at": null,
              "acknowledged_at": null, "acknowledged_by": null},
 "at": "2026-10-06T12:00:35Z"}
{"type": "device_online", "...same keys...", "incident": {"status": "resolved", "resolved_at": "…", ...}}
```

They are sent by the scheduler via `await manager.broadcast(event)` (`src/realtime/manager.py:56`) on the loop, never from listeners. They are not in `ALERT_EVENT_TYPES`, so they are never published to MQTT.

## Notification behaviour

`src/notifications/dispatch.py`:

- `_recipients(conn, roles=("admin", "supervisor"))` is parameterised with placeholders (`role IN (?, ?)`). `notify_alert` calls it with no argument, so its behaviour and `tests/test_notifications.py` are unchanged.
- New `notify_device_incident(conn, incident, *, roles=("admin", "supervisor")) -> int`:
  - Subject: `MaintainIQ DEVICE: {device_id} went silent` for `silent`, or `MaintainIQ DEVICE: {device_id} disconnected` for `lwt`. Machine id is appended when known.
  - Body: device, machine (or "unassigned"), kind explained in words, last heard, opened at, and "Acknowledge on the Devices page."
  - Each recipient gets a `notifications` row with `alert_id = NULL`, `device_incident_id = incident["id"]`, `status` `sent`/`failed`. Commit, then return the number sent.
- Operators are not emailed, consistent with the dispatch module docstring. Feature 5 adds push for them.

## Frontend UX

**Types (`frontend/src/api/types.ts`):**
- `DeviceState`, `DeviceIncident`, `DeviceIncidentStatus`, `DeviceDetail`.
- `TelemetryDevice` (:289-304) gains `state`, `silent_for_s`, `expected_heartbeat_s`, `open_incident_id`.
- `TelemetryStatus` gains `device_watchdog: DeviceWatchdogStatus`.
- `NotificationOut` gains `device_incident_id`.
- `LiveEvent` (:176-187) becomes:

  ```ts
  export type AlertEventType = 'alert_created' | 'alert_escalated' | 'alert_resolved' | 'alert_acknowledged'
  export interface AlertLiveEvent { type: AlertEventType; machine_id: string; alert: Alert; at: string; prediction?: RULPrediction }
  export type DeviceEventType = 'device_offline' | 'device_online'
  export interface DeviceLiveEvent { type: DeviceEventType; device_id: string; machine_id: string | null; incident: DeviceIncident; at: string }
  export type LiveEvent = AlertLiveEvent | DeviceLiveEvent
  export type LiveEventType = LiveEvent['type']
  export function isDeviceEvent(e: LiveEvent): e is DeviceLiveEvent
  ```

**Client (`frontend/src/api/client.ts`, next to :138-139):**
- `getTelemetryDevices(opts?: { machineId?: string })`
- `getTelemetryDevice(id)`
- `getDeviceIncidents(opts?: { status?: DeviceIncidentStatus; deviceId?: string; limit?: number })`
- `acknowledgeDeviceIncident(id)` (POST)

**Styles (`frontend/src/components/healthStyles.ts`):**
- `deviceStateClasses(state)` maps `online → healthy`, `stale → degrading`, `offline → critical`, `never_reported → unknown`, reusing `MAP` (intensity tokens, not new colours).
- `deviceStateLabel(state)` returns `Online` / `Stale` / `Offline` / `Never reported`.

**`components/DevicesTable.tsx` (new):**
- Moved out of `LiveTelemetryPanel.tsx` (`BufferBar` :179, `DevicesTable` :207). `LiveTelemetryPanel` imports it.
- The leading dot's `aria-label` becomes the state label in lower case (`online`, `stale`, `offline`, `never reported`). The existing `LiveTelemetryPanel.test.tsx:59,64` assertions on `online`/`offline` stay valid.
- New optional columns, enabled on the Devices page: State chip, Silent for, Incident.

**`pages/DevicesPage.tsx` (new, route `/devices`, all roles):**
- Header "Sensor nodes" with summary chips: count per state.
- **Banner** (role `status`) shown when:
  - ingest is disabled ("Live ingest is off — no sensor nodes can report"),
  - ingest is disconnected ("Broker disconnected — silence alerting paused"),
  - `device_watchdog.enabled` is false ("Silence alerting is off: set MAINTAINIQ_SWEEP_INTERVAL_S"), or
  - the watchdog is enabled but not yet armed ("Silence alerting arms 30 s after the broker connects").
- **Devices table:** state chip, device, machine (link to `/machines/:id` when set), last seen (relative, `formatRelative`), silent for, heartbeat, edge buffer, RSSI, firmware, and an incident cell. The incident cell shows "Open since …" with an **Acknowledge** button, or "Acknowledged" when already acknowledged.
- **Incidents section** "Incident history" with Open / Resolved / All tabs, from `getDeviceIncidents({status})`. Columns: device, machine, kind ("Went silent" / "Disconnected"), opened, last heard, resolved, acknowledged (+ button).
- **Acknowledge:** calls the API, toasts success, and re-fetches both lists. The button is disabled while its request is in flight.
- **Refresh:** polls every 5 s (`TELEMETRY_POLL_MS`, `LiveTelemetryPanel.tsx:33` pattern, with a ticking `now` for relative times). It also re-fetches immediately when `lastEvent` is a device event.

**Nav and routing:**
- `AppShell.tsx` Monitor section (:27-34): `{ to: '/devices', label: 'Devices', icon: RadioTower }` after Alerts.
- `App.tsx` (:43-50): `<Route path="devices" element={<DevicesPage />} />`.

**Machine detail chip (`components/MachineDetail.tsx` facts grid :112-128):**
- New `<Fact label="Sensor node">` rendering a chip with the worst state across the machine's nodes (offline > stale > never_reported > online) and the device id. The data comes from `api.getTelemetryDevices({ machineId })` in its own effect.
- It shows `—` when the machine has no node (every XJTU-SY machine) or the request fails.
- `MachineDetailPage` already remounts on `lastEvent.machine_id === id` (`MachineDetailPage.tsx:13-15`). Device events with a matching `machine_id` refresh the chip; `machine_id: null` matches nothing.

**Realtime (`realtime/LiveEventsProvider.tsx`):**
- `describe()` switches over the union:
  - `device_offline` → `toast.warning("Sensor node {device_id} ({machine_id}) went silent")` (or "disconnected" for `lwt`);
  - `device_online` → `toast.success("Sensor node {device_id} is back online")`.
- The success-toast branch at :74-78 becomes `alert_resolved || device_online`.
- Unknown types are dropped (decision 12).

**`NotificationBell` (`AppShell.tsx:60-91`):** sets the unseen dot for `device_offline` (it pages), not for `device_online` or `alert_resolved`.

**MSW (`frontend/src/test/server.ts`, `fixtures.ts`):**
- Defaults: `GET /api/telemetry/devices/:deviceId`, `GET /api/telemetry/incidents` (returns `structuredClone(deviceIncidents)`), and `POST /api/telemetry/incidents/:id/acknowledge`. The POST returns a cloned, acknowledged copy and never mutates fixtures, unlike the alert ack handler at :39-48.
- `telemetryDevices` (:283) rows gain `state`/`silent_for_s`/`expected_heartbeat_s`/`open_incident_id`. Make `simdev-02` `offline` with `open_incident_id: 1`.
- Both `telemetryStatus*` fixtures (:245, :264) gain `device_watchdog`.
- New `deviceIncidents` fixture: one open incident and one resolved, acknowledged incident.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `MAINTAINIQ_SWEEP_INTERVAL_S` | `0` | Background scheduler tick in seconds. `0`/unset ⇒ no background jobs (no silence alerting). Non-numeric or negative ⇒ logged, scheduler off. Recommended `5`. `docker-compose.yml` sets `${MAINTAINIQ_SWEEP_INTERVAL_S:-5}`, because the compose stack always runs MQTT ingest. |
| `DEVICE_SILENT_GRACE_S` | `30` | Seconds the MQTT connection must be continuously up before the watchdog may open or resolve incidents. Covers retained-status replay and reconnect backlog. Invalid/negative ⇒ logged, default used. |

The autouse conftest fixture (`tests/conftest.py:17-33`) is extended to also delete `MAINTAINIQ_SWEEP_*` and `DEVICE_SILENT_*`, so a developer's shell can never start the scheduler against the real `maintainiq.db` during tests.

## Test plan

### Backend (pytest)

`tests/telemetry/test_device_health.py` (new), with explicit `now=` throughout:
1. `online` when heard 5 s ago with hb 10.
2. `stale` at 15 s and at 29.9 s; `online` at 14.9 s (1.5× boundary).
3. `offline` at exactly 30 s (≥ 3× boundary) and beyond.
4. `offline` immediately when `online = 0` and `reported = True` (LWT), even if heard 0 s ago.
5. `never_reported` when `reported = False`, regardless of flag or age.
6. Heartbeat `None`, `0` and negative fall back to 10 s.
7. `last_message_at` newer than `last_seen_at` is used (KPI path).
8. Missing or unparseable `last_seen_at` with `reported = True` gives `offline`.
9. `Z`, `+00:00` and naive timestamps are equivalent.
10. Parametrised: `ingest.device_online(...) == is_heard(device_state(...))` across all the cases above.
11. `silent_for_s` value and `None` handling.

`tests/telemetry/test_watchdog.py` (new). Seed `device_status` as `tests/telemetry/test_telemetry_route.py` does, with `payload_json` set for reported nodes. Use a fake `notify` and a fixed `now`.
1. A silent node (online=1, heard 40 s ago, hb 10) gets a `silent` incident, one `device_offline` event and one notify call.
2. A node with `online = 0` and a payload gets an `lwt` incident on the first armed tick.
3. A stale node gets no incident; a never_reported node gets no incident.
4. A second tick creates no duplicate: still one open row, no second event, no second notify.
5. The partial unique index rejects a second open row inserted directly (IntegrityError), and the tick tolerates a race (pre-inserted open row ⇒ no event, no notify).
6. The node is heard again (`last_seen_at` = now): the incident is resolved, `resolved_at` = now, a `device_online` event is sent, and existing ack fields are preserved.
7. A later silence opens a new incident with a new id.
8. Not armed (`ingest_connected_for` returns `None`): no rows, no events and no resolve of an existing open incident, even with offline nodes.
9. Grace: connected for 10 s with grace 30 does nothing; at 30 s it opens.
10. Flap guard: an incident opened 5 min after a previous one opens and broadcasts but is not notified; after 16 min it is notified.
11. A raising `notify` leaves the incident committed and still returns the event.
12. On a pre-v3 DB (v2 stamped, no `device_incidents`), the first armed tick migrates via `ensure_telemetry_schema` and works. With the table absent and migration impossible (v0 without `device_status`), it returns `[]` and does not raise.
13. `machine_id` is copied, including NULL.

`tests/background/test_scheduler.py` (new):
1. `SchedulerSettings.from_env`: unset gives 0 (disabled); `"5"` gives 5.0; `"abc"` and `"-1"` raise `ValueError`.
2. `run_once` gives each job a fresh connection from the factory and closes it, even when the job raises.
3. Returned events are passed to the injected async `broadcast` in order.
4. A raising job is logged and the other jobs still run; a raising broadcast is logged, not raised.
5. With `start()` and a 0.05 s interval, the job runs repeatedly; `stop()` cancels, awaits, and is idempotent.
6. No tick runs before the first interval elapses.
7. Lifespan: with the `client` fixture and env scrubbed, `get_scheduler()` is `None`. With `MAINTAINIQ_SWEEP_INTERVAL_S=3600` and a monkeypatched `_scheduler_connection_factory`, the scheduler is set with job `device_silence` inside the `with` block, and `None` after exit.

`tests/telemetry/test_mqtt_service.py` (extend): `connected_for_s()` is `None` before start and after a disconnect, and grows after `_on_connect` (patched `time.monotonic`).

`tests/test_notifications.py` (extend):
1. `_recipients(conn)` default is unchanged (admin and supervisor); `roles=("admin",)` gives only admins.
2. `notify_device_incident`: subject per kind, body mentions the device and machine; rows have `alert_id IS NULL`, `device_incident_id` set; an SMTP failure gives a `failed` row and the return value counts only sent.
3. Existing `notify_alert` tests pass unmodified.

`tests/telemetry/test_telemetry_route.py` (extend):
1. `/devices` rows include `state`, `silent_for_s`, `expected_heartbeat_s` and `open_incident_id`; `online` equals `state ∈ {online, stale}`. The existing `test_devices_apply_online_rule` is updated to set `payload_json` on the LWT row `dev-c`, so it stays `offline`, not `never_reported`.
2. A `payload_json IS NULL` row reports `never_reported`, `online: false`.
3. The `?machine_id=` filter works.
4. `/devices/{id}`: 200 with `recent_incidents` newest-first (max 10); 404 for an unknown id.
5. `/incidents`: open first, then by `opened_at` desc; the `status=open` and `status=resolved` filters work; `status=bogus` gives 400; the `device_id` filter works; `limit=0` gives 422.
6. Acknowledge as operator, supervisor and admin: 200 with fields set and `acknowledged_by` equal to the user id. A second call is an unchanged no-op. A resolved incident can be acknowledged. Unknown id gives 404; anonymous gives 401.
7. Pre-v3 DB (drop `device_incidents`): `/incidents` gives `[]`, `/devices` has `open_incident_id` null, acknowledge gives 404.
8. `/status` has a `device_watchdog` block in both the disabled and the enabled (fake service) case.

`tests/storage/test_migrations.py`:
1. Replace the hard-coded `== 2` / `[1, 2]` at :209, :216, :218, :219 and :233 with `LATEST` / `list(range(1, LATEST + 1))`. The v1 tests then keep passing as further migrations are added.
2. `_v1_db()` also drops `device_incidents`.
3. New `_v2_db()`: steps 1 and 2, drop `device_incidents`, rebuild `notifications` in its pre-v3 shape (no `device_incident_id`), stamp versions 1 and 2.
4. v2→v3: `device_status`, `telemetry_messages`, `users` and `notifications` rows are preserved. `device_incidents` and its three indexes exist, and `notifications.device_incident_id` exists. Versions are `[1, 2, 3]`, and a re-run is a no-op.
5. A fresh DB has `device_incidents` and the column. The CHECKs reject a bad `kind`/`status`; the partial unique index allows several resolved rows but only one open row per device.
6. `ensure_telemetry_schema` on a v0 DB creates `device_incidents` (`tests/telemetry/test_ingest.py`).

`tests/kpi/test_system_kpis.py` (extend): `cloud_sync_health.devices_online` counts online and stale nodes, but not offline, LWT or never_reported nodes. Existing assertions stay unchanged, proving the shared rule did not change KPI output.

`tests/docs/*`: `device_incidents` is documented (enforced automatically).

### Frontend (vitest + RTL + MSW)

- `components/healthStyles.test.ts`: `deviceStateClasses`/`deviceStateLabel` for all four states plus unknown-state fallback.
- `components/DevicesTable.test.tsx` (new): state dot `aria-label` per state; incident cell shows the Acknowledge button or "Acknowledged"; empty state.
- `components/LiveTelemetryPanel.test.tsx`: existing assertions still pass after the extraction.
- `pages/DevicesPage.test.tsx` (new):
  1. Renders a row per device and the per-state summary counts.
  2. An open incident shows Acknowledge; clicking it POSTs to `/api/telemetry/incidents/1/acknowledge`, toasts and re-fetches (a spy on the handler).
  3. The incident tabs switch the `status` query.
  4. Banners for ingest disabled, watchdog disabled and watchdog not armed (`server.use` overrides).
  5. Re-fetches on a `device_offline` live event (mocked `useLiveEvents`).
  6. Polls every 5 s (fake timers).
- `realtime/LiveEventsProvider.test.tsx`: `device_offline` gives a warning toast with the device id; `device_online` gives a success toast; an unknown `type` gives no toast and leaves `lastEvent` unchanged; alert events still behave as before.
- `layout/AppShell` / `App.test.tsx`: operators see the Devices nav link and can open `/devices`. The bell dot appears on `device_offline` but not on `device_online`.
- `components/MachineDetail.test.tsx`: the Sensor node fact shows "Offline · simdev-02" for machine `sim-02`, and `—` when the response is `[]`.
- `npm run build` (type-checks the union and its exhaustive switches) and `npm run lint`.

## Docs to update

- `docs/DATA_MODEL.md`:
  - `### \`device_incidents\`` section;
  - `notifications` column `device_incident_id`;
  - ER (:14-27) gains `device_status ||--o{ device_incidents : "device_id (logical)"` and `device_incidents ||--o{ notifications : "paged via"`;
  - Storage layout (~:367-390) gains a migration 3 paragraph.
- `design/M6_LIVE_TELEMETRY.md`: §4 online rule now points at `device_health.device_state` (states and the 1.5× stale band); §8 documents the new fields and routes; new §12 "Device health / silence" summarising decisions 3-11.
- `README.md`: env table (:47-62) gains the two vars; the telemetry endpoints paragraph (:199-200) gains `/telemetry/incidents`, acknowledge and the Devices page.
- `.env.example`: a new "Background jobs / device health" block after the M6 MQTT block.
- `docker-compose.yml`: `MAINTAINIQ_SWEEP_INTERVAL_S=${MAINTAINIQ_SWEEP_INTERVAL_S:-5}` in the app `environment`.
- `TODO.md`: a checked item "Device health: node status page + silence incidents (design/2026-10-06-device-health-design.md)".
- `IMPLEMENTATION_PLAN.md`: a new `## M7 — Device health` after M6 (:37).
- `src/storage/migrations.py` docstring: a migration 3 paragraph.
- `PROJECT_CONTEXT.md` is not touched.

## Implementation notes (backend, 2026-10-07)

Where the backend build refined or corrected this design:

- **Watcher accessor.** `src/telemetry/watchdog.py` also has `set_watcher()` / `get_watcher()`, set and cleared by the lifespan next to `set_scheduler()`. The `/telemetry/status` route reads the watcher's `status()` through it. Without the accessor the route would have to reach into the scheduler's job list.
- **`DEVICE_SILENT_GRACE_S` parsing** lives in `watchdog.WatchdogSettings` (`from_env` raises `ValueError`; `from_env_or_default` logs and falls back to 30 s). The disabled `device_watchdog` block reports the configured grace too, so the UI banner can quote it.
- **`SchedulerSettings`** also rejects `nan`/`inf` and has an `enabled` property. `BackgroundScheduler(interval_s<=0)` raises: a disabled scheduler is simply not built. `stop()` cancels the loop; a job already running on its worker thread finishes there, but nothing it returns is broadcast.
- **`.env.example`** ships `MAINTAINIQ_SWEEP_INTERVAL_S=` (empty), not `0`. Compose interpolates `${MAINTAINIQ_SWEEP_INTERVAL_S:-5}` from `.env`, so a literal `0` copied from the example would silently switch silence alerting off in the compose stack. Empty means "off" for host-side processes and "5" in compose.
- **`GET /api/notifications` on a pre-v3 DB.** Migrations do not run at app start, so the route checks `PRAGMA table_info(notifications)` and selects `NULL AS device_incident_id` when the column is missing, rather than returning 500.
- **Incident list ordering** is done in SQL (`(status='open') DESC, opened_at DESC, id DESC`), like the alerts list. Every `opened_at` is written by the watchdog through `protocol.format_utc`, so string order is time order.
- **`silent_for_s` in the API** is measured from `device_status.last_seen_at`, the same input the route's state uses. Only the KPI path also passes `last_message_at`.
- **Route tests** for the new endpoints are in `tests/telemetry/test_device_health_routes.py`. `tests/telemetry/test_telemetry_route.py` only got the `payload_json` fixture fix and the new-key assertions.
- `tests/telemetry/test_ingest.py::test_first_message_migrates_a_pre_m6_db` now asserts the latest migration version instead of `2`.

## Implementation notes (frontend, 2026-10-07)

- **Unknown event types** are filtered by `KNOWN_EVENT_TYPES` (exported from `api/types.ts` next to `isDeviceEvent`) in a small `parseEvent()` in the provider. `describe()` has no `default`, so a new union member without a case fails `tsc -b`.
- **Devices page banner** shows only the first applicable reason (ingest off → broker disconnected → watchdog disabled → not armed); the later ones are moot while an earlier one holds.
- **Two incident fetches per poll:** `status=open` feeds the devices table's incident cell whatever history tab is selected; the tab feeds "Incident history". Both tables label their buttons `Acknowledge incident {id} ({device})`.
- **Helpers added:** `deviceStateTone()` (the `Badge` variant for a state) beside `deviceStateClasses`/`deviceStateLabel`, and `formatDuration()` in `lib/telemetryFormat.ts` for "silent for"/heartbeat.
- **`DevicesTable`** takes `showHealth` (extra columns) and a separate `linkMachines` (machine links need a router; the Ingestion panel's tests render without one).
- **Machine-detail chip** appends `+N` when a machine has more than one node.
- **MSW:** the default `GET /api/telemetry/devices` handler honours `?machine_id=`, so the XJTU machines (`m1`, `m2`) show `—` by default; `GET /api/telemetry/incidents` honours `?status=`.

## Risks

- **False alarms after outages on our side.** Gating plus the 30 s grace cover start and reconnect. A deep QoS-1 snapshot backlog does not delay status messages, which have their own worker lane (`mqtt_service` status queue), so live nodes are heard within one heartbeat of reconnect. Raise `DEVICE_SILENT_GRACE_S` if nodes use very long heartbeats; any node with hb > grace / 1.5 can briefly show `stale`.
- **Long-dead nodes page once after deployment.** On the first armed tick, every node already silent gets an incident and an email. This is correct but noisy on a fresh install; it is documented in the README. There is no decommission feature yet (non-goal).
- **Retained `reported_at` uses the device clock.** Skew can shift `last_seen_at` for retained replays. Live messages use receive time, so the error clears on the next heartbeat.
- **Rule boundary change in the UI.** Nodes between 1.5× and 3× hb now show `stale` where they used to show plain "online". KPI numbers are unchanged by construction (test-guarded).
- **Test fixtures without `payload_json`** now read as `never_reported`. Existing fixtures that model an LWT must set it (one known case: `test_devices_apply_online_rule`).
- **Mixed migration step** (`executescript` + ALTER) is not atomic. It is mitigated by idempotent DDL and the guarded ALTER.
- **Multiple app instances** (unsupported per README "One app instance") are still safe from duplicate incidents and pages, thanks to the partial unique index and email-after-insert.
- **Scheduler accidentally on in tests** would write to the real `maintainiq.db`. Mitigated four ways: default off, the conftest env scrub, the injectable `_scheduler_connection_factory`, and the first tick only after one interval.
- **`LiveEvent` union churn.** Every `lastEvent` consumer must narrow before touching `.alert`. `tsc -b` in `npm run build` enforces this, and unknown types are dropped at the provider.
