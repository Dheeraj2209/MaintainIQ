# Alert Acknowledge Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a human-driven "acknowledge" step to the alert lifecycle — schema, API, realtime broadcast, KPI, and UI — per `design/2026-08-04-alert-acknowledge-design.md`.

**Architecture:** `acknowledged_at`/`acknowledged_by` are new nullable columns on `alerts`, orthogonal to the existing `status` (`open`/`resolved`). A new `POST /alerts/{id}/acknowledge` endpoint (role-gated to admin/supervisor/operator) sets them idempotently, broadcasts an `alert_acknowledged` WebSocket event, and the frontend adds a button + toast + KPI fact for it.

**Tech Stack:** FastAPI + raw sqlite3 (no ORM/migrations — schema changes are a `SCHEMA` string edit plus a guarded `ALTER TABLE`), pytest + `TestClient`, React + TypeScript, Vitest + Testing Library + MSW.

## Global Constraints

- No new `acknowledged` status value — `status` stays `open`/`resolved`; acknowledgement is independent.
- Re-acknowledging an already-acknowledged alert is an idempotent no-op: 200, unchanged alert, no re-broadcast.
- Any alert (open or resolved) can be acknowledged.
- `acknowledged_by` stores the numeric `users.id` (no FK constraint, consistent with existing columns).
- Acknowledge button appears only on `AlertsPage.tsx`, not `AlertsPanel.tsx`.
- Roles allowed to acknowledge: `admin`, `supervisor`, `operator` (i.e., all three — same set `require_role` is normally used to restrict, here it's used to just capture identity).

---

### Task 1: Database schema — `acknowledged_at`/`acknowledged_by` columns

**Files:**
- Modify: `src/storage/db.py:70-83` (SCHEMA `alerts` table), `src/storage/db.py:137-139` (`init_schema`)
- Test: `tests/test_db.py` (new file)

**Interfaces:**
- Produces: `alerts.acknowledged_at` (TEXT, nullable), `alerts.acknowledged_by` (INTEGER, nullable), both selected by every existing `SELECT * FROM alerts` / `SELECT id, ... FROM alerts` style query since sqlite3.Row supports dict-style access by column name.

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
"""Tests for src/storage/db.py schema (M-ack)."""
import sqlite3

from src.storage.db import SCHEMA, init_schema


def test_alerts_table_has_acknowledge_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
    assert "acknowledged_at" in cols
    assert "acknowledged_by" in cols


def test_init_schema_is_idempotent_on_existing_db():
    """A DB created before this change (no ack columns) must still work after
    init_schema runs again — the guarded ALTER TABLE must not raise."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Simulate a pre-existing DB: create the old alerts table shape only.
    conn.execute(
        """CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            resolved_at TEXT,
            severity TEXT NOT NULL,
            health_state TEXT NOT NULL,
            probable_cause TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            source TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()

    init_schema(conn)  # must not raise, and must add the new columns

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
    assert "acknowledged_at" in cols
    assert "acknowledged_by" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL — `test_alerts_table_has_acknowledge_columns` fails with `AssertionError` (column not found); `test_init_schema_is_idempotent_on_existing_db` passes trivially today (no ALTER TABLE exists yet to raise), but will validate the migration path once Step 3 lands.

- [ ] **Step 3: Add columns to SCHEMA and a guarded migration**

In `src/storage/db.py`, edit the `alerts` table definition (currently lines 70-83):

```python
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    severity TEXT NOT NULL,
    health_state TEXT NOT NULL,
    probable_cause TEXT,
    message TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    acknowledged_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_machine_status ON alerts(machine_id, status);
```

Then edit `init_schema` (currently lines 137-139) to add a guarded migration for DBs created before this change, where `CREATE TABLE IF NOT EXISTS` above is a no-op:

```python
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for column in ("acknowledged_at TEXT", "acknowledged_by INTEGER"):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB created via SCHEMA above)
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/storage/db.py tests/test_db.py
git commit -m "Add acknowledged_at/acknowledged_by columns to alerts table"
```

---

### Task 2: `Alert` schema fields

**Files:**
- Modify: `src/api/schemas.py:34-44`

**Interfaces:**
- Consumes: nothing new (pure model field addition)
- Produces: `Alert.acknowledged_at: Optional[str]`, `Alert.acknowledged_by: Optional[int]` — consumed by Task 3's endpoint response and by every existing endpoint that returns `Alert`/`list[Alert]` (`GET /alerts`, `GET /machines/{id}` detail's `alerts` field).

