# Work Orders + Alert Paging Escalation — Design

Status: Approved (decisions below are final for implementation)
Date: 2026-10-07
Source: user requirement (verbatim) "Work orders + escalation: turn alerts into tracked repair jobs, and escalate unacknowledged alerts to higher roles."; `design/_integration_map.md` §3 (feature 1, migration 4) and §7 (cross-cutting checklist); builds on `design/2026-10-06-device-health-design.md` (scheduler, `_recipients(roles)`, `LiveEvent` union) and `design/2026-08-04-alert-acknowledge-design.md` (acknowledgement semantics).

## Problem

An alert today is a flag, not a job:

- **Nobody owns the repair.** Alerts open and auto-resolve (`src/alerts/live.py:49-102`), and anyone can acknowledge them (`src/api/routes/alerts.py:45-76`). There is no record of who is fixing the machine, whether work has started, or when it finished. The only link from an alert to a repair is a free-standing `maintenance_records.alert_id` that someone has to remember to set on the Machine Detail form.
- **An ignored alert stays ignored.** Admins and supervisors get one email when an alert opens or its severity rises (`src/prediction/pipeline.py:27,91-96`, `src/notifications/dispatch.py:58-86`). If nobody acknowledges it, nothing else happens.
- **The demo path duplicates the fan-out.** `src/api/routes/demo.py:36-61` and `:88-106` call `apply_reading`, `notify_alert` and `manager.broadcast` themselves instead of going through `pipeline.handle_prediction`, so any change to the fan-out has to be made twice.

This feature adds work orders (a tracked repair job with an owner, a status and an audit trail) and a paging ladder that re-pages higher roles while an alert stays unacknowledged.

## Goal

1. Any signed-in user can turn an alert into a work order with one click. Admins and supervisors can also create work orders that are not tied to an alert.
2. A work order moves `open → assigned → in_progress → done`. It can be cancelled from any non-terminal state. Every change is written to an append-only event trail.
3. Completing a work order writes a corrective `maintenance_records` row and links it, so the maintenance KPIs keep a single source.
4. While an open alert stays unacknowledged and has no active work order, it is re-paged: supervisors after `ESCALATION_L1_MINUTES`, then admins after `ESCALATION_L2_MINUTES`. Each level pages once.
5. Dashboards hear about all of this in realtime: `work_order_created`, `work_order_updated` and `alert_paged`.
6. Demo-injected alerts use the same fan-out as model predictions.

## Non-goals

- **Auto-creating a work order when an alert opens.** Alerts auto-resolve as soon as a healthy reading arrives (`live.py:93-100`). Auto-created orders would mostly be noise for transient episodes. A human decides that a repair is needed.
- **Resolving the alert when its work order completes.** `alerts.status` belongs to the model-driven state machine (integration map §1). If a person resolved an alert while the machine still read abnormal, the next reading would just open a new alert. Completion is recorded on the work order and in `maintenance_records` only.
- **Per-severity or per-machine paging thresholds, and a configurable ladder.** There is one ladder, set by two env vars.
- **Operator push or SMS paging.** Feature 5 adds a push tier. Operators are still not emailed (`dispatch.py:7-10`).
- **Unassigning.** Reassign instead.
- **Due-date reminders or SLA breach paging for work orders.** `due_at` is stored and shown only.
- **Work-order fields in reports** (`src/reports/generators.py`) and work-order analytics charts.
- **Resetting the RUL predictor state on completion** (`DELETE /api/predictions/rul/{id}/state`). Left as an explicit manual action.
- **Paging escalation for device incidents.** Device-health design non-goal; unchanged.
- **Republishing work-order or paging events to devices over MQTT.**

## Decisions

1. **Two new tables, `work_orders` and `work_order_events`, plus two columns on `alerts`.** Work orders are their own entity rather than more alert statuses. `alerts.status` stays `open|resolved`, because KPIs and reports count those values (`kpi/calculations.py:97,158,165,725`, `reports/generators.py:232`). Paging state lives in `alerts.page_level INTEGER NOT NULL DEFAULT 0` and `alerts.last_paged_at TEXT` (integration map §3). This is migration 4.

2. **Status machine (strict).**

   | Action | From | To | Who |
   |---|---|---|---|
   | create (from alert) | — | `open`, or `assigned` if `assigned_to` given | any role; `assigned_to` only by admin/supervisor |
   | create (free-standing) | — | `open`, or `assigned` | admin, supervisor |
   | assign / reassign | `open`, `assigned`, `in_progress` | `assigned` from `open`; otherwise unchanged | admin, supervisor |
   | start | `assigned` | `in_progress` | the assignee, admin, supervisor |
   | complete | `in_progress` | `done` | the assignee, admin, supervisor |
   | cancel | `open`, `assigned`, `in_progress` | `cancelled` | admin, supervisor |
   | edit (title, description, priority, due_at) | any non-terminal | unchanged | admin, supervisor |

   - `done` and `cancelled` are terminal.
   - **Errors:** any action outside the table gives **409** `invalid transition: {status} -> {action}`. A role or assignee mismatch gives **403**. `open → in_progress` is invalid: someone must be assigned first, and an admin or supervisor can assign themselves.
   - **Why explicit action endpoints, not one `/transition`:** the map suggested a single endpoint. Separate endpoints (`/assign`, `/start`, `/complete`, `/cancel`) give each action its own typed body (assignee, completion notes, cancel reason) and make the RBAC table above one `require_role` or assignee check per route.

3. **One active work order per alert.** This is enforced in the DB by a partial unique index on `work_orders(alert_id) WHERE alert_id IS NOT NULL AND status IN ('open','assigned','in_progress')`.
   - The service turns the resulting `sqlite3.IntegrityError` into **409** `alert {id} already has an active work order: #{wo}`. That makes duplicate creation race-free across threads and instances.
   - Once an order is `done` or `cancelled`, a new one may be raised for the same alert.

4. **Creating a work order acknowledges the alert.**
   - **One transaction under `live._TRANSITION_LOCK`:** read the alert (404 if missing); INSERT the work order and its `created` event; if `acknowledged_at IS NULL`, `UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ? AND acknowledged_at IS NULL`; then one commit.
   - **Duplicate insert:** if the INSERT hits the unique index, nothing is acknowledged.
   - **Resolved alerts** can get a work order. A resolved episode may still mean a damaged bearing, and acknowledging resolved alerts is already allowed (ack design, decision 3).
   - **Broadcasts after commit:** `alert_acknowledged` (only if this call set it) and then `work_order_created`.

5. **Shared acknowledge helper in `src/alerts/live.py`.**
   - `acknowledge_alert(conn, alert_id, user_id, *, now=None) -> tuple[dict | None, bool]` returns `(alert_row_dict, changed)`, or `(None, False)` for an unknown id. It takes `_TRANSITION_LOCK` and commits. A lock-free `_acknowledge_locked(conn, alert_id, user_id, now)` does the conditional UPDATE without committing.
   - The work-order service calls `_acknowledge_locked` while it already holds the lock. `threading.Lock` is not re-entrant, so calling the public wrapper there would deadlock.
   - `POST /api/alerts/{id}/acknowledge` (`alerts.py:45-76`) is rewritten to call `acknowledge_alert`. Its response and idempotence don't change. Its read-then-write becomes a conditional UPDATE under the lock, which fixes the last-writer-wins race on `acknowledged_by`.

6. **Completion writes maintenance through `log_maintenance`.**
   - **Lock and check:** under a process-wide `work_orders.service._LOCK`, re-read the order and require `status = 'in_progress'`.
   - **The maintenance record:** call `src.maintenance.records.log_maintenance(conn, machine_id, performed_at, description, technician, alert_id=wo.alert_id, type=maintenance_type)` (`records.py:49-82`), with:
     - `technician`: the assignee's `users.name`, else the completer's.
     - `performed_at`: from the request, else now.
     - `description`: `"Work order #{id}: {title}"`, plus `" — {notes}"` when notes are given.
     - `type`: `'corrective'` unless the request says `'preventive'`.
   - **Then:** `UPDATE work_orders SET status='done', completed_at, maintenance_record_id, notes, updated_at WHERE id=? AND status='in_progress'`, insert a `completed` event, and commit.
   - **Not atomic:** `log_maintenance` commits by itself (`records.py:72`), so the two writes are separate. The process lock means no concurrent completion can slip between them (the same one-process argument as `live.py:7-13`). The remaining risk, an orphan maintenance row if the second write fails, is listed under Risks.
   - **Errors:** `MaintenanceError` maps to 400.

