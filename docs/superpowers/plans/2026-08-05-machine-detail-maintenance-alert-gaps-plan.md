# Machine Detail Maintenance/Alert Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Machine Detail page's Alerts panel to the maintenance-logging form, add an `alert_id`/`type` link on maintenance records, tie technician to the logged-in user, remove the dead `onLogged` plumbing in favor of a toast, and cap/paginate maintenance history.

**Architecture:** Two additive, nullable SQLite columns (`alert_id`, `type`) on `maintenance_records`, threaded through `src/maintenance/records.py` → `src/api/schemas.py` → the FastAPI routes → the TS API client → React components. `GET /machines/{id}` caps its embedded history at 10 rows; `GET /maintenance/{machine_id}` gains `limit`/`offset` query params for a "Load more" button. `AlertsPanel.onSelect` changes from `(machineId: string) => void` to `(alert: Alert) => void` so `MachineDetail` can hold the selected alert and pass it into `MaintenanceForm` as `linkedAlert`.

**Tech Stack:** FastAPI + SQLite (backend), React + TypeScript + Vite (frontend), pytest (backend tests), Vitest + Testing Library + MSW (frontend tests), `sonner` for toasts.

## Global Constraints

- No backfill needed for the new columns — both are nullable, existing rows stay valid with `NULL`.
- Follow the idempotent-migration pattern already established for `alerts.acknowledged_at`/`acknowledged_by` in `src/storage/db.py`: add the columns to the `SCHEMA` string (for fresh DBs / `tests/conftest.py`'s `_populate()`, which calls `conn.executescript(SCHEMA)` directly) **and** guard `ALTER TABLE ... ADD COLUMN` in `init_schema()` with `try`/`except sqlite3.OperationalError` (for upgrading existing DBs).
- `get_history(conn, machine_id, limit=None, offset=0)` must default `limit=None` (unbounded) so `src/kpi/calculations.py` (which calls `get_history` today) and any other existing caller keep today's behavior unless they opt into pagination.
- Cost / parts_used fields and a dedicated full-history modal/route are explicitly out of scope (per the committed spec).
- Every test file that renders a component calling `useAuth()` (directly or via a child) must wrap the render in `<AuthProvider>` — `useAuth()` throws outside it.

---

### Task 1: DB schema — `alert_id`/`type` columns on `maintenance_records`

**Files:**
- Modify: `src/storage/db.py:88-95` (SCHEMA `maintenance_records` block), `src/storage/db.py:139-146` (`init_schema`)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `maintenance_records` table gains `alert_id INTEGER REFERENCES alerts(id)` and `type TEXT CHECK(type IN ('preventive','corrective'))`, both nullable. Consumed by Task 2 (`src/maintenance/records.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py` (needs `import pytest` added alongside the existing `import sqlite3`):

```python
import sqlite3

import pytest

from src.storage.db import SCHEMA, init_schema
```

Append at the end of the file:

```python
def test_maintenance_records_table_has_alert_id_and_type_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(maintenance_records)")}
    assert "alert_id" in cols
    assert "type" in cols


def test_init_schema_is_idempotent_for_maintenance_records_on_existing_db():
    """A DB created before this change (no alert_id/type columns) must still
    work after init_schema runs again — the guarded ALTER TABLE must not raise."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE maintenance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            performed_at TEXT NOT NULL,
            description TEXT,
            technician TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()

    init_schema(conn)  # must not raise, and must add the new columns

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(maintenance_records)")}
    assert "alert_id" in cols
    assert "type" in cols


def test_maintenance_records_type_check_constraint():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute(
        "INSERT INTO machines (machine_id, source_test, bearing, is_documented_failure) VALUES ('m1', 't', 'b', 0)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO maintenance_records (machine_id, performed_at, created_at, type)
               VALUES ('m1', '2026-01-01T00:00:00', '2026-01-01T00:00:00', 'bogus')"""
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: `test_maintenance_records_table_has_alert_id_and_type_columns` and `test_init_schema_is_idempotent_for_maintenance_records_on_existing_db` FAIL with `AssertionError` (columns missing); `test_maintenance_records_type_check_constraint` FAILS because the INSERT succeeds (no CHECK constraint yet, so no `IntegrityError` is raised).

- [ ] **Step 3: Update the schema and migration**

In `src/storage/db.py`, change the `maintenance_records` block inside `SCHEMA`:

```sql
-- Schema only for now: M5 populates this via a dashboard form/API.
CREATE TABLE IF NOT EXISTS maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    performed_at TEXT NOT NULL,
    description TEXT,
    technician TEXT,
    created_at TEXT NOT NULL,
    alert_id INTEGER REFERENCES alerts(id),
    type TEXT CHECK(type IN ('preventive','corrective'))
);
```

And update `init_schema`:

```python
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for column in ("acknowledged_at TEXT", "acknowledged_by INTEGER"):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB created via SCHEMA above)
    for column in (
        "alert_id INTEGER REFERENCES alerts(id)",
        "type TEXT CHECK(type IN ('preventive','corrective'))",
    ):
        try:
            conn.execute(f"ALTER TABLE maintenance_records ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB created via SCHEMA above)
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (5 tests: 2 pre-existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/storage/db.py tests/test_db.py
git commit -m "feat: add alert_id/type columns to maintenance_records"
```

---

### Task 2: `src/maintenance/records.py` — alert linking, `type`, pagination

**Files:**
- Modify: `src/maintenance/records.py`
- Test: `tests/test_maintenance.py`

**Interfaces:**
- Consumes: Task 1's `alert_id`/`type` columns.
- Produces: `log_maintenance(conn, machine_id, performed_at, description=None, technician=None, alert_id=None, type=None) -> dict` (dict now includes `alert_id`, `type` keys); `get_history(conn, machine_id, limit=None, offset=0) -> list[dict]` (each dict now includes `alert_id`, `type` keys). Consumed by Task 3 (`src/api/routes/maintenance.py`, `src/api/routes/machines.py`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_maintenance.py`:

```python
def test_alert_id_accepted_when_it_belongs_to_the_machine(conn):
    rec = m.log_maintenance(conn, "m1", "2026-07-20T10:00:00Z", alert_id=2, type="corrective")
    assert rec["alert_id"] == 2
    assert rec["type"] == "corrective"


def test_alert_id_rejected_when_unknown(conn):
    with pytest.raises(m.MaintenanceError, match="does not belong"):
        m.log_maintenance(conn, "m1", "2026-07-20T10:00:00Z", alert_id=9999)


def test_alert_id_rejected_when_belongs_to_a_different_machine(conn):
    with pytest.raises(m.MaintenanceError, match="does not belong"):
        m.log_maintenance(conn, "m2", "2026-07-20T10:00:00Z", alert_id=2)


def test_get_history_pagination(conn):
    for i in range(3):
        m.log_maintenance(conn, "m1", f"2026-07-{10 + i:02d}T10:00:00Z", description=str(i))
    page1 = m.get_history(conn, "m1", limit=2, offset=0)
    page2 = m.get_history(conn, "m1", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1


def test_get_history_unbounded_when_limit_omitted(conn):
    for i in range(3):
        m.log_maintenance(conn, "m1", f"2026-07-{10 + i:02d}T10:00:00Z")
    assert len(m.get_history(conn, "m1")) == 3
```

(Note: fixture alert `id=2` is `m1`'s open "m1 critical" alert, `id=1` is `m1`'s resolved alert — see `tests/conftest.py:72-83`. `m2` has zero alerts, which is what makes `test_alert_id_rejected_when_belongs_to_a_different_machine` a real cross-machine check.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_maintenance.py -v`
Expected: FAIL — `log_maintenance()` raises `TypeError: got an unexpected keyword argument 'alert_id'`; `get_history()` raises `TypeError: got an unexpected keyword argument 'limit'`.

- [ ] **Step 3: Implement**

Replace `log_maintenance` and `get_history` in `src/maintenance/records.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_maintenance.py -v`
Expected: PASS (13 tests: 8 pre-existing + 5 new)

- [ ] **Step 5: Commit**

```bash
git add src/maintenance/records.py tests/test_maintenance.py
git commit -m "feat: validate alert_id ownership and paginate maintenance history"
```

---

### Task 3: API schemas + routes — `alert_id`/`type` passthrough, pagination, history cap

**Files:**
- Modify: `src/api/schemas.py:49-74` (`MaintenanceRecord`, `MaintenanceCreate`), `src/api/routes/maintenance.py`, `src/api/routes/machines.py:48`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Task 2's `log_maintenance`/`get_history` signatures.
- Produces: `GET /api/maintenance/{machine_id}?limit=&offset=`, `POST /api/maintenance` round-tripping `alert_id`/`type`, `GET /api/machines/{id}` returning at most 10 `maintenance_history` rows. Consumed by Task 4 (`frontend/src/api/types.ts`, `client.ts`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_maintenance_post_with_alert_and_type_round_trips(client):
    resp = client.post("/api/maintenance", json={
        "machine_id": "m1",
        "performed_at": "2026-07-20T10:00:00",
        "alert_id": 2,
        "type": "corrective",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["alert_id"] == 2
    assert body["type"] == "corrective"


def test_maintenance_post_alert_from_other_machine_400(client):
    resp = client.post("/api/maintenance", json={
        "machine_id": "m2", "performed_at": "2026-07-20T10:00:00", "alert_id": 2,
    })
    assert resp.status_code == 400


def test_maintenance_history_pagination(client):
    for i in range(3):
        client.post("/api/maintenance", json={
            "machine_id": "m1", "performed_at": f"2026-07-{10 + i:02d}T10:00:00",
        })
    page1 = client.get("/api/maintenance/m1?limit=2&offset=0").json()
    page2 = client.get("/api/maintenance/m1?limit=2&offset=2").json()
    assert len(page1) == 2
    assert len(page2) == 1


def test_machine_detail_maintenance_history_capped_at_ten(client):
    for i in range(12):
        client.post("/api/maintenance", json={
            "machine_id": "m1", "performed_at": f"2026-07-{i + 1:02d}T10:00:00",
        })
    resp = client.get("/api/machines/m1")
    assert len(resp.json()["maintenance_history"]) == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`
Expected: FAIL — `test_maintenance_post_with_alert_and_type_round_trips` gets a 422 (unrecognized fields rejected by Pydantic... actually Pydantic ignores unknown fields by default, so this returns 201 but `body["alert_id"]` raises `KeyError`/assertion fails since the response model doesn't include it); `test_maintenance_post_alert_from_other_machine_400` gets 201 instead of 400 (no validation yet); `test_machine_detail_maintenance_history_capped_at_ten` fails (`len == 12`, not `10`).

- [ ] **Step 3: Implement**

In `src/api/schemas.py`, update both models:

```python
class MaintenanceRecord(BaseModel):
    id: int
    machine_id: str
    performed_at: str
    description: Optional[str] = None
    technician: Optional[str] = None
    created_at: str
    alert_id: Optional[int] = None
    type: Optional[Literal["preventive", "corrective"]] = None


class MaintenanceCreate(BaseModel):
    machine_id: str
    performed_at: str
    description: Optional[str] = None
    technician: Optional[str] = None
    alert_id: Optional[int] = None
    type: Optional[Literal["preventive", "corrective"]] = None
```

Replace `src/api/routes/maintenance.py`:

```python
"""Maintenance endpoints: history read + log-a-record write (M5)."""
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_db
from src.api.schemas import MaintenanceCreate, MaintenanceRecord
from src.maintenance import records as maintenance

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.get("/{machine_id}", response_model=list[MaintenanceRecord])
def get_maintenance_history(
    machine_id: str,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    return maintenance.get_history(db, machine_id, limit=limit, offset=offset)


@router.post("", response_model=MaintenanceRecord, status_code=201)
def log_maintenance(payload: MaintenanceCreate, db=Depends(get_db)):
    try:
        return maintenance.log_maintenance(
            db,
            machine_id=payload.machine_id,
            performed_at=payload.performed_at,
            description=payload.description,
            technician=payload.technician,
            alert_id=payload.alert_id,
            type=payload.type,
        )
    except maintenance.MaintenanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

In `src/api/routes/machines.py`, change line 48:

```python
    history = [MaintenanceRecord(**rec) for rec in maintenance.get_history(db, machine_id, limit=10)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: PASS (all tests, including the 4 new ones)

- [ ] **Step 5: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS (all backend tests green — confirms `src/kpi/calculations.py`'s unbounded `get_history` call is unaffected)

- [ ] **Step 6: Commit**

```bash
git add src/api/schemas.py src/api/routes/maintenance.py src/api/routes/machines.py tests/test_api.py
git commit -m "feat: expose alert_id/type on maintenance API and cap/paginate history"
```

---

### Task 4: Frontend types + API client — `alert_id`/`type`, `limit`/`offset`

**Files:**
- Modify: `frontend/src/api/types.ts:42-56` (`MaintenanceRecord`, `MaintenanceCreate`), `frontend/src/api/client.ts:73-74` (`getMaintenanceHistory`)
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: Task 3's API contract.
- Produces: `MaintenanceRecord`/`MaintenanceCreate` gain `alert_id?: number | null`, `type?: 'preventive' | 'corrective' | null`; `api.getMaintenanceHistory(id: string, opts?: { limit?: number; offset?: number }) => Promise<MaintenanceRecord[]>`. Consumed by Task 5, 6, 7, 9.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/api/client.test.ts` (inside the existing `describe('api client', ...)` block):

```ts
  it('fetches maintenance history with limit/offset as query params', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/maintenance/:id', ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json([])
      }),
    )
    await api.getMaintenanceHistory('m1', { limit: 5, offset: 10 })
    expect(capturedUrl).toContain('limit=5')
    expect(capturedUrl).toContain('offset=10')
  })

  it('defaults maintenance history limit/offset when omitted', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/maintenance/:id', ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json([])
      }),
    )
    await api.getMaintenanceHistory('m1')
    expect(capturedUrl).toContain('limit=20')
    expect(capturedUrl).toContain('offset=0')
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/api/client.test.ts`
Expected: FAIL — `api.getMaintenanceHistory` only accepts one argument today, so the request URL has no `limit`/`offset` query params (`capturedUrl` doesn't contain them). TypeScript compilation also fails on the second argument since the current signature is `(id: string)`.

- [ ] **Step 3: Implement**

In `frontend/src/api/types.ts`, update both interfaces:

```ts
export interface MaintenanceRecord {
  id: number
  machine_id: string
  performed_at: string
  description: string | null
  technician: string | null
  created_at: string
  alert_id?: number | null
  type?: 'preventive' | 'corrective' | null
}