- [ ] **Step 1: Edit the model**

In `src/api/schemas.py`, change the `Alert` model (currently lines 34-44):

```python
class Alert(BaseModel):
    id: int
    machine_id: str
    opened_at: str
    resolved_at: Optional[str] = None
    severity: str
    health_state: str
    probable_cause: Optional[str] = None
    message: Optional[str] = None
    status: str
    source: Optional[str] = None
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[int] = None
```

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `python -m pytest tests/test_api.py -v`
Expected: PASS — `sqlite3.Row` rows are unpacked into `Alert(**dict(row))`-style construction elsewhere (see Task 3), and since the new columns exist (Task 1) and are nullable, no existing call site breaks. If any call site constructs `Alert` from an explicit dict (not `**row`), it will need the two new keys — grep to confirm:

Run: `grep -rn "Alert(" src/api/`
Expected: all call sites either use `**dict(row)`/`**alert` (dict unpacking, automatically picks up new nullable columns) or `Alert(**alert)` where `alert` is itself a `dict(row)` — verified in `src/api/routes/demo.py:50` (`Alert(**alert)`) and `src/api/routes/alerts.py` (list endpoint). No explicit-kwarg construction exists, so no call site needs editing.

- [ ] **Step 3: Commit**

```bash
git add src/api/schemas.py
git commit -m "Add acknowledged_at/acknowledged_by to Alert schema"
```

---

### Task 3: `POST /alerts/{id}/acknowledge` endpoint

**Files:**
- Modify: `src/api/routes/alerts.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `require_role` from `src/auth/deps.py` (`Depends(require_role("admin", "supervisor", "operator"))` returns the user dict with `user["id"]`), `manager.broadcast(dict)` from `src/realtime/manager.py`, `get_db` from `src/api/deps.py`.
- Produces: `POST /alerts/{id}/acknowledge` -> `Alert` (200) or 404 if the alert id doesn't exist. Broadcasts `{"type": "alert_acknowledged", "machine_id": ..., "alert": {...}, "at": iso_timestamp}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_acknowledge_alert_sets_fields_and_broadcasts(client):
    resp = client.post("/api/alerts/2/acknowledge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 2
    assert body["acknowledged_at"] is not None
    assert body["acknowledged_by"] is not None


def test_acknowledge_alert_is_idempotent(client):
    first = client.post("/api/alerts/2/acknowledge").json()
    second = client.post("/api/alerts/2/acknowledge").json()
    assert second["acknowledged_at"] == first["acknowledged_at"]
    assert second["acknowledged_by"] == first["acknowledged_by"]


def test_acknowledge_alert_unknown_id_404(client):
    assert client.post("/api/alerts/9999/acknowledge").status_code == 404


def test_acknowledge_alert_allows_all_three_roles(auth_client):
    for role in ("admin", "supervisor", "operator"):
        c = auth_client(role)
        resp = c.post("/api/alerts/2/acknowledge")
        assert resp.status_code == 200, f"{role} should be able to acknowledge"


def test_acknowledge_alert_requires_login(anon_client):
    assert anon_client.post("/api/alerts/2/acknowledge").status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py -k acknowledge -v`
Expected: FAIL with 404 (route doesn't exist yet) for all five tests.

- [ ] **Step 3: Implement the endpoint**

In `src/api/routes/alerts.py`, current full content is:

```python
from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.api.schemas import Alert

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[Alert])
def list_alerts(
    status: Optional[str] = Query(None, description="open | resolved"),
    machine_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    ...
```

Replace it with (adding imports, the new endpoint, and the broadcast call):

```python
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_db
from src.api.schemas import Alert
from src.auth.deps import require_role
from src.realtime.manager import manager

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[Alert])
def list_alerts(
    status: Optional[str] = Query(None, description="open | resolved"),
    machine_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    ...


@router.post("/{alert_id}/acknowledge", response_model=Alert)
async def acknowledge_alert(
    alert_id: int,
    db=Depends(get_db),
    user: dict = Depends(require_role("admin", "supervisor", "operator")),
):
    row = db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown alert: {alert_id}")

    alert = dict(row)
    if alert["acknowledged_at"] is not None:
        return Alert(**alert)  # already acknowledged: idempotent no-op, no re-broadcast

    acknowledged_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
        (acknowledged_at, user["id"], alert_id),
    )
    db.commit()

    alert["acknowledged_at"] = acknowledged_at
    alert["acknowledged_by"] = user["id"]

    await manager.broadcast({
        "type": "alert_acknowledged",
        "machine_id": alert["machine_id"],
        "alert": alert,
        "at": acknowledged_at,
    })

    return Alert(**alert)