7. **Paging ladder** (`src/alerts/paging.py`, scheduler job `alert_paging`).
   - **Levels:**
     - **Level 0** is today's behaviour, unchanged: on `alert_created` and `alert_escalated`, `notify_alert` emails admins and supervisors.
     - **Level 1:** once an alert has been open and unacknowledged for `ESCALATION_L1_MINUTES` (default 15), re-page `('supervisor',)`.
     - **Level 2:** after `ESCALATION_L2_MINUTES` (default 30), page `('admin',)`. That is the top level, and nothing is paged after it.
   - **Age is measured from `alerts.created_at`**, which is always the wall clock (`live.py:51`), **not `opened_at`.** `opened_at` is the *reading* timestamp. Replayed XJTU-SY data carries 2003 dates (`tests/conftest.py:110-113`, `frontend/src/test/fixtures.ts:79`), so measuring from `opened_at` would page every replayed alert instantly. Timestamps are parsed in Python with `src.telemetry.device_health.parse_iso`, which accepts both the `Z` and `+00:00` suffixes and treats naive values as UTC. String comparison is never used.
   - **Who is eligible:** alerts with `status = 'open' AND acknowledged_at IS NULL AND page_level < 2` and no active work order (`NOT EXISTS`). Acknowledging the alert or creating a work order (which acknowledges it) therefore stops paging, and so does auto-resolution.
   - **Several levels crossed at once** (app was down, or a fresh deploy with old open alerts): the alert jumps straight to the highest level reached. One email goes to the deduplicated union of the skipped levels' roles. One `alert_paged` event is sent, carrying the final level.
   - **No double paging:**
     - Under `live._TRANSITION_LOCK`, a compare-and-set `UPDATE alerts SET page_level = ?, last_paged_at = ? WHERE id = ? AND page_level = ? AND status = 'open' AND acknowledged_at IS NULL AND NOT EXISTS (active work order)` is committed.
     - The email and event happen only if `rowcount == 1`.
     - Email is sent after the commit and outside the lock (SMTP is slow), inside try/except with `logger.exception`. It never rolls back the level.
     - **Email is never sent inside the tick** (fix, 2026-10-07). The app passes `deliver=NotificationWorker.submit` (`src/background/delivery.py`): the tick only does the compare-and-set UPDATEs and returns its `alert_paged` events at once, and each page (email + push) is sent on one delivery thread with its own connection. Sending inline held every event until the sweep ended, delayed new pages behind a backlog (about 8 s per page with an unreachable SMTP) and kept the device watchdog from ticking. Without `deliver` (unit tests) pages are sent on the tick's connection after the whole sweep has committed.
   - **Alerts that predate the ladder are never paged by it** (fix, 2026-10-07). Migration 4 sets `page_level = 2` on every open alert it finds (`last_paged_at` stays NULL), and the legacy batch seed path (`storage.db.insert_alerts`) writes its historical alerts the same way. Otherwise the first tick after an upgrade paged admins and supervisors at level 2 for every weeks-old dataset/demo alert. The UI shows the "Paged L{n}" badge only when `last_paged_at` is set (`wasLadderPaged`), so these alerts carry no badge.
   - **A severity escalation does not reset the ladder.** It still sends its level-0 email, as today. `page_level` is never lowered; it stays as history after acknowledgement.

8. **Level-0 pages are stamped too.** The shared fan-out (decision 9) sets `alerts.last_paged_at = now` after it calls `notify_alert` for `alert_created`/`alert_escalated`. It is a write-only UPDATE in try/except, so a failure never undoes the alert. "Last paged" in the UI then covers every level, and `page_level` stays 0 until the ladder fires.

9. **Demo goes through the shared fan-out** (map §3 and §7 asked for this "if feasible"; it is).
   - **New function** in `src/prediction/pipeline.py`: `fan_out(conn, applied, *, at, prediction=None, broadcast=None) -> dict`. `applied` is `apply_reading`'s return value. It holds today's `pipeline.py:86-115` logic: level-0 email via the module-level `notify_alert(conn, alert)` (the 2-arg call keeps the `tests/prediction/test_pipeline.py:41-44` monkeypatch valid), the `last_paged_at` stamp, and the broadcast.
   - **`handle_prediction`** keeps its signature and becomes `apply_reading` + `fan_out`.
   - **`demo.py` changes:**
     - Both routes become sync `def`, so FastAPI runs them in its threadpool.
     - They call `apply_reading` with the demo's own `probable_cause` and then `pipeline.fan_out(conn, applied, at=now)`. The default broadcast is `manager.broadcast_threadsafe`, exactly like replay.
     - `reset-machine` keeps sending no email, because `alert_resolved` is not a paging event.
   - **Two demo behaviours change** and are tested:
     - an email failure is now logged rather than raised;
     - the broadcast is fire-and-forget on the bound loop.

10. **Realtime events** are all new types. None goes into `protocol.ALERT_EVENT_TYPES` (`src/telemetry/protocol.py:65`), so `is_alert_event` (:659) keeps them off MQTT. `alert_escalated` keeps its meaning (a severity increase).
    - `work_order_created`: `{type, machine_id, work_order, at}`
    - `work_order_updated`: `{type, machine_id, work_order, change, at}`, where `change` ∈ `assigned | started | completed | cancelled | edited`
    - `alert_paged`: `{type, machine_id, alert, page_level, at}`

11. **Schema upgrade happens lazily on the first request.**
    - **Why:** the new `alerts` columns are read by every alert SELECT. Migrations do not run at app start (map §1), so a v3 file served by new code would return 500 on `GET /api/alerts` until something migrated it. Per-reader `PRAGMA` guards (the `notifications.py:35-40` pattern) would have to be repeated in five places, including the live alert path.
    - **Mechanism:** a new `migrations.ensure_current_schema(conn) -> int`:
      - If `current_version(conn) >= 1` and `< LATEST`, it runs `run_migrations` under a module `threading.Lock` with a double check.
      - It memoises the DB file path (`PRAGMA database_list`) once the file is current.
      - A v0 or legacy file is never touched. Migration 1 drops dataset tables, and that stays an explicit backfill decision, the same rule as `ingest.ensure_telemetry_schema` (`src/telemetry/ingest.py:165-188`).
    - **Callers:**
      - `src/api/deps.py get_db` calls it once per process per file, logging and continuing on failure. Every authenticated route, the login route and replay start (whose `require_role` resolves `get_db`) open a current-schema DB.
      - The `alert_paging` job calls it on its first tick, just as the watchdog's `_ensure_schema` does.
      - MQTT ingest already migrates through `ensure_telemetry_schema`.
    - **Why not the lifespan:** tests override `get_db`, so this can never touch the developer's `maintainiq.db`. The lifespan alternative in the map would need yet another monkeypatched factory in every `TestClient(app)` test.
    - **What stays the same:** the existing `notifications` PRAGMA guard is kept as is.

12. **Assignee list for supervisors.** `GET /api/users` is admin-only (`src/api/routes/users.py:13`), so supervisors could not fill the assign dropdown. A new `GET /api/work-orders/assignees` (admin, supervisor) returns active users as `{id, name, role}`, with no email, ordered by role then name.

13. **Priority defaults from alert severity** when the order is created from an alert: `low → low`, `medium → medium`, `high → high`. The default title is the alert's `message`, else `"{machine_id}: {health_state} alert"`. Free-standing orders default to `medium`.

14. **KPIs.** `_maintenance_for_machine` (`kpi/calculations.py:149-207`) gains:
    - `open_work_order_count`: active orders for the machine;
    - `avg_work_order_completion_hours`: mean `completed_at − created_at` over `done` orders, else `None`.

    `summary()` (`:717-736`) gains a fleet-wide `open_work_order_count`. All of them are guarded by `_table_exists(conn, "work_orders")` (`:672`), which returns 0 or `None` when the table is missing, so report generation on an old file still works. `due_for_inspection` is unchanged.

15. **Migration 4** (map §1 numbering) is one mixed step: `executescript(WORK_ORDER_SCHEMA)` followed by two guarded `ALTER TABLE alerts ADD COLUMN` calls. Like migration 3, this is acceptable: `executescript` COMMITs first, and the ALTERs are idempotent (`try/except sqlite3.OperationalError`), so a crash between them re-runs cleanly.
    - **No index on the new `alerts` columns.** On a legacy DB, migration 1 runs `executescript(SCHEMA)` against a pre-existing `alerts` table that lacks them (`migrations.py:51`), so an index in SCHEMA would fail there. The sweep scans open alerts only, which is a small set.