export interface MaintenanceCreate {
  machine_id: string
  performed_at: string
  description?: string | null
  technician?: string | null
  alert_id?: number | null
  type?: 'preventive' | 'corrective' | null
}
```

In `frontend/src/api/client.ts`, replace `getMaintenanceHistory`:

```ts
  getMaintenanceHistory: (id: string, { limit = 20, offset = 0 }: { limit?: number; offset?: number } = {}) =>
    request<MaintenanceRecord[]>(`/maintenance/${encodeURIComponent(id)}?limit=${limit}&offset=${offset}`),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/api/client.test.ts`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "feat: add alert_id/type and pagination to the maintenance API client"
```

---

### Task 5: `AlertsPanel` — `onSelect(alert)` + selected-state indicator

**Files:**
- Modify: `frontend/src/components/AlertsPanel.tsx`
- Test: `frontend/src/components/AlertsPanel.test.tsx`

**Interfaces:**
- Produces: `Props { alerts: Alert[]; onSelect: (alert: Alert) => void; selectedAlertId?: number | null }`. Consumed by Task 7 (`MachineDetail.tsx`).

- [ ] **Step 1: Write the failing tests**

Replace `frontend/src/components/AlertsPanel.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AlertsPanel } from './AlertsPanel'
import { openAlerts } from '../test/fixtures'

describe('AlertsPanel', () => {
  it('renders each alert message', () => {
    render(<AlertsPanel alerts={openAlerts} onSelect={() => {}} />)
    expect(screen.getByText('m1 critical')).toBeInTheDocument()
  })

  it('shows an empty state when there are no alerts', () => {
    render(<AlertsPanel alerts={[]} onSelect={() => {}} />)
    expect(screen.getByText(/no open alerts/i)).toBeInTheDocument()
  })

  it('calls onSelect with the full alert object when an alert is clicked', async () => {
    const onSelect = vi.fn()
    render(<AlertsPanel alerts={openAlerts} onSelect={onSelect} />)
    await userEvent.click(screen.getByText('m1 critical'))
    expect(onSelect).toHaveBeenCalledWith(openAlerts[0])
  })

  it('visually marks the selected alert', () => {
    render(<AlertsPanel alerts={openAlerts} onSelect={() => {}} selectedAlertId={openAlerts[0].id} />)
    expect(screen.getByRole('button', { name: /m1 critical/i })).toHaveClass('ring-2')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/components/AlertsPanel.test.tsx`