```

Note: keep whatever imports/body `list_alerts` already has in the real file — only the header imports and the new endpoint are additions; do not alter `list_alerts`'s existing logic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py -k acknowledge -v`
Expected: PASS (5 tests)

Run full backend suite to check for regressions: `python -m pytest tests/ -v`
Expected: PASS (all tests, including the pre-existing 61 + Task 1's 2 + Task 3's 5)

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/alerts.py tests/test_api.py
git commit -m "Add POST /alerts/{id}/acknowledge endpoint"
```

---

### Task 4: KPI — `avg_alert_acknowledgement_hours`

**Files:**
- Modify: `src/kpi/calculations.py:150-192` (`_maintenance_for_machine`)
- Modify: `src/api/schemas.py` (maintenance summary model — find the model backing `maintenance_kpis`'s dict)
- Test: `tests/test_kpi.py`

**Interfaces:**
- Consumes: `alerts.acknowledged_at` (Task 1), `conn` (sqlite3 connection with `Row` factory)
- Produces: `_maintenance_for_machine()` return dict gains `avg_alert_acknowledgement_hours: float | None`, consumed by the `MachineDetail.tsx` UI (Task 7) and `GET /machines/{id}` / `GET /kpis/{id}` responses.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_kpi.py` (after `test_maintenance_kpis_avg_resolution_hours`):

```python
def test_maintenance_kpis_avg_acknowledgement_hours_none_when_unacknowledged(conn):
    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    assert m1["avg_alert_acknowledgement_hours"] is None  # neither seeded alert is acknowledged


def test_maintenance_kpis_avg_acknowledgement_hours_computed(conn):
    from datetime import datetime, timezone
    # Acknowledge the open alert (id 2, opened 2003-10-22T13:00:00+00:00) 30 minutes later.
    conn.execute(
        "UPDATE alerts SET acknowledged_at = ?, acknowledged_by = 1 WHERE id = 2",
        ("2003-10-22T13:30:00+00:00",),
    )
    conn.commit()
    m1 = kpi.maintenance_kpis(conn, "m1")[0]
    assert m1["avg_alert_acknowledgement_hours"] == round(30 / 60, 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_kpi.py -k acknowledgement -v`
Expected: FAIL with `KeyError: 'avg_alert_acknowledgement_hours'`

- [ ] **Step 3: Implement in `_maintenance_for_machine`**

In `src/kpi/calculations.py`, the existing resolution-hours block (lines 164-179) reads:

```python
    cur = conn.execute(
        """SELECT opened_at, resolved_at FROM alerts
           WHERE machine_id = ? AND status = 'resolved' AND resolved_at IS NOT NULL""",
        (machine_id,),
    )
    durations = []
    from datetime import datetime
    for row in cur.fetchall():
        try:
            opened = datetime.fromisoformat(row["opened_at"])
            resolved = datetime.fromisoformat(row["resolved_at"])
        except (ValueError, TypeError):
            continue
        durations.append((resolved - opened).total_seconds() / 3600.0)
    avg_resolution_hours = round(sum(durations) / len(durations), 2) if durations else None
```

Add immediately after it (still inside `_maintenance_for_machine`, before the `return` dict is built):

```python
    cur = conn.execute(
        """SELECT opened_at, acknowledged_at FROM alerts
           WHERE machine_id = ? AND acknowledged_at IS NOT NULL""",
        (machine_id,),
    )
    ack_durations = []
    for row in cur.fetchall():
        try:
            opened = datetime.fromisoformat(row["opened_at"])
            acknowledged = datetime.fromisoformat(row["acknowledged_at"])
        except (ValueError, TypeError):
            continue
        ack_durations.append((acknowledged - opened).total_seconds() / 3600.0)
    avg_acknowledgement_hours = round(sum(ack_durations) / len(ack_durations), 2) if ack_durations else None
```

Then add `"avg_alert_acknowledgement_hours": avg_acknowledgement_hours,` to the function's return dict, alongside the existing `"avg_alert_resolution_hours": avg_resolution_hours,` entry (return dict currently spans lines 181-192).