## Data model

### `src/storage/db.py`

`alerts` CREATE (`db.py:134-148`) gains two columns after `acknowledged_by`:

```sql
    page_level INTEGER NOT NULL DEFAULT 0,
    last_paged_at TEXT
```

The DEFAULT keeps the conftest INSERT (`tests/conftest.py:104-115`) and `insert_alerts` (`db.py:375-386`) valid.

New constant, folded in after `DEVICE_HEALTH_SCHEMA` (`db.py:266` becomes `SCHEMA = SCHEMA + TELEMETRY_SCHEMA + DEVICE_HEALTH_SCHEMA + WORK_ORDER_SCHEMA`):

```python
# Work orders (design/2026-10-07-work-orders-escalation-design.md): a tracked
# repair job, optionally raised from an alert, with an append-only event trail.
# At most one active (open/assigned/in_progress) order per alert, enforced by a
# partial unique index so duplicate creation is race-free. Completing an order
# writes a maintenance_records row (maintenance_record_id) so maintenance KPIs
# stay single-sourced. Applied on its own by migration 4 and folded into
# SCHEMA for fresh installs.
WORK_ORDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_orders (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id               INTEGER REFERENCES alerts(id),
    machine_id             TEXT NOT NULL REFERENCES machines(machine_id),
    status                 TEXT NOT NULL DEFAULT 'open'
                           CHECK(status IN ('open','assigned','in_progress','done','cancelled')),
    priority               TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('low','medium','high')),
    title                  TEXT NOT NULL,
    description            TEXT,
    assigned_to            INTEGER,
    created_by             INTEGER,
    due_at                 TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    started_at             TEXT,
    completed_at           TEXT,
    cancelled_at           TEXT,
    maintenance_record_id  INTEGER REFERENCES maintenance_records(id),
    notes                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_work_orders_status ON work_orders(status, machine_id);
CREATE INDEX IF NOT EXISTS idx_work_orders_assignee ON work_orders(assigned_to, status);
-- One active order per alert; finished/cancelled orders leave the index.
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_orders_one_active_per_alert
    ON work_orders(alert_id)
    WHERE alert_id IS NOT NULL AND status IN ('open','assigned','in_progress');

CREATE TABLE IF NOT EXISTS work_order_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id  INTEGER NOT NULL REFERENCES work_orders(id),
    event          TEXT NOT NULL
                   CHECK(event IN ('created','edited','assigned','started','completed','cancelled')),
    from_status    TEXT,
    to_status      TEXT,
    user_id        INTEGER,
    assigned_to    INTEGER,
    note           TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_order_events_order ON work_order_events(work_order_id, id);
"""
```

Column semantics:
- **`assigned_to`, `created_by`, `work_order_events.user_id` and `.assigned_to`** are `users.id` values with no FK, matching `alerts.acknowledged_by`.
- **`started_at`, `completed_at` and `cancelled_at`** are each set once, by their own transition. `completed_at` is set only for `done`.
- **`updated_at`** changes on every write. List order is `updated_at DESC, id DESC`, which is safe as a string order because one writer (`service._now()`, `datetime.now(timezone.utc).isoformat()`) formats every value.
- **Reassignment** is an `assigned` event whose `from_status` equals `to_status`. `assigned_to` holds the new assignee.
- **`created`** is the first event of every order. It carries `note = "from alert #{id}"` for alert-raised orders.

### `src/storage/migrations.py`

```python
def _migration_004_work_orders(conn: sqlite3.Connection) -> None:
    # Additive: two new tables plus two alerts columns. executescript()
    # COMMITs first; the guarded ALTERs after it are idempotent, so a crash
    # between them re-runs cleanly (same shape as migration 3).
    conn.executescript(WORK_ORDER_SCHEMA)
    for column in ("page_level INTEGER NOT NULL DEFAULT 0", "last_paged_at TEXT"):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # fresh DB: SCHEMA already has the column

MIGRATIONS = [..., (3, _migration_003_device_health), (4, _migration_004_work_orders)]

def ensure_current_schema(conn) -> int: ...   # decision 11
```

The module docstring (`migrations.py:1-27`) gains a "Migration 4" paragraph. `MIGRATIONS` is at `migrations.py:91-96`.

## Services

### `src/work_orders/service.py` (new package `src/work_orders/`)

```python
ACTIVE_STATUSES = ("open", "assigned", "in_progress")
TERMINAL_STATUSES = ("done", "cancelled")
SUPERVISING_ROLES = ("admin", "supervisor")
PRIORITY_BY_SEVERITY = {"low": "low", "medium": "medium", "high": "high"}

class WorkOrderError(ValueError): ...          # base; 400 (validation)
class WorkOrderNotFound(WorkOrderError): ...   # 404
class WorkOrderConflict(WorkOrderError): ...   # 409 (bad transition, duplicate active order, edit terminal)
class WorkOrderForbidden(WorkOrderError): ...  # 403 (role / not the assignee)

def list_work_orders(conn, *, status=None, machine_id=None, assigned_to=None,
                     alert_id=None, limit=200) -> list[dict]
def get_work_order(conn, wo_id, *, with_events=False) -> dict      # WorkOrderNotFound
def list_assignees(conn) -> list[dict]
def create_from_alert(conn, alert_id, actor, *, title=None, description=None, priority=None,
                      assigned_to=None, due_at=None, now=None) -> tuple[dict, dict | None]
    # -> (work_order, acknowledged_alert_or_None)
def create(conn, actor, *, machine_id, title, description=None, priority="medium",
           assigned_to=None, due_at=None, now=None) -> dict
def edit(conn, wo_id, actor, *, title=None, description=_UNSET, priority=None, due_at=_UNSET, now=None) -> tuple[dict, bool]
    # -> (work_order, changed). title/priority None = not given; an explicit None clears description/due_at.
    # Fields equal to their current value are dropped; a no-op edit writes no event, keeps updated_at,
    # and the PATCH route skips the work_order_updated broadcast.
def assign(conn, wo_id, actor, assigned_to, *, note=None, now=None) -> dict
def start(conn, wo_id, actor, *, now=None) -> dict
def complete(conn, wo_id, actor, *, notes=None, performed_at=None,
             maintenance_type="corrective", now=None) -> dict
def cancel(conn, wo_id, actor, *, reason=None, now=None) -> dict
```

- `actor` is the `get_current_user` dict (`id`, `role`, `name`). Role and assignee checks live in the service, so the RBAC table is unit-tested without HTTP.
- Every mutator holds `_LOCK`, re-reads the row, validates the edge, and does a conditional UPDATE (`WHERE id = ? AND status = ?`). It inserts exactly one `work_order_events` row and commits once. It returns `get_work_order(...)`, which includes `assigned_to_name` and `created_by_name` via LEFT JOIN `users`.
- **Validation (400):**
  - `machine_id` must exist (`unknown machine_id: '{id}'`);
  - `assigned_to` must be an active user (`unknown or inactive user: {id}`);
  - `due_at`/`performed_at` must parse as ISO-8601 (normalised with the `records._parse_timestamp` rule);
  - `title` must be 1–200 characters after strip.
- `create_from_alert` takes `live._TRANSITION_LOCK` (decision 4), not `_LOCK`. It never takes both locks, so lock order cannot deadlock.

### `src/alerts/paging.py` (new)

```python
JOB_NAME = "alert_paging"
L1_ENV, L2_ENV = "ESCALATION_L1_MINUTES", "ESCALATION_L2_MINUTES"
DEFAULT_L1_MINUTES, DEFAULT_L2_MINUTES = 15.0, 30.0
MAX_PAGE_LEVEL = 2
LADDER = {1: ("supervisor",), 2: ("admin",)}

@dataclass(frozen=True)
class PagingPolicy:
    l1_minutes: float = DEFAULT_L1_MINUTES
    l2_minutes: float = DEFAULT_L2_MINUTES
    def level_for(self, age: timedelta) -> int        # 0, 1 or 2
    def roles_between(self, old: int, new: int) -> tuple[str, ...]   # union of LADDER[old+1..new]
    @classmethod
    def from_env(cls) -> "PagingPolicy"               # ValueError: non-numeric, <= 0, non-finite, L2 <= L1
    @classmethod
    def from_env_or_default(cls) -> "PagingPolicy"    # logs and returns defaults

class AlertPager:
    def __init__(self, *, policy: PagingPolicy, notify=dispatch.notify_alert): ...
    def tick(self, conn, now: datetime) -> list[dict]  # scheduler job signature
    def status(self) -> dict  # {"l1_minutes", "l2_minutes", "last_tick_at", "last_error"}
```