Expected: FAIL — `onSelect` is called with `'m1'` (a string), not `openAlerts[0]` (an object), so the third test fails; the fourth test fails because `selectedAlertId` doesn't exist and no `ring-2` class is ever applied.

- [ ] **Step 3: Implement**

Replace `frontend/src/components/AlertsPanel.tsx`:

```tsx
import { AnimatePresence, motion } from 'framer-motion'
import type { Alert } from '../api/types'
import { severityClasses } from './healthStyles'

interface Props {
  alerts: Alert[]
  onSelect: (alert: Alert) => void
  selectedAlertId?: number | null
}

export function AlertsPanel({ alerts, onSelect, selectedAlertId = null }: Props) {
  if (alerts.length === 0) {
    return <p className="py-4 text-sm text-text-muted">No open alerts.</p>
  }

  return (
    <ul className="space-y-2">
      <AnimatePresence initial={false}>
        {alerts.map((a) => (
          <motion.li key={a.id} layout initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
            <motion.button
              type="button"
              onClick={() => onSelect(a)}
              className={`glass w-full overflow-hidden rounded-xl border-l-2 p-3 text-left transition hover:border-white/20 hover:bg-white/[0.07] ${a.id === selectedAlertId ? 'ring-2 ring-accent/60' : ''}`}
              style={{ borderLeftColor: 'var(--color-' + severityBorder(a.severity) + ')' }}
              initial={{ backgroundColor: 'rgba(212,175,55,0.14)' }}
              animate={{ backgroundColor: 'rgba(19,19,19,1)' }}
              transition={{ duration: 1.1, ease: 'easeOut' }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-text">{a.message ?? `${a.machine_id}: ${a.health_state}`}</span>
                <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs ${severityClasses(a.severity)}`}>
                  {a.severity}
                </span>
              </div>
              <div className="mt-1 font-mono text-xs text-text-muted">
                {a.status} · opened {a.opened_at}
              </div>
            </motion.button>
          </motion.li>
        ))}
      </AnimatePresence>
    </ul>
  )
}