- [ ] **Step 4: Add the field to the API schema**

Run: `grep -n "avg_alert_resolution_hours" src/api/schemas.py`

Find the Pydantic model containing `avg_alert_resolution_hours: Optional[float] = None` (the maintenance-summary model backing `maintenance_kpis()`'s dict) and add directly below it:

```python
    avg_alert_acknowledgement_hours: Optional[float] = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_kpi.py tests/test_api.py -v`
Expected: PASS (all tests, no regressions in `test_maintenance_post_valid_then_history` or `test_kpi_detail_has_all_categories` which round-trip through the same schema)

- [ ] **Step 6: Commit**

```bash
git add src/kpi/calculations.py src/api/schemas.py tests/test_kpi.py
git commit -m "Add avg_alert_acknowledgement_hours KPI"
```

---

### Task 5: Frontend types + API client

**Files:**
- Modify: `frontend/src/api/types.ts:27-38` (`Alert`), `frontend/src/api/types.ts:56-64` (`MaintenanceSummary`), `frontend/src/api/types.ts:147` (`LiveEventType`)
- Modify: `frontend/src/api/client.ts:71-79` (add `acknowledgeAlert`)

**Interfaces:**
- Produces: `api.acknowledgeAlert(id: number): Promise<Alert>`, `Alert.acknowledged_at`/`acknowledged_by`, `LiveEventType` including `'alert_acknowledged'` — consumed by Task 6 (`AlertsPage.tsx`, `LiveEventsProvider.tsx`) and Task 7 (`MachineDetail.tsx`).

- [ ] **Step 1: Edit `Alert` and `MaintenanceSummary` in types.ts**

In `frontend/src/api/types.ts`, the `Alert` interface (lines 27-38) currently ends with `source: string | null` (or similar) — add two fields:

```ts
export interface Alert {
  id: number
  machine_id: string
  opened_at: string
  resolved_at: string | null
  severity: string
  health_state: string
  probable_cause: string | null
  message: string | null
  status: string
  source: string | null
  acknowledged_at: string | null
  acknowledged_by: number | null
}
```

In the `MaintenanceSummary` interface (lines 56-64), add alongside `avg_alert_resolution_hours: number | null`:

```ts
  avg_alert_acknowledgement_hours: number | null
```

Update `LiveEventType` (line 147) from:

```ts
export type LiveEventType = 'alert_created' | 'alert_escalated' | 'alert_resolved'
```

to:

```ts
export type LiveEventType = 'alert_created' | 'alert_escalated' | 'alert_resolved' | 'alert_acknowledged'
```

- [ ] **Step 2: Add the client method**

In `frontend/src/api/client.ts`, inside the `api` object, add after `logMaintenance` (currently lines 75-79):

```ts
  acknowledgeAlert: (id: number) =>
    request<Alert>(`/alerts/${id}/acknowledge`, { method: 'POST' }),
```

- [ ] **Step 3: Verify the project still typechecks**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors. (`fixtures.ts`'s `Alert`/`MaintenanceSummary` object literals will now be missing the new required fields and fail to typecheck — this is expected and fixed in Task 6.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "Add acknowledge fields/client method to frontend API layer"
```

---

### Task 6: `AlertsPage.tsx` acknowledge button + `LiveEventsProvider` toast + test fixtures

**Files:**
- Modify: `frontend/src/pages/AlertsPage.tsx`
- Modify: `frontend/src/realtime/LiveEventsProvider.tsx:28-39`
- Modify: `frontend/src/test/fixtures.ts:59-72` (`openAlerts`), `frontend/src/test/fixtures.ts:79-92` (`machineDetail.maintenance`)
- Modify: `frontend/src/test/server.ts` (add acknowledge handler)
- Test: `frontend/src/pages/AlertsPage.test.tsx`, `frontend/src/realtime/LiveEventsProvider.test.tsx`

**Interfaces:**
- Consumes: `api.acknowledgeAlert` (Task 5), `Alert.acknowledged_at` (Task 5)

- [ ] **Step 1: Fix fixtures so existing tests keep passing**

In `frontend/src/test/fixtures.ts`, `openAlerts` (lines 59-72) must include the two new required `Alert` fields. Change:

```ts
export const openAlerts: Alert[] = [
  {
    id: 2,
    machine_id: 'm1',
    opened_at: '2003-10-22T13:00:00+00:00',
    resolved_at: null,
    severity: 'high',
    health_state: 'critical',
    probable_cause: 'bearing_wear',
    message: 'm1 critical',
    status: 'open',
    source: 'ml',
    acknowledged_at: null,
    acknowledged_by: null,
  },
]
```

And `machineDetail.maintenance` (lines 79-92) needs `avg_alert_acknowledgement_hours: null` added alongside `avg_alert_resolution_hours: 0.08`.

Also fix `AlertsPage.test.tsx`'s inline `Alert[]` literals in the `'sorts by severity when requested'` test (lines 51-76) — add `acknowledged_at: null, acknowledged_by: null` to both objects.

- [ ] **Step 2: Add an MSW handler for acknowledge**

In `frontend/src/test/server.ts`, add to the `handlers` array (after the alerts handler, line 28):

```ts
  http.post('/api/alerts/:id/acknowledge', ({ params }) => {
    const alert = openAlerts.find((a) => a.id === Number(params.id)) ?? openAlerts[0]
    return HttpResponse.json({ ...alert, acknowledged_at: '2026-08-04T00:00:00+00:00', acknowledged_by: 1 })
  }),
```

- [ ] **Step 3: Write the failing frontend tests**

Add to `frontend/src/pages/AlertsPage.test.tsx`:

```ts
  it('acknowledges an alert and updates the row without navigating', async () => {
    render(harness())
    await screen.findByText(/m1 critical/i)

    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')
    const button = within(row).getByRole('button', { name: /acknowledge/i })
    await userEvent.click(button)

    expect(await within(row).findByText(/acknowledged/i)).toBeInTheDocument()
    // Clicking the button must not trigger the row's navigate-on-click handler.
    expect(screen.queryByText(/selected machine: m1/i)).not.toBeInTheDocument()
  })

  it('does not show an acknowledge button for an already-acknowledged alert', async () => {
    server.use(
      http.get('/api/alerts', () =>
        HttpResponse.json([{ ...openAlertsFixtureAcknowledged() }]),
      ),
    )
    render(harness())
    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')
    expect(within(row).queryByRole('button', { name: /acknowledge/i })).not.toBeInTheDocument()
  })
```

Add this helper near the top of the same test file (after imports), returning an already-acknowledged variant of the fixture alert:

```ts
function openAlertsFixtureAcknowledged(): Alert {
  return {
    id: 2,
    machine_id: 'm1',
    opened_at: '2003-10-22T13:00:00+00:00',
    resolved_at: null,
    severity: 'high',
    health_state: 'critical',
    probable_cause: 'bearing_wear',
    message: 'm1 critical',
    status: 'open',
    source: 'ml',
    acknowledged_at: '2026-08-04T00:00:00+00:00',
    acknowledged_by: 1,
  }
}
```

Add to `frontend/src/realtime/LiveEventsProvider.test.tsx` (after the `'raises a success toast for a resolved alert'` test):

```ts
  it('raises a warning toast for an acknowledged alert', async () => {
    renderProvider()
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    socket.emitMessage({
      type: 'alert_acknowledged',
      machine_id: 'm1',
      alert: { severity: 'high', health_state: 'critical' },
    })

    expect(await screen.findByText('alert_acknowledged')).toBeInTheDocument()
    expect(toast.warning).toHaveBeenCalled()
  })
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/AlertsPage.test.tsx src/realtime/LiveEventsProvider.test.tsx`
Expected: FAIL — no Acknowledge button exists yet; `describe()` falls through to `default` (still produces text, but let's confirm the specific `alert_acknowledged` case doesn't exist by checking the toast variant assertion... actually `lastEvent.type` display will still pass since `Probe` just echoes `event.type`). The real failure is in `AlertsPage.test.tsx` — no button, so `getByRole('button', { name: /acknowledge/i })` throws.

- [ ] **Step 5: Implement `describe()` case in `LiveEventsProvider.tsx`**

In `frontend/src/realtime/LiveEventsProvider.tsx`, the `describe()` switch (lines 28-39) currently is:

```ts
function describe(event: LiveEvent): string {
  switch (event.type) {
    case 'alert_created':
      return `${event.machine_id}: new ${event.alert.severity} alert (${event.alert.health_state})`
    case 'alert_escalated':
      return `${event.machine_id}: alert escalated to ${event.alert.severity}`
    case 'alert_resolved':
      return `${event.machine_id}: alert resolved`
    default:
      return `${event.machine_id}: update`
  }
}
```

Add a case before `default`:

```ts
    case 'alert_acknowledged':
      return `${event.machine_id}: alert acknowledged`
```

This falls into the `toast.warning` branch in `onmessage` (only `alert_resolved` uses `toast.success`), matching the new test's expectation.

- [ ] **Step 6: Implement the Acknowledge button in `AlertsPage.tsx`**

In `frontend/src/pages/AlertsPage.tsx`, add an `acknowledging` state and handler, and an Actions column. Full updated file:

```tsx
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Alert } from '../api/types'
import { api } from '../api/client'
import { healthLabel, severityClasses } from '../components/healthStyles'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

type StatusFilter = 'all' | 'open' | 'resolved'
type SortKey = 'opened_at' | 'severity'

const SEVERITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1, unknown: 0 }