`tick` algorithm:

1. On the first tick, call `ensure_current_schema(conn)`. If `alerts.page_level` is still missing (PRAGMA), return `[]`.
2. `SELECT id, created_at, page_level FROM alerts a WHERE status = 'open' AND acknowledged_at IS NULL AND page_level < 2 AND NOT EXISTS (SELECT 1 FROM work_orders w WHERE w.alert_id = a.id AND w.status IN ('open','assigned','in_progress'))`.
3. For each candidate:
   - Compute `age = now − parse_iso(created_at)`. Skip it on an unparseable timestamp (logged at debug).
   - Compute `target = policy.level_for(age)`. Skip it if `target <= page_level`.
4. Under `live._TRANSITION_LOCK`: run the CAS UPDATE (decision 7) and commit. If `rowcount != 1`, skip. Otherwise re-read the full alert row (`live._ALERT_COLUMNS`).
5. Outside the lock, in try/except: `notify(conn, alert, roles=policy.roles_between(old, target), page_level=target)`.
6. Append `{"type": "alert_paged", "machine_id", "alert", "page_level": target, "at": protocol.format_utc(now)}`.
7. Return the events. The scheduler broadcasts them (`scheduler.py:124-139`).

Accessors `set_pager()` / `get_pager()` are like `watchdog.set_watcher`.

### `src/notifications/dispatch.py`

- `notify_alert(conn, alert, *, roles=DEFAULT_PAGED_ROLES, page_level=0) -> int`. The defaults keep today's behaviour and `tests/test_notifications.py:29-88` byte-identical.
- `_compose(alert, page_level=0)`:
  - **Subject:** at level ≥ 1 the subject gains the prefix `"[Unacknowledged — page {n}] "`.
  - **Body:** gets `"Unacknowledged since {opened_at}. Paging level {n} of 2. Acknowledge it or create a work order on the Alerts page.\n"`.
  - **Level 0:** unchanged.
- No `notifications` schema change. The paging level is visible in the subject, which keeps migration 4 off the notifications table.

### `src/alerts/live.py`

- `_ALERT_COLUMNS` (`live.py:22-25`) gains `acknowledged_at, acknowledged_by, page_level, last_paged_at`. The create dict (`:66-78`) gains `acknowledged_at: None, acknowledged_by: None, page_level: 0, last_paged_at: None`. Broadcast alerts then match the API `Alert` shape (map §7).
- New `acknowledge_alert` / `_acknowledge_locked` (decision 5).

### `src/prediction/pipeline.py` and `src/api/routes/demo.py`

`fan_out(...)` is extracted (decision 9). `_PAGING_EVENTS` (`pipeline.py:27`) is unchanged.

## API

All new routes require a session. `work_orders.router` is added to the `get_current_user` tuple (`src/api/app.py:165`), so anonymous requests get 401.

| Method & path | Roles | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/work-orders?status=&machine_id=&assigned_to=&alert_id=&limit=` | any | `status` ∈ the five statuses or `active` (= open/assigned/in_progress); omitted = all. `limit` 1–2000 (default 200) | `list[WorkOrder]`, `updated_at DESC, id DESC` | 400 `invalid status: {s}`; 422 bad `limit` |
| `GET /api/work-orders/assignees` | admin, supervisor | — | `list[Assignee]` | 403 |
| `GET /api/work-orders/{id}` | any | — | `WorkOrderDetail` | 404 `unknown work order: {id}` |
| `POST /api/work-orders` | admin, supervisor | `WorkOrderCreate` | 201 `WorkOrder` | 400, 403 |
| `PATCH /api/work-orders/{id}` | admin, supervisor | `WorkOrderUpdate` (any subset) | `WorkOrder` | 400, 403, 404, 409 (terminal) |
| `POST /api/work-orders/{id}/assign` | admin, supervisor | `{assigned_to: int, note?: str}` | `WorkOrder` | 400, 403, 404, 409 |
| `POST /api/work-orders/{id}/start` | assignee, admin, supervisor | — | `WorkOrder` | 403, 404, 409 |
| `POST /api/work-orders/{id}/complete` | assignee, admin, supervisor | `WorkOrderComplete` | `WorkOrder` (with `maintenance_record_id`) | 400, 403, 404, 409 |
| `POST /api/work-orders/{id}/cancel` | admin, supervisor | `{reason?: str}` | `WorkOrder` | 403, 404, 409 |
| `POST /api/alerts/{id}/work-order` | any (`assigned_to` admin/supervisor only, else 403) | `WorkOrderFromAlert` (body optional) | 201 `WorkOrder` | 403, 404 `unknown alert: {id}`, 409 duplicate |

- `/assignees` is declared before `/{id}` in the router.
- The routes are `async def`, call the sync service, and `await manager.broadcast(...)` after commit, matching the ack route (`alerts.py:45-76`). A broadcast failure is logged and does not fail the request.
- The service exceptions map to status codes in one helper in the router module.

`src/api/schemas.py`:

```python
WorkOrderStatus = Literal["open", "assigned", "in_progress", "done", "cancelled"]
Priority = Literal["low", "medium", "high"]

class Alert(BaseModel):                     # existing, schemas.py:36-48, gains:
    page_level: int = 0
    last_paged_at: Optional[str] = None
    active_work_order_id: Optional[int] = None   # list/detail routes only; None in broadcasts

class WorkOrder(BaseModel):
    id: int; alert_id: Optional[int]; machine_id: str; status: WorkOrderStatus; priority: Priority
    title: str; description: Optional[str]; assigned_to: Optional[int]; assigned_to_name: Optional[str]
    created_by: Optional[int]; created_by_name: Optional[str]; due_at: Optional[str]
    created_at: str; updated_at: str; started_at: Optional[str]; completed_at: Optional[str]
    cancelled_at: Optional[str]; maintenance_record_id: Optional[int]; notes: Optional[str]

class WorkOrderEvent(BaseModel):
    id: int; work_order_id: int; event: Literal["created","edited","assigned","started","completed","cancelled"]
    from_status: Optional[str]; to_status: Optional[str]; user_id: Optional[int]; user_name: Optional[str]
    assigned_to: Optional[int]; assigned_to_name: Optional[str]; note: Optional[str]; created_at: str

class WorkOrderDetail(WorkOrder):
    events: list[WorkOrderEvent]            # oldest first
    alert: Optional[Alert] = None           # the linked alert, if any

class WorkOrderCreate(BaseModel):
    machine_id: str = Field(min_length=1); title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000); priority: Priority = "medium"
    assigned_to: Optional[int] = None; due_at: Optional[str] = None

class WorkOrderFromAlert(BaseModel):        # all optional; defaults per decision 13
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000); priority: Optional[Priority] = None
    assigned_to: Optional[int] = None; due_at: Optional[str] = None

class WorkOrderUpdate(BaseModel):          # title/description/priority/due_at, all optional
class WorkOrderAssign(BaseModel):  assigned_to: int; note: Optional[str] = Field(None, max_length=500)
class WorkOrderComplete(BaseModel):
    notes: Optional[str] = Field(None, max_length=2000); performed_at: Optional[str] = None
    maintenance_type: Literal["preventive", "corrective"] = "corrective"
class WorkOrderCancel(BaseModel):  reason: Optional[str] = Field(None, max_length=500)
class Assignee(BaseModel):  id: int; name: str; role: Role

class MaintenanceSummary(BaseModel):        # gains
    open_work_order_count: int = 0
    avg_work_order_completion_hours: Optional[float] = None
class KpiSummary(BaseModel):               # gains
    open_work_order_count: int = 0
```

**Alert SELECTs** (map §7). The alert list (`alerts.py:33-41`) and machine detail (`machines.py:42-49`) add `page_level, last_paged_at` and:

```sql
(SELECT w.id FROM work_orders w WHERE w.alert_id = alerts.id
   AND w.status IN ('open','assigned','in_progress')) AS active_work_order_id