function severityBorder(severity: string): string {
  if (severity === 'high') return 'critical'
  if (severity === 'medium') return 'faulty'
  if (severity === 'low') return 'degrading'
  return 'unknown'
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/components/AlertsPanel.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AlertsPanel.tsx frontend/src/components/AlertsPanel.test.tsx
git commit -m "feat: pass the full alert object through AlertsPanel.onSelect"
```

---

### Task 6: `MaintenanceForm` — `linkedAlert` chip, `type` field, technician pre-fill

**Files:**
- Modify: `frontend/src/components/MaintenanceForm.tsx`
- Test: `frontend/src/components/MaintenanceForm.test.tsx`

**Interfaces:**
- Consumes: `useAuth()` from `frontend/src/auth/AuthContext.tsx` (`{ user, loading, login, logout }`); Task 4's `MaintenanceCreate` shape; Task 5's `Alert` type (unchanged, just imported).
- Produces: `Props { machineId: string; onSubmit: (payload: MaintenanceCreate) => Promise<unknown>; linkedAlert?: Alert | null }`. Consumed by Task 7 (`MachineDetail.tsx`) and used as-is (no `linkedAlert`) by `frontend/src/pages/MaintenancePage.tsx`.

- [ ] **Step 1: Write the failing tests**

Replace `frontend/src/components/MaintenanceForm.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthProvider } from '../auth/AuthContext'
import { MaintenanceForm } from './MaintenanceForm'
import { openAlerts } from '../test/fixtures'

function harness(props: Parameters<typeof MaintenanceForm>[0]) {
  return (
    <AuthProvider>
      <MaintenanceForm {...props} />
    </AuthProvider>
  )
}