export function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [status, setStatus] = useState<StatusFilter>('open')
  const [sortKey, setSortKey] = useState<SortKey>('opened_at')
  const [error, setError] = useState<string | null>(null)
  const [acknowledgingId, setAcknowledgingId] = useState<number | null>(null)
  const navigate = useNavigate()
  const { lastEvent } = useLiveEvents()

  const load = useCallback((s: StatusFilter) => {
    setError(null)
    api
      .getAlerts(s === 'all' ? undefined : s)
      .then(setAlerts)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load alerts'))
  }, [])

  useEffect(() => {
    load(status)
  }, [load, status])

  useEffect(() => {
    if (lastEvent) load(status)
  }, [lastEvent, load, status])

  const sorted = useMemo(() => {
    const copy = [...alerts]
    if (sortKey === 'severity') {
      copy.sort((a, b) => (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0))
    } else {
      copy.sort((a, b) => (a.opened_at < b.opened_at ? 1 : -1))
    }
    return copy
  }, [alerts, sortKey])

  async function handleAcknowledge(e: React.MouseEvent, alertId: number) {
    e.stopPropagation()
    setAcknowledgingId(alertId)
    try {
      await api.acknowledgeAlert(alertId)
      load(status)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to acknowledge alert')
    } finally {
      setAcknowledgingId(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-text">Alerts</h1>
          <p className="text-xs text-text-muted">{alerts.length} alerts matching this filter</p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1 text-text-muted">
            Status
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as StatusFilter)}
              className="rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
            >
              <option value="open">Open</option>
              <option value="resolved">Resolved</option>
              <option value="all">All</option>
            </select>
          </label>
          <label className="flex items-center gap-1 text-text-muted">
            Sort by
            <select
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              className="rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
            >
              <option value="opened_at">Newest</option>
              <option value="severity">Severity</option>
            </select>
          </label>
        </div>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      {sorted.length === 0 ? (
        <p className="py-6 text-sm text-text-muted">No alerts match this filter.</p>
      ) : (
        <div className="glass overflow-x-auto rounded-2xl">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface">
              <tr className="text-xs uppercase text-text-muted">
                <th className="px-3 py-2">Machine</th>
                <th className="px-3 py-2">Severity</th>
                <th className="px-3 py-2">Health state</th>
                <th className="px-3 py-2">Message</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Opened</th>
                <th className="px-3 py-2">Resolved</th>
                <th className="px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((a) => (
                <tr
                  key={a.id}
                  onClick={() => navigate(`/machines/${encodeURIComponent(a.machine_id)}`)}
                  className="cursor-pointer border-t border-border bg-surface hover:bg-surface-hover"
                >
                  <td className="px-3 py-2 font-medium text-text">{a.machine_id}</td>
                  <td className="px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${severityClasses(a.severity)}`}>
                      {a.severity}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-text-muted">{healthLabel(a.health_state)}</td>
                  <td className="px-3 py-2 text-text-muted">{a.message ?? '—'}</td>
                  <td className="px-3 py-2 text-text-muted">{a.status}</td>
                  <td className="px-3 py-2 text-text-muted">{a.opened_at}</td>
                  <td className="px-3 py-2 text-text-muted">{a.resolved_at ?? '—'}</td>
                  <td className="px-3 py-2">
                    {a.acknowledged_at ? (
                      <span className="text-xs text-text-muted">Acknowledged</span>
                    ) : (
                      <button
                        type="button"
                        disabled={acknowledgingId === a.id}
                        onClick={(e) => handleAcknowledge(e, a.id)}
                        className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-text backdrop-blur hover:bg-surface-hover disabled:opacity-50"
                      >
                        {acknowledgingId === a.id ? 'Acknowledging…' : 'Acknowledge'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/AlertsPage.test.tsx src/realtime/LiveEventsProvider.test.tsx`
Expected: PASS (all tests including the 2 new ones per file)

Run full frontend suite to check for regressions: `cd frontend && npx vitest run`
Expected: PASS (all tests)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/AlertsPage.tsx frontend/src/pages/AlertsPage.test.tsx frontend/src/realtime/LiveEventsProvider.tsx frontend/src/realtime/LiveEventsProvider.test.tsx frontend/src/test/fixtures.ts frontend/src/test/server.ts
git commit -m "Add acknowledge button to AlertsPage and alert_acknowledged toast"
```

---

### Task 7: `MachineDetail.tsx` — avg acknowledgement fact

**Files:**
- Modify: `frontend/src/components/MachineDetail.tsx:112-125`

**Interfaces:**
- Consumes: `maintenance.avg_alert_acknowledgement_hours` (Task 4/5)

- [ ] **Step 1: Edit the Maintenance facts block**

In `frontend/src/components/MachineDetail.tsx`, the maintenance `<dl>` (lines 112-125) currently is:

```tsx
          <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <Fact label="Last serviced" value={maintenance.last_maintenance_at ?? 'never'} mono />
            <Fact
              label="Days since"
              value={maintenance.days_since_last_maintenance != null ? String(maintenance.days_since_last_maintenance) : '—'}
              mono
            />
            <Fact label="Records" value={String(maintenance.completed_maintenance_count)} mono />
            <Fact
              label="Avg resolution (h)"
              value={maintenance.avg_alert_resolution_hours != null ? maintenance.avg_alert_resolution_hours.toFixed(2) : '—'}
              mono
            />
          </dl>
```

Add a fifth `<Fact>` after "Avg resolution (h)":

```tsx
          <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <Fact label="Last serviced" value={maintenance.last_maintenance_at ?? 'never'} mono />
            <Fact
              label="Days since"
              value={maintenance.days_since_last_maintenance != null ? String(maintenance.days_since_last_maintenance) : '—'}
              mono
            />
            <Fact label="Records" value={String(maintenance.completed_maintenance_count)} mono />
            <Fact
              label="Avg resolution (h)"
              value={maintenance.avg_alert_resolution_hours != null ? maintenance.avg_alert_resolution_hours.toFixed(2) : '—'}
              mono
            />
            <Fact
              label="Avg acknowledgement (h)"
              value={maintenance.avg_alert_acknowledgement_hours != null ? maintenance.avg_alert_acknowledgement_hours.toFixed(2) : '—'}
              mono
            />
          </dl>
```

- [ ] **Step 2: Run the existing MachineDetail test (if any) and full frontend suite**

Run: `grep -rl "MachineDetail" frontend/src --include=*.test.tsx`

If a `MachineDetail.test.tsx` exists, run it directly; otherwise run the full suite since `MachineDetail` is exercised indirectly via a machine-detail page test:

Run: `cd frontend && npx vitest run`
Expected: PASS (all tests; `machineDetail` fixture already has `avg_alert_acknowledgement_hours: null` from Task 6 Step 1, so this renders `—`)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MachineDetail.tsx
git commit -m "Show avg acknowledgement hours on machine detail page"
```

---

### Task 8: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `python -m pytest tests/ -v`
Expected: PASS — all pre-existing tests plus Task 1 (2), Task 3 (5), Task 4 (2) new tests. Total should be 61 + 9 = 70 backend tests passing.

- [ ] **Step 2: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS — all pre-existing tests plus Task 6 (2) and any incidental fixture-driven assertions. Total should be 107 + 2 = 109 frontend tests passing (exact count may vary slightly if other suites share fixtures).

- [ ] **Step 3: Typecheck the frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Update TODO.md to check off item 1**

In `c:\projects\MaintainIQ\TODO.md`, change all five checkboxes under "## 1. Alert acknowledge workflow" from `- [ ]` to `- [x]`.

- [ ] **Step 5: Commit**

```bash
git add TODO.md
git commit -m "Mark alert acknowledge workflow TODO items complete"
```