```

The ack route's `SELECT *` (`alerts.py:51`) is replaced by `live.acknowledge_alert`.

## Realtime events

```json
{"type": "work_order_created", "machine_id": "m1",
 "work_order": {"id": 7, "alert_id": 2, "machine_id": "m1", "status": "open", "priority": "high",
                "title": "m1 critical", "assigned_to": null, "assigned_to_name": null, "...": "WorkOrder"},
 "at": "2026-10-07T12:00:00+00:00"}
{"type": "work_order_updated", "machine_id": "m1", "work_order": {"...": "WorkOrder"},
 "change": "assigned", "at": "..."}
{"type": "alert_paged", "machine_id": "m1",
 "alert": {"id": 2, "page_level": 1, "last_paged_at": "...", "...": "Alert"},
 "page_level": 1, "at": "2026-10-07T12:15:05Z"}
```

- `work_order_*` events are sent from the route handlers (`await manager.broadcast`). `alert_paged` is returned by the paging job and broadcast by the scheduler.
- None of these is republished to devices.
- Creating from an alert that was unacknowledged first broadcasts the usual `alert_acknowledged`.

## Notification behaviour

| Trigger | Who is emailed | Subject |
|---|---|---|
| `alert_created` / `alert_escalated` (level 0, unchanged) | active admins + supervisors | `MaintainIQ CRITICAL: m1 needs attention` (severity templates, `dispatch.py:21-25`) |
| level 1 (unacked ≥ L1 min) | active supervisors | `[Unacknowledged — page 1] MaintainIQ CRITICAL: m1 needs attention` |
| level 2 (unacked ≥ L2 min) | active admins | `[Unacknowledged — page 2] …` |
| levels 1+2 crossed in one tick | union (supervisors + admins), each once | `[Unacknowledged — page 2] …` |
| Work-order create/assign/start/complete/cancel | nobody (realtime only) | — |

- Every page writes one `notifications` row per recipient (`alert_id` set), `sent`/`failed` as today. A failed or raising `notify` never rolls back `page_level`.
- **Work-order assignment is not emailed.** The assignee may be an operator, and operators are deliberately not emailed (`dispatch.py:7-10`). Feature 5's push tier is where an "assigned to you" notification belongs. Until then, the realtime toast and the Work Orders "Mine" filter are the channel.

## Frontend UX

**Types (`frontend/src/api/types.ts`):**
- `Alert` (:29-42) gains `page_level: number`, `last_paged_at: string | null` and `active_work_order_id?: number | null`.
- `MaintenanceSummary` (:64-73) gains `open_work_order_count` and `avg_work_order_completion_hours`. `KpiSummary` (:93-99) gains `open_work_order_count`.
- New `WorkOrderStatus`, `WorkOrderPriority`, `WorkOrder`, `WorkOrderEvent`, `WorkOrderDetail`, `WorkOrderCreate`, `WorkOrderFromAlert`, `WorkOrderUpdate`, `WorkOrderComplete`, `WorkOrderChange` and `Assignee`.
- The `LiveEvent` union (:178-222) gains two members:

  ```ts
  export type WorkOrderEventType = 'work_order_created' | 'work_order_updated'
  export type WorkOrderChange = 'assigned' | 'started' | 'completed' | 'cancelled' | 'edited'
  export interface WorkOrderLiveEvent { type: WorkOrderEventType; machine_id: string; work_order: WorkOrder; change?: WorkOrderChange; at: string }
  export interface AlertPagedLiveEvent { type: 'alert_paged'; machine_id: string; alert: Alert; page_level: number; at: string }
  export type LiveEvent = AlertLiveEvent | DeviceLiveEvent | WorkOrderLiveEvent | AlertPagedLiveEvent
  export function isWorkOrderEvent(e: LiveEvent): e is WorkOrderLiveEvent
  ```

  `KNOWN_EVENT_TYPES` gains the three new types.

**Client (`frontend/src/api/client.ts`, after `acknowledgeAlert` :102-103):**
- `listWorkOrders(opts?: { status?: WorkOrderStatus | 'active'; machineId?: string; assignedTo?: number; alertId?: number })`
- `getWorkOrder(id)`, `getAssignees()`
- `createWorkOrder(payload)`, `createWorkOrderFromAlert(alertId, payload?)`, `updateWorkOrder(id, payload)`
- `assignWorkOrder(id, assignedTo, note?)`, `startWorkOrder(id)`, `completeWorkOrder(id, payload)`, `cancelWorkOrder(id, reason?)`

**Styles (`components/healthStyles.ts`), reusing the intensity tokens:**
- `workOrderStatusTone(status)`: `open → neutral`, `assigned → accent`, `in_progress → degrading`, `done → healthy`, `cancelled → unknown`.
- `workOrderStatusLabel(status)`: `Open`, `Assigned`, `In progress`, `Done`, `Cancelled`.
- `pageLevelTone(level)`: `1 → degrading`, `≥2 → critical`.

**`components/CreateWorkOrderDialog.tsx` (new):**
- A modal (`role="dialog"`, `aria-modal`, labelled by its heading; Escape closes, focus returns to the opener).
- Props: `{ alert?: Alert | null; machineId?: string; open; onClose; onCreated(wo) }`.
- Fields:
  - Title, prefilled per decision 13.
  - Description.
  - Priority select.
  - Assignee select, shown to admins and supervisors only and loaded from `getAssignees()` when it opens.
  - Due date (optional).
  - Machine select for free-standing orders without `machineId` (from `getMachines()`).
- **Submit:** calls `createWorkOrderFromAlert` when `alert` is set, otherwise `createWorkOrder`. A 409 shows the server detail inline and offers an "Open work order #n" link.

**`components/WorkOrderDrawer.tsx` (new):**
- A right-side panel (`role="dialog"`, labelled "Work order #{id}") fed by `getWorkOrder(id)`.
- **Header:** title, status badge, priority, machine link, and the linked alert (severity chip, status, "Paged L{n}" if paged).
- **Facts:** assignee, created by and when, due date, started/completed/cancelled times, maintenance record id.
- **Timeline:** `events` oldest-first. Each entry shows its time (`formatRelative`, with absolute time in `title`), who, what (for example "assigned to Sam Supervisor") and its note.
- **Actions** are rendered only when allowed, mirroring the server table:
  - **Assign:** an assignee select plus button, for admins and supervisors while the order is non-terminal.
  - **Start:** when the order is `assigned` and the user is the assignee, an admin or a supervisor.
  - **Complete:** when the order is `in_progress`, for the same users. Clicking it reveals an inline form with notes, "Performed at" (`datetime-local`, default now) and type (Corrective/Preventive), then Confirm.
  - **Cancel:** for admins and supervisors on a non-terminal order, with a reason.
  - **Edit:** for admins and supervisors on a non-terminal order. Reveals an inline form (title, description, priority, due) pre-filled from the order; Save sends only the changed fields via `PATCH` (an emptied description/due is sent as `null`), and closes without a request when nothing changed.
- **Results:** each action toasts and re-fetches. A 409 shows `toast.error(detail)` and re-fetches, which covers the case where another user changed the order first.
- **Live updates:** the drawer re-fetches when `lastEvent` is a work-order event for its id.

**`pages/WorkOrdersPage.tsx` (new; routes `work-orders` and `work-orders/:id`, all roles):**
- Header "Work orders", plus a "New work order" button for admins and supervisors that opens the free-standing dialog.
- **Filters:**
  - Status tabs Active (default), Open, Assigned, In progress, Done, Cancelled and All, each mapped to the `status` query.
  - A "Mine" toggle (`assignedTo = user.id`).
  - An optional `?`-free machine filter select.
- **Table:** #, Title, Machine (link), Priority (badge), Status (badge), Assignee, Alert (#id, or "—"), Updated (relative), and an empty state.
- **Drawer:** clicking a row navigates to `/work-orders/:id`, which opens the drawer. The id is in the path because `LoginPage` restores only the pathname (map §0). Closing navigates back to `/work-orders`.
- **Refresh:** a single list call per load, with no N+1. The page re-fetches when `lastEvent` is a work-order event, and has no polling (all mutations broadcast).

**Nav and routing:**
- `AppShell.tsx` Operations section (:58-65): `{ to: '/work-orders', label: 'Work orders', icon: ClipboardList }` as its first item. Every role sees it, so the Operations section stays visible for operators (they already see Maintenance).
- `App.tsx` ungated block (:44-52): `<Route path="work-orders" …/>` and `<Route path="work-orders/:id" …/>`, both rendering `WorkOrdersPage`.

**`pages/AlertsPage.tsx`:**
- **Status cell** (:131): adds a `Badge` "Paged L{n}" with `pageLevelTone` when `page_level > 0`. Its `title` reads "Last paged {last_paged_at}".
- **Actions cell** (:134-147), next to Acknowledge:
  - **No active order:** a "Create work order" button. It calls `e.stopPropagation()` (the row navigates on click, :120), and the page holds the dialog state outside the row.
  - **Active order:** a "WO #{id}" link to `/work-orders/{id}`, also with `stopPropagation`.
- **After creating:** `toast.success`, then `load(status)`. The alert row now shows "Acknowledged" and the WO link.

**`components/MachineDetail.tsx` and `pages/MachineDetailPage.tsx`:**
- The maintenance facts (:175-193) gain "Open work orders" (`open_work_order_count`) and "Avg WO completion (h)" facts.
- A "Create work order" button sits next to `MaintenanceForm` (:197) and uses the selected alert (`selectedAlert`, :42). Its state depends on the selection and the role:

  | Selection | Button |
  |---|---|
  | alert with an active order | disabled, "WO #n open" |
  | no alert, admin or supervisor | free-standing order for this machine |
  | no alert, operator | disabled, `title="Select an alert first"` |

- It calls a new optional prop `onCreateWorkOrder?(alert: Alert | null)`. `MachineDetailPage` owns `CreateWorkOrderDialog`, because `MachineDetailPage.tsx:15-17,26` remounts `MachineDetail` on every event for the machine, and a dialog inside it would vanish mid-edit (map §3).

**Dashboard (`components/KpiCards.tsx` :13-20):**
- A new card "Open work orders" (`open_work_order_count`, sub "active", tone `accent`).
- The grid becomes `lg:grid-cols-4 xl:grid-cols-8`, to fit eight cards.

**Realtime (`realtime/LiveEventsProvider.tsx`):**
- `describe()` (:31-50) adds:
  - `work_order_created` → `Work order #{id} opened for {machine_id}`
  - `work_order_updated` → by `change`: `assigned to {assigned_to_name}`, `started`, `completed`, `cancelled`, or `updated`
  - `alert_paged` → `{machine_id}: alert unacknowledged — paged level {n}`