describe('MaintenanceForm', () => {
  it('submits the entered values with the machine id, default type, and no linked alert', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit }))

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.type(screen.getByLabelText(/description/i), 'greased bearing')
    await userEvent.clear(screen.getByLabelText(/technician/i))
    await userEvent.type(screen.getByLabelText(/technician/i), 'tech1')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(onSubmit).toHaveBeenCalledWith({
      machine_id: 'm1',
      performed_at: '2026-07-20T10:00:00',
      description: 'greased bearing',
      technician: 'tech1',
      alert_id: null,
      type: 'preventive',
    })
  })

  it('pre-fills the technician field from the authenticated user', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit }))

    expect(await screen.findByDisplayValue('Ada Admin')).toBeInTheDocument()
  })

  it('shows a success message after a successful submit', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit }))
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))
    expect(await screen.findByText(/logged/i)).toBeInTheDocument()
  })

  it('surfaces the error message when submit fails', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('unknown machine_id'))
    render(harness({ machineId: 'm1', onSubmit }))
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))
    expect(await screen.findByText(/unknown machine_id/)).toBeInTheDocument()
  })

  it('shows a dismissible chip and defaults to corrective when a linked alert is set', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit, linkedAlert: openAlerts[0] }))

    expect(await screen.findByText(/logging maintenance for alert #2/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/type/i)).toHaveValue('corrective')

    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(screen.queryByText(/logging maintenance for alert #2/i)).not.toBeInTheDocument()
  })

  it('includes alert_id in the payload when a linked alert is set', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit, linkedAlert: openAlerts[0] }))

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ alert_id: 2, type: 'corrective' }))
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/components/MaintenanceForm.test.tsx`
Expected: FAIL — `useAuth` doesn't exist in the component yet so the payload lacks `alert_id`/`type` and no pre-fill happens; there's no `linkedAlert` prop, no chip, and no `type` field, so `getByLabelText(/type/i)` and the chip-related assertions throw.

- [ ] **Step 3: Implement**

Replace `frontend/src/components/MaintenanceForm.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { Alert, MaintenanceCreate } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { Input, Label, Select } from './ui/input'
import { Button } from './ui/button'

interface Props {
  machineId: string
  onSubmit: (payload: MaintenanceCreate) => Promise<unknown>
  linkedAlert?: Alert | null
}

type Status = { kind: 'idle' | 'ok' | 'err'; message?: string }
type MaintenanceType = 'preventive' | 'corrective'