- The toast choice (:93-97) becomes:
  - `success` for `alert_resolved`, `device_online` and `work_order_updated` with `change === 'completed'`;
  - `info` for other work-order events;
  - `warning` for everything else.

**`NotificationBell` (`AppShell.tsx:75-…`, effect :82-84):**
- The exclusion test becomes an explicit set of events that page someone: `alert_created`, `alert_escalated`, `alert_paged` and `device_offline`.
- Work-order events and acknowledgements no longer light the dot. This deliberately changes `alert_acknowledged`, which never paged anyone.

**MSW (`frontend/src/test/server.ts`, `fixtures.ts`):**
- Default handlers:
  - `GET /api/work-orders` honours `status` (including `active`) and `assigned_to`, and returns `structuredClone`.
  - `GET /api/work-orders/assignees` is registered before `/:id`.
  - `GET /api/work-orders/:id` returns the detail fixture with `id` patched.
  - `POST /api/work-orders`, `POST /api/alerts/:id/work-order`, `PATCH /api/work-orders/:id` and the four action POSTs each return a cloned, updated fixture and never mutate the shared ones (unlike the ack handler at `server.ts:41-49`).
- Fixtures:
  - `openAlerts[0]` (:75-90) gains `page_level: 0`, `last_paged_at: null` and `active_work_order_id: null`, so the existing AlertsPage tests are unchanged.
  - `kpiSummary` gains `open_work_order_count: 1`.
  - `machineDetail.maintenance` gains the two new fields.
  - New `workOrders`: one `open` order from alert 1 on m1, one `assigned` to the operator (`operatorUser.id`), one `in_progress`, and one `done` with `maintenance_record_id`.
  - New `workOrderDetail` with a three-event timeline, and new `assignees`.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `ESCALATION_L1_MINUTES` | `15` | Minutes an open alert may stay unacknowledged, with no active work order, before supervisors are re-paged (level 1). Measured from `alerts.created_at`. |
| `ESCALATION_L2_MINUTES` | `30` | Minutes before admins are paged (level 2, the last level). Must be greater than L1. |

- **Validation:** both must be positive and finite, and L2 must be greater than L1. Otherwise the problem is logged and both defaults are used. A bad value never disables paging silently, and never fails startup.
- **Requires the scheduler:** the ladder only runs when `MAINTAINIQ_SWEEP_INTERVAL_S > 0`. It is registered in `_start_background_jobs` (`src/api/app.py:77-111`) as a second job, `alert_paging`, after `device_silence` (:104), with `pager.set_pager()` / `set_pager(None)` in the lifespan next to the watcher (:109, :136).
- **Docker:** `docker-compose.yml` needs no change. The app service reads `.env` via `env_file` (:6), and the scheduler is already on at 5 s in compose (:29).
- **Tests:** the autouse conftest fixture (`tests/conftest.py:32-36`) adds `"ESCALATION_"` to its scrubbed prefixes.

## Test plan

### Backend (pytest)

**`tests/storage/test_migrations.py`:**
1. `_v1_db()` (:166-181) and `_v2_db()` (:275-291) also drop `work_orders` and `work_order_events`. The version asserts already use `LATEST` (:210-220, :314-326), so none of them needs editing.
2. A new `_v3_db()` runs steps 1–3, drops both work-order tables, rebuilds `alerts` in its pre-v4 shape (`_PRE_V4_ALERTS`, no `page_level`/`last_paged_at`) and stamps versions 1–3.
3. **v3→v4:** `alerts` rows are preserved; open ones read `page_level = 2` (pre-ladder, see decision 7) and every row reads `last_paged_at IS NULL`; `maintenance_records`, `users`, `notifications` and `device_incidents` rows are preserved; both tables and all four indexes exist; versions are `[1..4]`; a re-run is a no-op.
4. **Fresh DB constraints:** the status, priority and event CHECKs reject bad values. The partial unique index allows only one active order per alert, but allows a second order once the first is `done` or `cancelled`, and allows any number of `alert_id IS NULL` orders.
5. **Legacy IMS upgrade** (the existing test at :136) still reaches `LATEST`, and its preserved alerts gain `page_level`.
6. **`ensure_current_schema`:**
   - a v3 file is brought to `LATEST`;
   - a v0 file is left untouched (no `machines` drop);
   - a second call does not re-query once the file is memoised (spy on `run_migrations`);
   - two threads calling at once stamp version 4 exactly once.

**`tests/api/test_get_db_schema.py` (new):** `get_db` with `src.api.deps.get_connection` monkeypatched to a temp v3 file migrates it to `LATEST` before yielding.

**`tests/work_orders/test_service.py` (new; seeded conftest DB, explicit `now=`):**
1. `create_from_alert` on open alert 2 (m1):
   - the order is `open`, priority `high`, title `m1 critical`, `created_by` is the actor;
   - one `created` event is written, with note `from alert #2`;
   - the alert gets `acknowledged_at = now` and `acknowledged_by = actor`;
   - the returned acknowledged alert is not `None`.
2. `create_from_alert` on an already-acknowledged alert leaves `acknowledged_*` unchanged and returns `None` for the acknowledged alert.
3. A second `create_from_alert` for the same alert raises `WorkOrderConflict`, and the alert's acknowledgement is not touched by the failed call (pre-acknowledge a fresh alert on m2 to check).
4. After the first order is `cancelled`, a new one can be created for the same alert.
5. `create_from_alert` on resolved alert 1 works. An unknown alert raises `WorkOrderNotFound`.
6. An operator passing `assigned_to` raises `WorkOrderForbidden`. A supervisor passing it creates the order as `assigned`, with an `assigned` event.
7. Free-standing `create`: an operator raises `WorkOrderForbidden`; an unknown `machine_id`, an inactive or unknown `assigned_to`, an empty title or an unparseable `due_at` raises `WorkOrderError` (400).
8. **Full happy path:** assign (supervisor) → start (operator assignee) → complete (operator assignee):
   - statuses and the `started_at`/`completed_at` timestamps are set;
   - four events have the correct from/to statuses;
   - a `maintenance_records` row exists with `type = 'corrective'`, `alert_id = 2`, `technician` = the operator's name, and a description starting `Work order #`;
   - `maintenance_record_id` links to it.
9. `complete` with `maintenance_type='preventive'` and an explicit `performed_at` uses both.
10. **Invalid edges raise `WorkOrderConflict`:** start from `open`; complete from `assigned`; any action on `done`/`cancelled`; edit when terminal; cancel when terminal.
11. **RBAC:**
    - an operator who is not the assignee cannot start or complete (`WorkOrderForbidden`);
    - an admin who is not the assignee can;
    - an operator cannot assign, cancel or edit.
12. Reassigning while `in_progress` keeps the status, and writes an `assigned` event whose `from_status` equals `to_status`.
13. `list_work_orders`:
    - the `status` filters work, including `active`;
    - the `assigned_to`, `machine_id` and `alert_id` filters work;
    - ordering is `updated_at DESC, id DESC`;
    - `assigned_to_name` and `created_by_name` are joined.
14. `list_assignees` returns active users only, without emails.
15. If `log_maintenance` raises `MaintenanceError` (monkeypatched), the order stays `in_progress` and no `completed` event is written.

**`tests/api/test_work_orders_route.py` (new):**
1. Anonymous gets 401 on every route.
2. The RBAC matrix is parametrised over admin, supervisor and operator for each route in the API table:
   - 201/200 vs 403;
   - operator start/complete only works on an order assigned to the operator user.
3. 404 for unknown work order ids and alert ids; 409 for a duplicate from the same alert and for an invalid transition; 400 for a bad `status` filter and an unknown assignee; 422 for `limit=0`.
4. `POST /api/alerts/2/work-order` with an empty body returns 201. A subsequent `GET /api/alerts` shows alert 2 with `acknowledged_at` set and `active_work_order_id` equal to the new id.
5. `GET /api/machines/m1` alerts carry `page_level`, `last_paged_at` and `active_work_order_id`. Its `maintenance.open_work_order_count` is 1.
6. **Broadcasts** (spy on `manager.broadcast`):
   - create-from-alert sends `alert_acknowledged`, then `work_order_created`;
   - assign/start/complete/cancel/edit each send `work_order_updated` with the right `change`;
   - a raising broadcast still returns 2xx.
7. `GET /api/work-orders/assignees` gives 403 for operators, and lists the three demo users for admins and supervisors.
8. `GET /api/work-orders/{id}` returns `events` oldest-first and the linked `alert`.