export function MaintenanceForm({ machineId, onSubmit, linkedAlert = null }: Props) {
  const { user } = useAuth()
  const [when, setWhen] = useState('')
  const [description, setDescription] = useState('')
  const [technician, setTechnician] = useState('')
  const [type, setType] = useState<MaintenanceType>(linkedAlert ? 'corrective' : 'preventive')
  const [chipDismissed, setChipDismissed] = useState(false)
  const [status, setStatus] = useState<Status>({ kind: 'idle' })
  const [busy, setBusy] = useState(false)
  const technicianTouched = useRef(false)

  useEffect(() => {
    if (!technicianTouched.current && user?.name) setTechnician(user.name)
  }, [user])

  useEffect(() => {
    setType(linkedAlert ? 'corrective' : 'preventive')
    setChipDismissed(false)
  }, [linkedAlert])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setStatus({ kind: 'idle' })
    try {
      // datetime-local yields "YYYY-MM-DDTHH:mm" (no seconds); append ':00'
      // to match the backend's ISO expectation.
      await onSubmit({
        machine_id: machineId,
        performed_at: when ? `${when}:00` : '',
        description: description || null,
        technician: technician || null,
        alert_id: linkedAlert?.id ?? null,
        type,
      })
      setStatus({ kind: 'ok', message: 'Logged.' })
      setWhen('')
      setDescription('')
    } catch (err) {
      setStatus({ kind: 'err', message: err instanceof Error ? err.message : 'Failed to log maintenance' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mt-3 grid gap-2">
      <strong className="text-sm text-text">Log a maintenance record</strong>
      {linkedAlert && !chipDismissed && (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-accent/30 bg-accent/10 px-3 py-1.5 text-xs text-text">
          <span>
            Logging maintenance for alert #{linkedAlert.id}: {linkedAlert.message ?? linkedAlert.health_state}
          </span>
          <button type="button" onClick={() => setChipDismissed(true)} className="text-text-muted hover:text-text" aria-label="Dismiss">
            ×
          </button>
        </div>
      )}
      <Label>
        When
        <Input type="datetime-local" required value={when} onChange={(e) => setWhen(e.target.value)} />
      </Label>
      <Label>
        Description
        <Input
          type="text"
          value={description}
          placeholder="e.g. re-greased bearing"
          onChange={(e) => setDescription(e.target.value)}
        />
      </Label>
      <Label>
        Technician
        <Input
          type="text"
          value={technician}
          onChange={(e) => {
            technicianTouched.current = true
            setTechnician(e.target.value)
          }}
        />
      </Label>
      <Label>
        Type
        <Select value={type} onChange={(e) => setType(e.target.value as MaintenanceType)}>
          <option value="preventive">Preventive</option>
          <option value="corrective">Corrective</option>
        </Select>
      </Label>
      <Button type="submit" variant="accent" size="sm" disabled={busy} className="justify-self-start">
        Log maintenance
      </Button>
      {status.kind !== 'idle' && (
        <p className={`text-xs ${status.kind === 'ok' ? 'text-healthy' : 'text-critical'}`}>{status.message}</p>
      )}
    </form>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/components/MaintenanceForm.test.tsx`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MaintenanceForm.tsx frontend/src/components/MaintenanceForm.test.tsx
git commit -m "feat: link maintenance records to alerts, add type field, pre-fill technician"
```

---

### Task 7: `MachineDetail` — selected alert, toast, remove `onLogged`, Type column, Load more

**Files:**
- Modify: `frontend/src/components/MachineDetail.tsx`
- Modify: `frontend/src/test/fixtures.ts` (new pagination fixtures)
- Test: `frontend/src/components/MachineDetail.test.tsx`

**Interfaces:**
- Consumes: Task 4's `api.getMaintenanceHistory(id, opts)`, Task 5's `AlertsPanel` props, Task 6's `MaintenanceForm` `linkedAlert` prop.
- Produces: `Props { machineId: string }` (no more `onLogged`). Consumed by Task 8 (`MachineDetailPage.tsx`).

- [ ] **Step 1: Add pagination fixtures**

In `frontend/src/test/fixtures.ts`, add `MaintenanceRecord` to the type-only import:

```ts
import type {
  Alert,
  KpiSummary,
  MachineDetail,
  MachineSummary,
  MaintenanceRecord,
  NotificationOut,
  TrendPoint,
  UserOut,
} from '../api/types'
```

Append at the end of the file:

```ts
export const maintenanceHistoryPage1: MaintenanceRecord[] = Array.from({ length: 10 }, (_, i): MaintenanceRecord => ({
  id: i + 1,
  machine_id: 'm1',
  performed_at: `2026-07-${String(i + 1).padStart(2, '0')}T10:00:00+00:00`,
  description: `service ${i + 1}`,
  technician: 'tech1',
  alert_id: null,
  type: i % 2 === 0 ? 'preventive' : 'corrective',
  created_at: '2026-07-01T00:00:00+00:00',
}))

export const maintenanceHistoryPage2: MaintenanceRecord[] = [
  {
    id: 11,
    machine_id: 'm1',
    performed_at: '2026-06-01T10:00:00+00:00',
    description: 'older service',
    technician: 'tech1',
    alert_id: null,
    type: 'preventive',
    created_at: '2026-06-01T00:00:00+00:00',
  },
]

export const machineDetailWithFullHistory: MachineDetail = {
  ...machineDetail,
  maintenance_history: maintenanceHistoryPage1,
}
```

- [ ] **Step 2: Write the failing tests**

Replace `frontend/src/components/MachineDetail.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../auth/AuthContext'
import { MachineDetail } from './MachineDetail'
import { server } from '../test/server'
import { api } from '../api/client'
import { machineDetailWithFullHistory, maintenanceHistoryPage2, openAlerts } from '../test/fixtures'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn() } }))

function harness(machineId = 'm1') {
  return (
    <AuthProvider>
      <MachineDetail machineId={machineId} />
    </AuthProvider>
  )
}

describe('MachineDetail', () => {
  it('loads and shows health facts for the machine', async () => {
    render(harness())

    expect(await screen.findByRole('heading', { name: /m1/ })).toBeInTheDocument()
    expect(screen.getAllByText(/critical/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/bearing_wear/)).toBeInTheDocument()
    // risk score surfaced somewhere
    expect(screen.getByText(/100/)).toBeInTheDocument()
  })

  it('renders a metric selector and the trend chart', async () => {
    render(harness())

    const select = await screen.findByLabelText(/metric/i)
    expect(select).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: /vibration_h_rms/i })).toBeInTheDocument()
  })

  it('refetches trends when the metric changes', async () => {
    const spy = vi.spyOn(api, 'getTrends')
    render(harness())

    const select = await screen.findByLabelText(/metric/i)
    await userEvent.selectOptions(select, 'temperature_c')

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('m1', 'temperature_c', expect.anything()),
    )
    spy.mockRestore()
  })

  it('logs maintenance and raises a success toast', async () => {
    render(harness())

    await screen.findByRole('heading', { name: /m1/ })
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.type(screen.getByLabelText(/description/i), 'greased bearing')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(await screen.findByText(/logged/i)).toBeInTheDocument()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Maintenance logged'))
  })

  it('surfaces a load error', async () => {
    server.use(
      http.get('/api/machines/:id', () =>
        HttpResponse.json({ detail: 'machine not found' }, { status: 404 }),
      ),
    )
    render(harness('mX'))
    expect(await screen.findByText(/not found/i)).toBeInTheDocument()
  })

  it('selecting an alert links it into the form and clears on submit', async () => {
    render(harness())

    await userEvent.click(await screen.findByText('m1 critical'))
    expect(await screen.findByText(/logging maintenance for alert #2/i)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    await screen.findByText(/logged/i)
    expect(screen.queryByText(/logging maintenance for alert #2/i)).not.toBeInTheDocument()
  })

  it('shows a Type column and a Load more button when a full page of history is returned', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json(machineDetailWithFullHistory)))
    render(harness())

    expect(await screen.findByRole('columnheader', { name: /type/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument()
  })

  it('appends more history rows and hides the button once a short page comes back', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json(machineDetailWithFullHistory)))
    server.use(
      http.get('/api/maintenance/:id', ({ request }) => {
        const url = new URL(request.url)
        expect(url.searchParams.get('offset')).toBe('10')
        return HttpResponse.json(maintenanceHistoryPage2)
      }),
    )
    render(harness())

    await screen.findByRole('button', { name: /load more/i })
    // 1 header row + 10 initial history rows
    expect(screen.getAllByRole('row')).toHaveLength(11)

    await userEvent.click(screen.getByRole('button', { name: /load more/i }))

    // 1 header row + 10 initial + 1 appended
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(12))
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npx vitest run src/components/MachineDetail.test.tsx`
Expected: FAIL — `MachineDetail` still requires an `onLogged` prop and calls it instead of `toast.success`; there's no alert-selection wiring, no Type column, and no Load more button, so most of the new assertions throw.

- [ ] **Step 4: Implement**

Replace `frontend/src/components/MachineDetail.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import type { Alert, MachineDetail as Detail, MaintenanceRecord, TrendPoint } from '../api/types'
import { api } from '../api/client'
import { healthClasses, healthLabel } from './healthStyles'
import { TrendChart } from './TrendChart'
import { AlertsPanel } from './AlertsPanel'
import { MaintenanceForm } from './MaintenanceForm'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Select } from './ui/input'

interface Props {
  machineId: string
}

const METRICS = [
  { value: 'vibration_h_rms', label: 'Vibration RMS' },
  { value: 'vibration_h_kurtosis', label: 'Kurtosis' },
  { value: 'vibration_h_high_band_energy_ratio', label: 'Band energy ratio' },
  { value: 'temperature_c', label: 'Temperature (°C)' },
]

const HISTORY_PAGE_SIZE = 10

export function MachineDetail({ machineId }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [metric, setMetric] = useState('vibration_h_rms')
  const [points, setPoints] = useState<TrendPoint[]>([])
  const [reloadKey, setReloadKey] = useState(0)
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null)
  const [history, setHistory] = useState<MaintenanceRecord[]>([])
  const [hasMoreHistory, setHasMoreHistory] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setError(null)
    api
      .getMachine(machineId)
      .then((d) => !cancelled && setDetail(d))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : 'Failed to load machine'))
    return () => {
      cancelled = true
    }
  }, [machineId, reloadKey])

  useEffect(() => {
    let cancelled = false
    api
      .getTrends(machineId, metric, 200)
      .then((p) => !cancelled && setPoints(p))
      .catch(() => !cancelled && setPoints([]))
    return () => {
      cancelled = true
    }
  }, [machineId, metric])

  useEffect(() => {
    if (detail) {
      setHistory(detail.maintenance_history)
      setHasMoreHistory(detail.maintenance_history.length >= HISTORY_PAGE_SIZE)
    }
  }, [detail])

  if (error) {
    return (
      <section className="rounded-2xl border border-critical/40 bg-critical/10 p-4 text-critical backdrop-blur">
        {error}
      </section>
    )
  }

  if (!detail) {
    return <section className="glass rounded-2xl p-4 text-text-muted">Loading…</section>
  }

  const { health, maintenance, alerts } = detail
  const hc = healthClasses(health.health_state)

  async function handleLog(payload: Parameters<typeof api.logMaintenance>[0]) {
    const result = await api.logMaintenance(payload)
    setReloadKey((k) => k + 1)
    setSelectedAlert(null)
    toast.success('Maintenance logged')
    return result
  }

  async function handleLoadMore() {
    setLoadingMore(true)
    try {
      const nextPage = await api.getMaintenanceHistory(machineId, { limit: HISTORY_PAGE_SIZE, offset: history.length })
      setHistory((prev) => [...prev, ...nextPage])
      setHasMoreHistory(nextPage.length >= HISTORY_PAGE_SIZE)
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <section className={`glass relative overflow-hidden rounded-2xl border-l-2 ${hc.border} p-5`}>
      <div aria-hidden className={`pointer-events-none absolute -right-10 -top-12 h-32 w-32 rounded-full opacity-20 blur-3xl ${hc.dot}`} />
      <header className="relative flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-mono text-lg font-semibold text-text">{health.machine_id}</h2>
        <Badge variant={health.health_state}>{healthLabel(health.health_state)}</Badge>
      </header>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
        <Fact label="Probable cause" value={health.probable_cause ?? '—'} />
        <Fact label="Risk score" value={String(health.risk_score)} mono />
        <Fact label="Confidence" value={health.confidence != null ? `${Math.round(health.confidence * 100)}%` : '—'} mono />
        <Fact label="Vibration" value={health.vibration_severity} />
        <Fact label="Temperature" value={health.temperature_severity} />
        <Fact label="Last reading" value={health.last_reading_at ?? '—'} mono />
      </dl>

      <div className="mt-4">
        <label className="flex items-center gap-2 text-sm text-text-muted">
          Metric
          <Select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {METRICS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </Select>
        </label>
        <div className="mt-2">
          <TrendChart metric={metric} points={points} />
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold text-text">Maintenance</h3>
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
          {maintenance.due_for_inspection && (
            <p className="mt-1 text-xs font-medium text-accent-2">Due for inspection</p>
          )}
          <MaintenanceForm machineId={health.machine_id} onSubmit={handleLog} linkedAlert={selectedAlert} />
        </div>

        <div>
          <h3 className="text-sm font-semibold text-text">Alerts</h3>
          <div className="mt-1">
            <AlertsPanel alerts={alerts} selectedAlertId={selectedAlert?.id ?? null} onSelect={setSelectedAlert} />
          </div>
        </div>
      </div>

      {history.length > 0 && (
        <div className="mt-4">
          <h3 className="text-sm font-semibold text-text">Maintenance history</h3>
          <table className="mt-1 w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase text-text-muted">
                <th className="py-1 pr-3">When</th>
                <th className="py-1 pr-3">Type</th>
                <th className="py-1 pr-3">Description</th>
                <th className="py-1">Technician</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r) => (
                <tr key={r.id} className="border-t border-border">
                  <td className="py-1 pr-3 font-mono">{r.performed_at}</td>
                  <td className="py-1 pr-3">{r.type ?? '—'}</td>
                  <td className="py-1 pr-3">{r.description ?? '—'}</td>
                  <td className="py-1">{r.technician ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {hasMoreHistory && (
            <Button type="button" variant="outline" size="sm" className="mt-2" disabled={loadingMore} onClick={handleLoadMore}>
              {loadingMore ? 'Loading…' : 'Load more'}
            </Button>
          )}
        </div>
      )}
    </section>
  )
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-text-muted">{label}</dt>
      <dd className={mono ? 'font-mono text-text' : 'text-text'}>{value}</dd>
    </div>
  )
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run src/components/MachineDetail.test.tsx`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/MachineDetail.tsx frontend/src/test/fixtures.ts frontend/src/components/MachineDetail.test.tsx
git commit -m "feat: link alerts to maintenance form, add toast, Type column, Load more"
```

---

### Task 8: `MachineDetailPage` — remove the dead `onLogged` prop

**Files:**
- Modify: `frontend/src/pages/MachineDetailPage.tsx:24`

**Interfaces:**
- Consumes: Task 7's `MachineDetail` `Props { machineId: string }` (no `onLogged`).

- [ ] **Step 1: Update the render call**

In `frontend/src/pages/MachineDetailPage.tsx`, change:

```tsx
<MachineDetail key={`${id}-${reloadCount}`} machineId={id} onLogged={() => {}} />
```

to:

```tsx
<MachineDetail key={`${id}-${reloadCount}`} machineId={id} />
```

- [ ] **Step 2: Run the existing page test suite to confirm no regressions**

Run: `npx vitest run src/pages/MachineDetailPage.test.tsx`
Expected: PASS (4 tests — this file never referenced the `onLogged` prop directly, since it was internal plumbing between this page and `MachineDetail`)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/MachineDetailPage.tsx
git commit -m "chore: remove dead onLogged prop from MachineDetailPage"
```

---

### Task 9: `MaintenancePage` — fix unbounded history fetch regression

**Files:**
- Modify: `frontend/src/pages/MaintenancePage.tsx:23`
- Test: `frontend/src/pages/MaintenancePage.test.tsx`

**Interfaces:**
- Consumes: Task 4's `api.getMaintenanceHistory(id, { limit, offset })`.

Since `GET /api/maintenance/{machine_id}` now defaults to `limit=20` (Task 3), the fleet-wide Maintenance page's unbounded `api.getMaintenanceHistory(machine.machine_id)` call (with no options) would silently start truncating any machine with more than 20 records. Pin it to a high explicit limit so this page's "show everything" behavior is preserved.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/pages/MaintenancePage.test.tsx` (inside the existing `describe('MaintenancePage', ...)` block):

```tsx
  it('requests a high history limit per machine to avoid silent truncation', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/maintenance/:id', ({ request, params }) => {
        if (params.id === 'm1') capturedUrl = request.url
        return HttpResponse.json([])
      }),
    )
    render(harness())
    await screen.findByText(/no maintenance records logged yet/i)
    expect(capturedUrl).toContain('limit=200')
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/pages/MaintenancePage.test.tsx`
Expected: FAIL — `capturedUrl` contains `limit=20` (the client's new default), not `limit=200`.

- [ ] **Step 3: Implement**

In `frontend/src/pages/MaintenancePage.tsx`, change line 23:

```tsx
      const histories = await Promise.all(m.map((machine) => api.getMaintenanceHistory(machine.machine_id, { limit: 200 })))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/pages/MaintenancePage.test.tsx`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full frontend suite**

Run: `npx vitest run`
Expected: PASS (all frontend tests green)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/MaintenancePage.tsx frontend/src/pages/MaintenancePage.test.tsx
git commit -m "fix: pin MaintenancePage's per-machine history fetch to avoid truncation"
```

---

## Final verification

- [ ] Run the full backend suite: `pytest tests/ -v` — expect all green.
- [ ] Run the full frontend suite: `npx vitest run` (from `frontend/`) — expect all green.
- [ ] Manually smoke-test in the browser: open a machine detail page, click an open alert, confirm the chip appears on the maintenance form, submit, confirm the toast fires and the chip clears, confirm the history table shows a Type column, and (if the machine has 10+ records) confirm "Load more" appends rows and disappears once exhausted.