**`tests/alerts/test_paging.py` (new; conftest DB, fake `notify` recording `(alert_id, roles, page_level)`, `now` derived from alert 2's `created_at`):**
1. At 14 min: no change, no event, no notify.
2. At 15 min: `page_level` 1, `last_paged_at = now`, one `alert_paged` event (`page_level: 1`), notify called with `roles=('supervisor',)`.
3. A second tick at 16 min does nothing (no double page).
4. At 30 min: level 2, roles `('admin',)`. A tick at 60 min does nothing (top level).
5. The first tick already at 45 min jumps 0→2 with one notify, roles `('supervisor', 'admin')` and one event.
6. An acknowledged alert is never paged. Acknowledging between L1 and L2 stops L2.
7. An alert with an active work order (inserted directly, no ack) is not paged. With only a `cancelled` order, it is.
8. A resolved alert is not paged.
9. Age uses `created_at`, not `opened_at`: alert 2's 2003 `opened_at` with a fresh `created_at` is not paged at `now = created_at + 1 min`.
10. Mixed `Z` and `+00:00` `created_at` suffixes give the same decision. An unparseable `created_at` is skipped without raising.
11. A raising `notify` still commits the level and returns the event.
12. **CAS race:** the row's `page_level` is bumped between the SELECT and the UPDATE (by patching a hook), and the tick sends nothing for it.
13. **Lock:** while the test holds `live._TRANSITION_LOCK`, a tick in a thread blocks until the lock is released.
14. A pre-v4 file is migrated on the first tick. A file still without `page_level` returns `[]`.
15. `PagingPolicy.from_env`: the defaults; `"5"`/`"10"`; and ValueError for `"abc"`, `"0"`, `"-1"`, `"inf"`, and L2 ≤ L1. `from_env_or_default` logs and returns the defaults.

**`tests/test_notifications.py` (extend):**
1. `notify_alert(conn, ALERT, roles=("supervisor",), page_level=1)` emails only the supervisor, and the subject starts `[Unacknowledged — page 1]`.
2. The body mentions the paging level.
3. The existing tests (:29-88) pass unmodified.

**`tests/prediction/test_pipeline.py` (extend):**
1. `fan_out` stamps `last_paged_at` on `alert_created`/`alert_escalated` but not on `alert_resolved`.
2. A failing stamp is logged and the alert stays committed.
3. The existing tests pass unmodified, which proves `handle_prediction`'s behaviour is unchanged.

**`tests/test_demo.py` (extend):**
1. The existing tests pass against the refactored sync routes.
2. Simulate-fault sets `last_paged_at`.
3. A raising `notify_alert` now yields 200 with `emails_sent: 0` and the alert committed.

**`tests/test_api.py` (extend):**
1. `POST /api/alerts/{id}/acknowledge` is unchanged (200, idempotent, 404), now through `live.acknowledge_alert`.
2. Two acknowledgements racing in threads leave `acknowledged_by` equal to the first writer.

**`tests/kpi/` / `tests/test_kpi.py` (extend):**
- `open_work_order_count` and `avg_work_order_completion_hours` per machine;
- the fleet `open_work_order_count` in `summary()`;
- on a DB without `work_orders`, they report 0 or `None` and nothing raises;
- the existing assertions are unchanged.

**`tests/background/test_scheduler.py`:**
1. The lifespan job assertion (:240) becomes `("device_silence", "alert_paging")`.
2. With invalid `ESCALATION_*` the scheduler still starts with the default policy.

**`tests/test_alerts_live_concurrency.py` (extend):**
- `_ALERT_COLUMNS` round-trips the new columns;
- the create dict has `page_level: 0`.

**`tests/docs/*`:** `work_orders` and `work_order_events` are documented (enforced automatically).

### Frontend (vitest + RTL + MSW)

- **`components/healthStyles.test.ts`:** `workOrderStatusTone`/`Label` for all five statuses plus unknown; `pageLevelTone`.
- **`components/CreateWorkOrderDialog.test.tsx` (new):**
  1. The title is prefilled from the alert message, and the priority from severity.
  2. Admins and supervisors see the assignee select, filled from `/api/work-orders/assignees`; operators don't, and no assignees request is made.
  3. Submit POSTs to `/api/alerts/2/work-order` (spy on the request body) and calls `onCreated`.
  4. A 409 shows the detail and an "Open work order" link.
  5. Free-standing mode lists machines and POSTs to `/api/work-orders`.
  6. Escape closes it.
- **`components/WorkOrderDrawer.test.tsx` (new):**
  1. Renders the facts and the timeline in order.
  2. **Visibility per role and status:**
     - an operator viewing an order assigned to them sees Start, and nothing else;
     - an operator viewing someone else's order sees no actions;
     - a supervisor sees Assign and Cancel.
  3. Complete reveals the form and POSTs notes, `performed_at` and type.
  4. A 409 from Start toasts an error and re-fetches.
  5. It re-fetches on a matching `work_order_updated` event.
- **`pages/WorkOrdersPage.test.tsx` (new):**
  1. Defaults to Active and requests `status=active`.
  2. The tabs change the `status` query; "Mine" adds `assigned_to`.
  3. A row click navigates to `/work-orders/:id` and the drawer opens.
  4. Deep-linking `/work-orders/3` opens the drawer.
  5. "New work order" is visible to admins and supervisors only.
  6. It re-fetches on `work_order_created` and ignores device events.
- **`pages/AlertsPage.test.tsx` (extend):**
  1. "Create work order" opens the dialog without navigating (stopPropagation), and on success the list re-fetches.
  2. A row with `active_work_order_id` shows a "WO #n" link instead.
  3. `page_level: 2` renders the "Paged L2" badge.
- **`components/MachineDetail.test.tsx` / `pages/MachineDetailPage.test.tsx` (extend):**
  1. The "Open work orders" fact shows.
  2. The Create button calls `onCreateWorkOrder` with the selected alert.
  3. For an operator with no alert selected, the button is disabled.
  4. The dialog survives a live event for the machine that remounts `MachineDetail`.
- **`components/KpiCards.test.tsx`:** the "Open work orders" card shows the value.
- **`realtime/LiveEventsProvider.test.tsx`:**
  - `work_order_created` → info toast with the id;
  - `work_order_updated` completed → success toast;
  - `alert_paged` → warning toast with the level;
  - all three update `lastEvent`.
- **`layout/AppShell.test.tsx` / `App.test.tsx`:**
  - every role sees the "Work orders" nav link and can open `/work-orders`;
  - the bell dot lights for `alert_paged` but not for `work_order_updated` or `alert_acknowledged`.
- **Build and lint:** `npm run build` (type-checks the extended union and the exhaustive `describe()` switch) and `npm run lint`.

## Docs to update

- **`docs/DATA_MODEL.md`:**
  - `### \`work_orders\`` and `### \`work_order_events\`` sections (columns, indexes and the partial unique index explained);
  - `alerts` table (:185-205) gains `page_level` and `last_paged_at` with notes;
  - the ER diagram (:14-29) gains `machines ||--o{ work_orders : has`, `alerts ||--o{ work_orders : "repaired via"`, `work_orders ||--o{ work_order_events : "audited by"` and `work_orders |o--o| maintenance_records : "completed as"`;
  - Storage layout (:407-433) gains a migration 4 paragraph. The sentence "Migrations are not run at app start…" is rewritten to describe `ensure_current_schema` being called from `get_db`.
- **`README.md`:**
  - two env-table rows after `DEVICE_SILENT_GRACE_S` (:64);
  - a new "#### Work orders and paging" subsection after the device-health endpoint list (~:221). It covers the ladder, the routes and the note that alerts predating the ladder are never paged by it.
- **`.env.example`:** an "Alert paging escalation" block after the device-health block (:107-112) with both variables commented at their defaults.
- **`TODO.md`:** a checked item "Work orders + paging escalation (design/2026-10-07-work-orders-escalation-design.md)" under the M6 / device-health items (:47).
- **`IMPLEMENTATION_PLAN.md`:** `## M8 — Work orders + escalation` after M7 (:50).
- **`src/storage/migrations.py`:** the docstring gains a migration 4 paragraph and `ensure_current_schema`.
- **`src/notifications/dispatch.py`:** the module docstring (:1-15) gains the paging ladder.
- **`design/_integration_map.md`:** not edited. This doc supersedes §3 where they differ: action endpoints instead of `/transition`; age measured from `created_at`; lazy upgrade in `get_db`; no `notifications.page_level`.
- **`PROJECT_CONTEXT.md`:** not touched.

## Implementation notes (backend, 2026-10-07)

Small additions and clarifications made while building the backend; the
decisions above are unchanged.

- **`src/alerts/live.py` helpers.** Besides `acknowledge_alert` /
  `_acknowledge_locked`, it exposes `get_alert(conn, id)` (one row in the
  broadcast/API shape, used by the ack route, work-order creation and the
  pager) and `API_ALERT_COLUMNS` (`_ALERT_COLUMNS` plus the
  `active_work_order_id` subquery), which the alert list, machine detail and
  `GET /api/work-orders/{id}` share instead of three copies of the SQL.
- **CAS test hook.** `src/alerts/paging._before_page(conn, alert_id)` is the
  no-op hook test 12 patches; it runs between choosing a candidate and its
  compare-and-set.
- **Level-0 stamp** is written after `notify_alert` whether or not the email
  call raised (an attempted page is still a page), and the broadcast alert dict
  carries the stamped `last_paged_at`.
- **`fan_out` events** carry `prediction` only when one is passed, so demo
  events keep their old shape (no `prediction` key).
- **Request bodies** of `/complete` and `/cancel` are optional, like
  `POST /api/alerts/{id}/work-order`; an empty `title` in
  `POST /api/work-orders` fails schema validation (422) before the service's
  400 check is reached.
- **`ensure_current_schema`** also exposes `LATEST_VERSION` and a test-only
  `_reset_current_schema_memo()`; the memo is skipped for in-memory DBs.
- **Paging policy validation** also rejects `nan`.

- **Routes never block the event loop** (fix, 2026-10-07). The mutating
  alert and work-order routes are `async` only to await their broadcast; the
  service call (SQLite writes that may wait out the busy timeout, and
  `live._TRANSITION_LOCK` / the work-order lock) runs through
  `asyncio.to_thread`. Run on the loop, one slow writer elsewhere froze every
  websocket and request (`tests/api/test_routes_off_event_loop.py`).
- **Notification rows are written after sending** (fix, 2026-10-07).
  `notify_alert` / `notify_device_incident` send every email first and then
  insert all the `notifications` rows in one short transaction, as push does,
  so SQLite's write lock is never held across an SMTP send.

## Implementation notes (frontend, 2026-10-07)

The Frontend UX section was built as written, with these additions:

- **One route, `work-orders/:id?`.** The list and the drawer share a single
  route, and `AppShell` keys its page transition with `transitionKey()`, which
  maps every `/work-orders…` path to `/work-orders`. Without this, opening an
  order would remount the page, replay the entrance and reset the filters.
- **Shared pieces.**
  - `lib/useDialogFocus.ts`: Escape-to-close, focus into the panel, and
    focus back to the opener. The dialog and the drawer both use it.
  - `lib/datetime.ts`: `localInputToIso` sends `datetime-local` values as
    UTC ISO-8601, and `nowLocalInput` gives the "Performed at" default.
  - A `Textarea` in `components/ui/input.tsx`.
  - `healthStyles.workOrderPriorityTone`: priority reuses the severity ramp.
    `pageLevelTone(0)` is `neutral`.
- **Drawer.**
  - Its optional `onChanged` callback lets the list refresh after an action,
    even while the socket is down.
  - Assign and Reassign are one form, labelled "New assignee".
  - Complete and Cancel each open an inline confirm form ("Confirm
    completion", "Confirm cancel").
- **Machine Detail.** The button reads "Create work order" in every enabled
  state. It is rendered only when `onCreateWorkOrder` is passed.
- **After a create:**
  - The Work Orders page navigates to the new order's drawer.
  - Machine Detail Page remounts the detail.
  - The Alerts page re-fetches.
- **MSW.** A test that overrides `GET /api/work-orders/:id` must let
  `/assignees` fall through, because the pattern matches it too.
  `WorkOrderDrawer.test.tsx` has an `onGetOrder` helper for this.

## Risks

- ~~**First sweep after deploy pages every old unacknowledged open alert** straight to level 2.~~ Fixed 2026-10-07: migration 4 and the batch seed mark pre-ladder alerts as already at level 2 with `last_paged_at` NULL (decision 7). Residual: a database already upgraded to v4+ by a pre-fix build keeps `page_level = 0` on its old alerts; acknowledge them once from the Alerts page.
- **Non-atomic completion.** `log_maintenance` commits before the work-order update. A crash or DB error between the two leaves a maintenance record with no `done` order. The order stays `in_progress`, and retrying Complete creates a second record. The process lock prevents concurrent double-completion, and multiple app instances are unsupported (README).
- **Lazy upgrade in `get_db`** runs migrations on a live request.
  - Migration 4 is additive, so it is fast, and the lock makes it run once.
  - A failure is logged, and readers of the new columns then 500 until the cause is fixed. That is no worse than today's behaviour on a stale file.
  - v0 files are never touched.
- **Acknowledgement now happens implicitly** on work-order creation. This skews "time to acknowledge" toward the time of creation, which is intended: creating an order is a human taking ownership.
- **Ladder keyed to `created_at`.** An alert that is resolved and re-opened is a new row, so the ladder restarts. A long-running open alert whose severity escalates is not re-laddered (decision 7).
- **Demo route becomes sync with fire-and-forget broadcast.** Any test that awaited the websocket frame synchronously after simulate-fault could become timing-dependent. None exists today (`tests/test_demo.py` asserts DB rows and emails only).
- **`LiveEvent` union growth.** Every `lastEvent` consumer must narrow before using `.alert`. `tsc -b` enforces this, and unknown types are still dropped at the provider.
- **Scheduler on in tests** would write pages to the real DB. This is mitigated the same way as in device health: the scheduler is off by default, the env is scrubbed (now including `ESCALATION_`), the connection factory is injectable, and the first tick only comes after one interval.
- **Bell behaviour change.** `alert_acknowledged` no longer lights the dot. This is intentional, but the change is user-visible.
