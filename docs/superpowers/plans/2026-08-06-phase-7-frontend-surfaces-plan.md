# Phase 7 — Frontend Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The React UI reflects XJTU RUL health, model observability, ingestion control, and reports — on real ingested data — with temperature fully removed.

**Architecture:** Extend the existing typed `fetch` client + MSW test harness with the Phase 3–6 backend endpoints (model health/telemetry, replay control, reports), purge the dead `temperature` surface and replace it with the RUL fields the backend already returns, and add three route-gated pages (Model observability, Ingestion control, Reports) wired into the AppShell nav under the existing RBAC route guards.

**Tech Stack:** React 19, react-router-dom 7, Vite 8, Tailwind CSS 4 (CSS-first, no config), Recharts 3, Vitest 4, @testing-library/react 16, MSW 2, jsdom, lucide-react, sonner. TypeScript strict.

## Global Constraints

- **XJTU-SY is canonical. No temperature anywhere in the UI** — remove every `temperature`/`temperature_c`/`temperature_severity` reference from production code, fixtures, and tests.
- **RUL is expressed in minutes.** Field names, verbatim from `src/api/schemas.py`: `predicted_rul_minutes` (number|null), `rul_estimate_kind` (string|null), `out_of_distribution` (boolean|null tri-state). Do **not** add `failure_within_horizon_probability` to `MachineSummary` — it is not on that backend model.
- **No new frontend chart library** beyond the already-installed Recharts. No new runtime dependencies at all.
- **RBAC preserved** (design spec §7): model health/telemetry and reports-list are readable by any authenticated role; ingestion start/stop is admin+supervisor only; reports of type `model_performance`/`fleet_summary` are admin+supervisor only (operators: `machine_prognostic` only).
- **TypeScript mirror stays honest against the backend** — every new type mirrors an exact response shape in `src/api/schemas.py` / the Phase 3–6 route handlers.
- **House patterns:** `credentials: 'same-origin'` on every fetch; 401 dispatches `miq:unauthorized`; errors throw `ApiError`; components style with Tailwind utility classes + the `glass`/`panel-notch` idioms; charts read colors via `useChartColors()`; toasts via `sonner`.
- **TDD, DRY, YAGNI, frequent commits.** Each task ends with `npm test` (Vitest run) green.

## Committed-Code Facts (verified against current HEAD)

- Backend `MachineSummary` (`src/api/schemas.py:15-28`) **already returns** `predicted_rul_minutes`, `rul_estimate_kind`, `out_of_distribution` and **has no** `temperature_severity`. The frontend `MachineSummary` type (`frontend/src/api/types.ts:8-20`) is stale: it still has `temperature_severity` and lacks the three RUL fields.
- Valid trend metrics on `GET /api/machines/{id}/trends` include `rul_minutes`; `temperature_c` is **not** a real metric.
- Model endpoints (`src/api/routes/model.py`) return plain dicts:
  - `GET /api/model/health` → `{status, model_version, last_inference_at, seconds_since_last_inference, active}`
  - `GET /api/model/telemetry?window_minutes` → `{window_minutes, inference_count, error_rate, latency_p50_ms, latency_p95_ms, ood_rate, warming_up_rate}`
- Ingestion endpoints (`src/api/routes/ingestion.py`): `POST /api/ingestion/replay/start` body `{machine_id, speed_multiplier?}` → `{machine_id, status:"started", speed_multiplier}` (admin+supervisor); `POST /api/ingestion/replay/stop` body `{machine_id}` → `{machine_id, status:"stopped"}` (admin+supervisor); `GET /api/ingestion/replay/status` → dict keyed by machine_id, each `{cycle, running, last_ts, replayed, error}` (any authed).
- Reports (`src/api/routes/reports.py` + `src/reports/service.py`): `POST /api/reports` and `GET /api/reports/{id}` return `{id, report_type, scope, format, period_start, period_end, generated_at, generated_by, content, summary}`; `GET /api/reports?scope=` returns the same **minus `content`** (list omits the content column). `GET /api/reports/{id}/download` returns the raw body with `Content-Disposition: attachment; filename="report-{id}.{ext}"`. Per-type RBAC: operators restricted to `machine_prognostic` (403 on elevated by id/download; list self-filters).
- `useAuth()` (`frontend/src/auth/AuthContext.tsx`) returns `{ user, loading, login, logout }`; `user.role` is `'admin'|'supervisor'|'operator'`.
- `RequireRole` (`frontend/src/auth/RequireRole.tsx`): `<RequireRole allow={Role[]} />`, bounces to `/`.
- House UI: `Card`/`CardHeader`/`CardTitle`/`CardContent` (`components/ui/card.tsx`); `MetricCard({label, value, sub?, tone?, trend?})` with `tone` ∈ `'default'|'accent'|'accent2'|'healthy'|'degrading'|'faulty'|'critical'|'unknown'` (`components/ui/metric-card.tsx`); `Select` (`components/ui/input`); `Button({variant, size})` (`components/ui/button`); `Badge({variant})` (`components/ui/badge`); `healthLabel`/`healthClasses` (`components/healthStyles.ts`).

## File Structure

**Task 1 — temperature → RUL swap**
- Modify `frontend/src/api/types.ts` (MachineSummary)
- Modify `frontend/src/test/fixtures.ts` (both machineSummaries entries)
- Modify `frontend/src/components/MachineDetail.tsx` (METRICS + Facts)
- Modify `frontend/src/components/MachineDetail.test.tsx`
- Modify `frontend/src/api/client.test.ts`
- Modify `frontend/src/components/TrendChart.test.tsx`

**Task 2 — API client + types + fixtures + MSW for new endpoints**
- Modify `frontend/src/api/types.ts` (new interfaces)
- Modify `frontend/src/api/client.ts` (refactor error handling + 9 methods)
- Modify `frontend/src/test/fixtures.ts` (new fixtures)
- Modify `frontend/src/test/server.ts` (default handlers)
- Modify `frontend/src/api/client.test.ts` (new method tests)

**Task 3 — Model observability page**
- Create `frontend/src/pages/ModelPage.tsx`
- Create `frontend/src/pages/ModelPage.test.tsx`
- Modify `frontend/src/App.tsx` (route)
- Modify `frontend/src/layout/AppShell.tsx` (nav)

**Task 4 — Ingestion control page**
- Create `frontend/src/pages/IngestionPage.tsx`
- Create `frontend/src/pages/IngestionPage.test.tsx`
- Modify `frontend/src/App.tsx` (route, admin+supervisor)
- Modify `frontend/src/layout/AppShell.tsx` (nav, admin+supervisor)

**Task 5 — Reports page**
- Create `frontend/src/pages/ReportsPage.tsx`
- Create `frontend/src/pages/ReportsPage.test.tsx`
- Modify `frontend/src/App.tsx` (route)
- Modify `frontend/src/layout/AppShell.tsx` (nav)

All commands run from `frontend/`: `cd C:/projects/MaintainIQ/frontend && npm test`. To run a single file: `npm test -- src/path/to/file.test.tsx`.

---

### Task 1: Remove temperature, surface RUL on MachineSummary + MachineDetail

**Files:**
- Modify: `frontend/src/api/types.ts:8-20`
- Modify: `frontend/src/test/fixtures.ts:15-42`
- Modify: `frontend/src/components/MachineDetail.tsx:17-22,112-119`
- Test: `frontend/src/components/MachineDetail.test.tsx`
- Test: `frontend/src/api/client.test.ts:19-30`
- Test: `frontend/src/components/TrendChart.test.tsx:12-15`

**Interfaces:**
- Consumes: nothing new.
- Produces: `MachineSummary` gains `predicted_rul_minutes: number | null`, `rul_estimate_kind: string | null`, `out_of_distribution: boolean | null` and loses `temperature_severity`. The `m1` fixture gains `predicted_rul_minutes: 42.5`, `rul_estimate_kind: 'point_estimate'`, `out_of_distribution: false`; the `m2` fixture gains `predicted_rul_minutes: 1200.0`, `rul_estimate_kind: 'point_estimate'`, `out_of_distribution: false`. Later tasks rely on these fixture values.

- [ ] **Step 1: Update the `MachineSummary` type**

In `frontend/src/api/types.ts`, replace the `MachineSummary` interface (lines 8-20) with:

```ts
export interface MachineSummary {
  machine_id: string
  health_state: HealthState
  confidence: number | null
  prediction_source: string | null
  probable_cause: string | null
  last_reading_at: string | null
  vibration_severity: Severity
  risk_score: number
  abnormal_event_count: number
  open_alert_count: number
  predicted_rul_minutes: number | null
  rul_estimate_kind: string | null
  out_of_distribution: boolean | null
}
```

- [ ] **Step 2: Update the fixtures**

In `frontend/src/test/fixtures.ts`, replace the two `machineSummaries` entries (lines 15-42) with:

```ts
export const machineSummaries: MachineSummary[] = [
  {
    machine_id: 'm1',
    health_state: 'critical',
    confidence: 0.95,
    prediction_source: 'ml',
    probable_cause: 'bearing_wear',
    last_reading_at: '2003-10-22T13:00:00+00:00',
    vibration_severity: 'high',
    risk_score: 100,
    abnormal_event_count: 2,
    open_alert_count: 1,
    predicted_rul_minutes: 42.5,
    rul_estimate_kind: 'point_estimate',
    out_of_distribution: false,
  },
  {
    machine_id: 'm2',
    health_state: 'healthy',
    confidence: null,
    prediction_source: 'rule_based',
    probable_cause: null,
    last_reading_at: '2003-10-22T13:00:00+00:00',
    vibration_severity: 'low',
    risk_score: 0,
    abnormal_event_count: 0,
    open_alert_count: 0,
    predicted_rul_minutes: 1200.0,
    rul_estimate_kind: 'point_estimate',
    out_of_distribution: false,
  },
]
```

- [ ] **Step 3: Update the failing tests first (temperature → RUL/real metrics)**

In `frontend/src/api/client.test.ts`, replace the body of the "fetches trends" test (lines 19-30) so it no longer uses a temperature metric:

```ts
  it('fetches trends with metric + limit as query params', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/machines/:id/trends', ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json([])
      }),
    )
    await api.getTrends('m1', 'rul_minutes', 100)
    expect(capturedUrl).toContain('metric=rul_minutes')
    expect(capturedUrl).toContain('limit=100')
  })
```

In `frontend/src/components/TrendChart.test.tsx`, replace the second test (lines 12-15) with:

```ts
  it('renders a labeled chart region when data is present', () => {
    render(<TrendChart metric="rul_minutes" points={trendPoints} />)
    expect(screen.getByRole('img', { name: /rul_minutes/ })).toBeInTheDocument()
  })
```

In `frontend/src/components/MachineDetail.test.tsx`, replace the "refetches trends when the metric changes" test (lines 40-51) with a non-temperature metric that will still exist in the dropdown, and add a new RUL fact test immediately after it:

```ts
  it('refetches trends when the metric changes', async () => {
    const spy = vi.spyOn(api, 'getTrends')
    render(harness())

    const select = await screen.findByLabelText(/metric/i)
    await userEvent.selectOptions(select, 'vibration_h_kurtosis')

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('m1', 'vibration_h_kurtosis', expect.anything()),
    )
    spy.mockRestore()
  })

  it('surfaces the predicted RUL health fact', async () => {
    render(harness())

    expect(await screen.findByText(/predicted rul \(min\)/i)).toBeInTheDocument()
    expect(screen.getByText('42.5')).toBeInTheDocument()
  })
```

- [ ] **Step 4: Run the tests to confirm they fail against the current component**

Run: `cd C:/projects/MaintainIQ/frontend && npm test -- src/components/MachineDetail.test.tsx`
Expected: FAIL — `selectOptions('vibration_h_kurtosis')` still works but the new RUL test fails (`/predicted rul \(min\)/i` not found — the component still renders "Temperature"). TypeScript will also flag `temperature_severity` removed from the fixture where `MachineDetail.tsx` still reads it.

- [ ] **Step 5: Swap the metric and the fact in `MachineDetail.tsx`**

In `frontend/src/components/MachineDetail.tsx`, replace the `METRICS` array (lines 17-22) with (drop `temperature_c`, add `rul_minutes`):

```ts
const METRICS = [
  { value: 'vibration_h_rms', label: 'Vibration RMS' },
  { value: 'vibration_h_kurtosis', label: 'Kurtosis' },
  { value: 'vibration_h_high_band_energy_ratio', label: 'Band energy ratio' },
  { value: 'rul_minutes', label: 'Predicted RUL (min)' },
]
```

Then replace the health-facts `<dl>` (lines 112-119) with (drop the Temperature `Fact`, add three RUL `Fact`s):

```tsx
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
        <Fact label="Probable cause" value={health.probable_cause ?? '—'} />
        <Fact label="Risk score" value={String(health.risk_score)} mono />
        <Fact label="Confidence" value={health.confidence != null ? `${Math.round(health.confidence * 100)}%` : '—'} mono />
        <Fact label="Vibration" value={health.vibration_severity} />
        <Fact
          label="Predicted RUL (min)"
          value={health.predicted_rul_minutes != null ? health.predicted_rul_minutes.toFixed(1) : '—'}
          mono
        />
        <Fact label="RUL estimate" value={health.rul_estimate_kind ?? '—'} />
        <Fact
          label="Out of distribution"
          value={health.out_of_distribution == null ? '—' : health.out_of_distribution ? 'Yes' : 'No'}
        />
        <Fact label="Last reading" value={health.last_reading_at ?? '—'} mono />
      </dl>
```

- [ ] **Step 6: Run the full frontend suite**

Run: `cd C:/projects/MaintainIQ/frontend && npm test`
Expected: PASS — all files green, no `temperature` references remain in production code or tests. If any other file still references `temperature`, grep and fix it (there should be none beyond the ones listed).

- [ ] **Step 7: Commit**

```bash
cd C:/projects/MaintainIQ && git add frontend/src/api/types.ts frontend/src/test/fixtures.ts frontend/src/components/MachineDetail.tsx frontend/src/components/MachineDetail.test.tsx frontend/src/api/client.test.ts frontend/src/components/TrendChart.test.tsx
git commit -m "feat(frontend): remove temperature, surface RUL on machine health"
```

---

### Task 2: API client + types + fixtures + MSW handlers for model/ingestion/reports

**Files:**
- Modify: `frontend/src/api/types.ts` (append new interfaces)
- Modify: `frontend/src/api/client.ts` (full replacement)
- Modify: `frontend/src/test/fixtures.ts` (append new fixtures)
- Modify: `frontend/src/test/server.ts` (add default handlers)
- Test: `frontend/src/api/client.test.ts` (append new method tests)

**Interfaces:**
- Consumes: `ApiError`, `request` from `client.ts`.
- Produces (relied on by Tasks 3–5):
  - `api.getModelHealth(): Promise<ModelHealth>`
  - `api.getModelTelemetry(windowMinutes?: number): Promise<ModelTelemetry>`
  - `api.startReplay(payload: ReplayStartRequest): Promise<ReplayStartResponse>`
  - `api.stopReplay(machineId: string): Promise<ReplayStopResponse>`
  - `api.getReplayStatus(): Promise<ReplayStatus>`
  - `api.createReport(payload: ReportCreateRequest): Promise<Report>`
  - `api.listReports(scope?: string): Promise<Report[]>`
  - `api.getReport(id: number): Promise<Report>`
  - `api.downloadReport(id: number): Promise<{ filename: string; content: string }>`
  - Fixtures `modelHealth`, `modelTelemetry`, `replayStatus`, `reports`, `reportDetail`.

- [ ] **Step 1: Add the new types**

Append to `frontend/src/api/types.ts`:

```ts
// ---- Model observability (src/api/routes/model.py) ----
export interface ModelHealth {
  status: 'healthy' | 'stale'
  model_version: string | null
  last_inference_at: string | null
  seconds_since_last_inference: number | null
  active: boolean
}

export interface ModelTelemetry {
  window_minutes: number
  inference_count: number
  error_rate: number
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  ood_rate: number
  warming_up_rate: number
}

// ---- Ingestion replay control (src/api/routes/ingestion.py) ----
export interface ReplayMachineStatus {
  cycle: number | null
  running: boolean
  last_ts: string | null
  replayed: number
  error: string | null
}

export type ReplayStatus = Record<string, ReplayMachineStatus>

export interface ReplayStartRequest {
  machine_id: string
  speed_multiplier?: number
}

export interface ReplayStartResponse {
  machine_id: string
  status: string
  speed_multiplier: number
}

export interface ReplayStopResponse {
  machine_id: string
  status: string
}

// ---- Reports (src/api/routes/reports.py) ----
export type ReportType = 'machine_prognostic' | 'model_performance' | 'fleet_summary'
export type ReportFormat = 'markdown' | 'json'

export interface ReportCreateRequest {
  report_type: ReportType
  scope: string
  format?: ReportFormat
  period_start?: string | null
  period_end?: string | null
}

// list responses omit `content` (see src/reports/service.py:list_reports);
// create/get responses include it.
export interface Report {
  id: number
  report_type: ReportType
  scope: string
  format: ReportFormat
  period_start: string | null
  period_end: string | null
  generated_at: string
  generated_by: number | null
  summary: Record<string, unknown> | null
  content?: string
}
```

- [ ] **Step 2: Write the failing client tests first**

Append these tests inside the `describe('api client', ...)` block in `frontend/src/api/client.test.ts` (before the closing `})`):

```ts
  it('fetches model health', async () => {
    const health = await api.getModelHealth()
    expect(health.status).toBe('healthy')
    expect(health.model_version).toBe('xjtu_rul_v1')
  })

  it('fetches model telemetry with a window_minutes query param', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/model/telemetry', ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json({
          window_minutes: 30,
          inference_count: 0,
          error_rate: 0,
          latency_p50_ms: null,
          latency_p95_ms: null,
          ood_rate: 0,
          warming_up_rate: 0,
        })
      }),
    )
    await api.getModelTelemetry(30)
    expect(capturedUrl).toContain('window_minutes=30')
  })

  it('starts a replay with machine_id and speed_multiplier', async () => {
    const res = await api.startReplay({ machine_id: 'm1', speed_multiplier: 4 })
    expect(res.status).toBe('started')
    expect(res.machine_id).toBe('m1')
    expect(res.speed_multiplier).toBe(4)
  })

  it('stops a replay', async () => {
    const res = await api.stopReplay('m1')
    expect(res.status).toBe('stopped')
    expect(res.machine_id).toBe('m1')
  })

  it('fetches replay status keyed by machine', async () => {
    const status = await api.getReplayStatus()
    expect(status.m1.running).toBe(true)
    expect(status.m2.running).toBe(false)
  })

  it('creates a report', async () => {
    const report = await api.createReport({ report_type: 'fleet_summary', scope: 'fleet' })
    expect(report.report_type).toBe('fleet_summary')
    expect(report.id).toBeGreaterThan(0)
  })

  it('lists reports with an optional scope query param', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/reports', ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json([])
      }),
    )
    await api.listReports('m1')
    expect(capturedUrl).toContain('scope=m1')
  })

  it('fetches a single report with content', async () => {
    const report = await api.getReport(1)
    expect(report.content).toContain('Machine Prognostic Report')
  })

  it('downloads a report, parsing the filename from Content-Disposition', async () => {
    const { filename, content } = await api.downloadReport(1)
    expect(filename).toBe('report-1.md')
    expect(content).toContain('Machine Prognostic Report')
  })
```

- [ ] **Step 3: Run the new tests to confirm they fail**

Run: `cd C:/projects/MaintainIQ/frontend && npm test -- src/api/client.test.ts`
Expected: FAIL — `api.getModelHealth` etc. are not functions; MSW has no default handlers yet.

- [ ] **Step 4: Add the fixtures**

Append to `frontend/src/test/fixtures.ts` (and add the new type imports to the existing `import type { ... } from '../api/types'` block: `ModelHealth`, `ModelTelemetry`, `ReplayStatus`, `Report`):

```ts
export const modelHealth: ModelHealth = {
  status: 'healthy',
  model_version: 'xjtu_rul_v1',
  last_inference_at: '2003-10-22T13:00:00+00:00',
  seconds_since_last_inference: 12.5,
  active: true,
}

export const modelTelemetry: ModelTelemetry = {
  window_minutes: 60,
  inference_count: 128,
  error_rate: 0.0,
  latency_p50_ms: 8.4,
  latency_p95_ms: 21.7,
  ood_rate: 0.05,
  warming_up_rate: 0.1,
}

export const replayStatus: ReplayStatus = {
  m1: { cycle: 3, running: true, last_ts: '2003-10-22T13:00:00+00:00', replayed: 1500, error: null },
  m2: { cycle: null, running: false, last_ts: null, replayed: 0, error: null },
}

export const reports: Report[] = [
  {
    id: 2,
    report_type: 'fleet_summary',
    scope: 'fleet',
    format: 'json',
    period_start: null,
    period_end: null,
    generated_at: '2026-08-06T10:00:00+00:00',
    generated_by: 1,
    summary: { machine_count: 2 },
  },
  {
    id: 1,
    report_type: 'machine_prognostic',
    scope: 'm1',
    format: 'markdown',
    period_start: null,
    period_end: null,
    generated_at: '2026-08-06T09:00:00+00:00',
    generated_by: 1,
    summary: { report_type: 'machine_prognostic' },
  },
]

export const reportDetail: Report = {
  ...reports[1],
  content: '# Machine Prognostic Report\n\nPredicted RUL: 42 minutes',
}
```

- [ ] **Step 5: Add the default MSW handlers**

In `frontend/src/test/server.ts`, add the new fixtures to the imports (`modelHealth`, `modelTelemetry`, `replayStatus`, `reports`, `reportDetail`) and append these handlers to the `handlers` array (before the closing `]`):

```ts
  http.get('/api/model/health', () => HttpResponse.json(modelHealth)),
  http.get('/api/model/telemetry', () => HttpResponse.json(modelTelemetry)),

  http.get('/api/ingestion/replay/status', () => HttpResponse.json(replayStatus)),
  http.post('/api/ingestion/replay/start', async ({ request }) => {
    const body = (await request.json()) as { machine_id: string; speed_multiplier?: number }
    return HttpResponse.json({
      machine_id: body.machine_id,
      status: 'started',
      speed_multiplier: body.speed_multiplier ?? 1.0,
    })
  }),
  http.post('/api/ingestion/replay/stop', async ({ request }) => {
    const body = (await request.json()) as { machine_id: string }
    return HttpResponse.json({ machine_id: body.machine_id, status: 'stopped' })
  }),

  http.get('/api/reports', () => HttpResponse.json(reports)),
  http.post('/api/reports', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json({
      id: 3,
      generated_at: '2026-08-06T11:00:00+00:00',
      generated_by: 1,
      content: '# Report',
      summary: {},
      ...body,
    })
  }),
  http.get('/api/reports/:id', () => HttpResponse.json(reportDetail)),
  http.get('/api/reports/:id/download', () =>
    new HttpResponse('# Machine Prognostic Report', {
      headers: {
        'Content-Type': 'text/markdown',
        'Content-Disposition': 'attachment; filename="report-1.md"',
      },
    }),
  ),
```

- [ ] **Step 6: Replace `client.ts` with the version that adds the methods**

Replace the entire contents of `frontend/src/api/client.ts` with:

```ts
// Thin typed fetch wrapper around the FastAPI backend. Base URL comes from
// VITE_API_BASE_URL (see README); defaults to '/api', which works both in dev
// (Vite proxy) and prod (FastAPI serves the SPA from the same origin).
import type {
  Alert,
  KpiSummary,
  MachineDetail,
  MachineSummary,
  MaintenanceCreate,
  MaintenanceRecord,
  ModelHealth,
  ModelTelemetry,
  NotificationOut,
  Report,
  ReportCreateRequest,
  ReplayStartRequest,
  ReplayStartResponse,
  ReplayStatus,
  ReplayStopResponse,
  SimulateFaultRequest,
  SimulateFaultResponse,
  TrendPoint,
  UserCreate,
  UserOut,
  UserUpdate,
} from './types'

export const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// Shared response handling: fire the app-wide logout event on 401 and turn any
// non-2xx into an ApiError carrying the backend `detail`. Used by both the JSON
// `request` helper and the raw `downloadReport` fetch below.
async function throwIfError(res: Response): Promise<void> {
  if (res.status === 401) {
    window.dispatchEvent(new Event('miq:unauthorized'))
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, detail)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    // Session is an httpOnly cookie (src/auth/security.py) — 'same-origin' is
    // fetch's default, but spelled out here since the whole app's auth model
    // depends on it: both dev (Vite proxy) and prod (FastAPI serves the SPA)
    // keep API calls same-origin from the browser's point of view.
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  await throwIfError(res)
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  login: (email: string, password: string) =>
    request<UserOut>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
  me: () => request<UserOut>('/auth/me'),

  getKpiSummary: () => request<KpiSummary>('/kpis'),
  getMachines: () => request<MachineSummary[]>('/machines'),
  getMachine: (id: string) => request<MachineDetail>(`/machines/${encodeURIComponent(id)}`),
  getTrends: (id: string, metric: string, limit = 500) =>
    request<TrendPoint[]>(
      `/machines/${encodeURIComponent(id)}/trends?metric=${encodeURIComponent(metric)}&limit=${limit}`,
    ),
  getAlerts: (status?: string) =>
    request<Alert[]>(`/alerts${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  getMaintenanceHistory: (id: string, { limit = 20, offset = 0 }: { limit?: number; offset?: number } = {}) =>
    request<MaintenanceRecord[]>(`/maintenance/${encodeURIComponent(id)}?limit=${limit}&offset=${offset}`),
  logMaintenance: (payload: MaintenanceCreate) =>
    request<MaintenanceRecord>('/maintenance', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  acknowledgeAlert: (id: number) =>
    request<Alert>(`/alerts/${id}/acknowledge`, { method: 'POST' }),

  listUsers: () => request<UserOut[]>('/users'),
  createUser: (payload: UserCreate) =>
    request<UserOut>('/users', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: UserUpdate) =>
    request<UserOut>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  listNotifications: (status?: string) =>
    request<NotificationOut[]>(`/notifications${status ? `?status=${encodeURIComponent(status)}` : ''}`),

  simulateFault: (payload: SimulateFaultRequest) =>
    request<SimulateFaultResponse>('/demo/simulate-fault', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  resetMachine: (machineId: string) =>
    request<SimulateFaultResponse>(`/demo/reset-machine/${encodeURIComponent(machineId)}`, {
      method: 'POST',
    }),

  getModelHealth: () => request<ModelHealth>('/model/health'),
  getModelTelemetry: (windowMinutes?: number) =>
    request<ModelTelemetry>(`/model/telemetry${windowMinutes != null ? `?window_minutes=${windowMinutes}` : ''}`),

  startReplay: (payload: ReplayStartRequest) =>
    request<ReplayStartResponse>('/ingestion/replay/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  stopReplay: (machineId: string) =>
    request<ReplayStopResponse>('/ingestion/replay/stop', {
      method: 'POST',
      body: JSON.stringify({ machine_id: machineId }),
    }),
  getReplayStatus: () => request<ReplayStatus>('/ingestion/replay/status'),

  createReport: (payload: ReportCreateRequest) =>
    request<Report>('/reports', { method: 'POST', body: JSON.stringify(payload) }),
  listReports: (scope?: string) =>
    request<Report[]>(`/reports${scope ? `?scope=${encodeURIComponent(scope)}` : ''}`),
  getReport: (id: number) => request<Report>(`/reports/${id}`),
  // Raw download: the endpoint returns the file body (not JSON) with a
  // Content-Disposition filename. Returns the parsed filename + text content so
  // the caller can build a Blob and trigger a browser download.
  downloadReport: async (id: number): Promise<{ filename: string; content: string }> => {
    const res = await fetch(`${BASE}/reports/${id}/download`, { credentials: 'same-origin' })
    await throwIfError(res)
    const disposition = res.headers.get('Content-Disposition') ?? ''
    const match = /filename="?([^"]+)"?/.exec(disposition)
    const filename = match ? match[1] : `report-${id}`
    const content = await res.text()
    return { filename, content }
  },
}
```

- [ ] **Step 7: Run the full suite**

Run: `cd C:/projects/MaintainIQ/frontend && npm test`
Expected: PASS — all prior tests plus the 9 new client tests green.

- [ ] **Step 8: Commit**

```bash
cd C:/projects/MaintainIQ && git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/fixtures.ts frontend/src/test/server.ts frontend/src/api/client.test.ts
git commit -m "feat(frontend): add API client + types + test doubles for model/ingestion/reports"
```

---

### Task 3: Model observability page + route + nav

**Files:**
- Create: `frontend/src/pages/ModelPage.tsx`
- Test: `frontend/src/pages/ModelPage.test.tsx`
- Modify: `frontend/src/App.tsx:13,29-35` (import + route)
- Modify: `frontend/src/layout/AppShell.tsx:1,17-26` (icon import + nav item)

**Interfaces:**
- Consumes: `api.getModelHealth`, `api.getModelTelemetry`, `MetricCard`, `Card`, `healthLabel`.
- Produces: route `/model` (any authenticated role), nav item "Model".

- [ ] **Step 1: Write the failing page test**

Create `frontend/src/pages/ModelPage.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { ModelPage } from './ModelPage'
import { server } from '../test/server'

function harness() {
  return (
    <MemoryRouter initialEntries={['/model']}>
      <AuthProvider>
        <ModelPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('ModelPage', () => {
  it('renders model health and telemetry', async () => {
    render(harness())

    expect(await screen.findByText(/xjtu_rul_v1/i)).toBeInTheDocument()
    // health status label
    expect(screen.getByText(/healthy/i)).toBeInTheDocument()
    // telemetry: inference count and p95 latency
    expect(screen.getByText('128')).toBeInTheDocument()
    expect(screen.getByText('21.7')).toBeInTheDocument()
  })

  it('shows an error banner when health fails to load', async () => {
    server.use(http.get('/api/model/health', () => HttpResponse.json({ detail: 'model unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/model unavailable/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd C:/projects/MaintainIQ/frontend && npm test -- src/pages/ModelPage.test.tsx`
Expected: FAIL — `./ModelPage` module not found.

- [ ] **Step 3: Create the page**

Create `frontend/src/pages/ModelPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import type { ModelHealth, ModelTelemetry } from '../api/types'
import { api } from '../api/client'
import { Card } from '../components/ui/card'
import { MetricCard } from '../components/ui/metric-card'

function fmt(n: number | null, digits = 1): string {
  return n != null ? n.toFixed(digits) : '—'
}

function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

export function ModelPage() {
  const [health, setHealth] = useState<ModelHealth | null>(null)
  const [telemetry, setTelemetry] = useState<ModelTelemetry | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    Promise.all([api.getModelHealth(), api.getModelTelemetry()])
      .then(([h, t]) => {
        setHealth(h)
        setTelemetry(t)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load model observability'))
  }, [])

  if (error) {
    return (
      <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
    )
  }

  if (!health || !telemetry) {
    return <p className="text-sm text-text-muted">Loading model observability…</p>
  }

  const healthTone = health.status === 'healthy' ? 'healthy' : 'critical'

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Model observability</h1>
        <p className="text-xs text-text-muted">Inference heartbeat and rolling telemetry for the active RUL model</p>
      </div>

      <Card className="panel-notch p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-text-muted">Health</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCard label="Status" value={health.status} tone={healthTone} />
          <MetricCard label="Model version" value={health.model_version ?? '—'} tone="accent" />
          <MetricCard label="Active" value={health.active ? 'Yes' : 'No'} tone={health.active ? 'healthy' : 'unknown'} />
          <MetricCard
            label="Since last inference (s)"
            value={fmt(health.seconds_since_last_inference)}
          />
        </div>
        <p className="mt-2 text-xs text-text-muted">Last inference: {health.last_inference_at ?? 'never'}</p>
      </Card>

      <Card className="panel-notch p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-text-muted">
          Telemetry (last {telemetry.window_minutes} min)
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <MetricCard label="Inferences" value={telemetry.inference_count} tone="accent" />
          <MetricCard label="Error rate" value={pct(telemetry.error_rate)} tone="critical" />
          <MetricCard label="OOD rate" value={pct(telemetry.ood_rate)} tone="degrading" />
          <MetricCard label="Warming-up rate" value={pct(telemetry.warming_up_rate)} tone="degrading" />
          <MetricCard label="Latency p50 (ms)" value={fmt(telemetry.latency_p50_ms)} />
          <MetricCard label="Latency p95 (ms)" value={fmt(telemetry.latency_p95_ms)} />
        </div>
      </Card>
    </div>
  )
}
```

- [ ] **Step 4: Wire the route**

In `frontend/src/App.tsx`, add the import after the `AnalyticsPage` import (line 13):

```tsx
import { ModelPage } from './pages/ModelPage'
```

Then add the route inside the `AppShell` route block, after the `analytics` route (line 34):

```tsx
                <Route path="model" element={<ModelPage />} />
```

- [ ] **Step 5: Wire the nav item**

In `frontend/src/layout/AppShell.tsx`, add `Activity` to the lucide-react import (line 1):

```tsx
import { Activity, Bell, Gauge, LayoutGrid, LogOut, PlayCircle, Radar, ShieldAlert, Users, Wrench } from 'lucide-react'
```

Then add to the `NAV` array after the Analytics item (line 21):

```tsx
  { to: '/model', label: 'Model', icon: Activity },
```

- [ ] **Step 6: Run the page test then the full suite**

Run: `cd C:/projects/MaintainIQ/frontend && npm test -- src/pages/ModelPage.test.tsx`
Expected: PASS
Run: `cd C:/projects/MaintainIQ/frontend && npm test`
Expected: PASS (App/AppShell tests still green with the new nav/route).

- [ ] **Step 7: Commit**

```bash
cd C:/projects/MaintainIQ && git add frontend/src/pages/ModelPage.tsx frontend/src/pages/ModelPage.test.tsx frontend/src/App.tsx frontend/src/layout/AppShell.tsx
git commit -m "feat(frontend): add model observability page"
```

---

### Task 4: Ingestion control page + route + nav (admin+supervisor)

**Files:**
- Create: `frontend/src/pages/IngestionPage.tsx`
- Test: `frontend/src/pages/IngestionPage.test.tsx`
- Modify: `frontend/src/App.tsx` (import + route under RequireRole)
- Modify: `frontend/src/layout/AppShell.tsx` (icon import + nav item with roles)

**Interfaces:**
- Consumes: `api.getMachines`, `api.getReplayStatus`, `api.startReplay`, `api.stopReplay`, `Card`, `Button`, `Select`.
- Produces: route `/ingestion` (admin+supervisor), nav item "Ingestion".

- [ ] **Step 1: Write the failing page test**

Create `frontend/src/pages/IngestionPage.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { IngestionPage } from './IngestionPage'
import { server } from '../test/server'
import { api } from '../api/client'

function harness() {
  return (
    <MemoryRouter initialEntries={['/ingestion']}>
      <AuthProvider>
        <IngestionPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('IngestionPage', () => {
  it('renders the replay status table', async () => {
    render(harness())

    const m1Row = (await screen.findByText('m1')).closest('tr')!
    expect(within(m1Row).getByText(/running/i)).toBeInTheDocument()
    expect(within(m1Row).getByText('1500')).toBeInTheDocument()
  })

  it('starts a replay for the selected machine', async () => {
    const spy = vi.spyOn(api, 'startReplay')
    render(harness())

    await screen.findByLabelText(/machine/i)
    await userEvent.selectOptions(screen.getByLabelText(/machine/i), 'm2')
    await userEvent.click(screen.getByRole('button', { name: /start replay/i }))

    await waitFor(() => expect(spy).toHaveBeenCalledWith({ machine_id: 'm2', speed_multiplier: 1 }))
    spy.mockRestore()
  })

  it('shows an error banner when start fails', async () => {
    server.use(
      http.post('/api/ingestion/replay/start', () => HttpResponse.json({ detail: 'no readings for machine' }, { status: 404 })),
    )
    render(harness())

    await screen.findByLabelText(/machine/i)
    await userEvent.click(screen.getByRole('button', { name: /start replay/i }))
    expect(await screen.findByText(/no readings for machine/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd C:/projects/MaintainIQ/frontend && npm test -- src/pages/IngestionPage.test.tsx`
Expected: FAIL — `./IngestionPage` module not found.

- [ ] **Step 3: Create the page**

Create `frontend/src/pages/IngestionPage.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import type { MachineSummary, ReplayStatus } from '../api/types'
import { api } from '../api/client'
import { Card } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Select } from '../components/ui/input'
import { Badge } from '../components/ui/badge'

export function IngestionPage() {
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [machineId, setMachineId] = useState('')
  const [speed, setSpeed] = useState(1)
  const [status, setStatus] = useState<ReplayStatus>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadStatus = useCallback(() => {
    api
      .getReplayStatus()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load replay status'))
  }, [])

  useEffect(() => {
    api
      .getMachines()
      .then((m) => {
        setMachines(m)
        setMachineId((prev) => prev || m[0]?.machine_id || '')
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load machines'))
    loadStatus()
  }, [loadStatus])

  async function handleStart() {
    if (!machineId) return
    setBusy(true)
    setError(null)
    try {
      await api.startReplay({ machine_id: machineId, speed_multiplier: speed })
      loadStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start replay')
    } finally {
      setBusy(false)
    }
  }

  async function handleStop() {
    if (!machineId) return
    setBusy(true)
    setError(null)
    try {
      await api.stopReplay(machineId)
      loadStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to stop replay')
    } finally {
      setBusy(false)
    }
  }

  const rows = Object.entries(status)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Ingestion control</h1>
        <p className="text-xs text-text-muted">Replay stored readings back through the live predict + persist path</p>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      <Card className="panel-notch p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Start / stop a replay</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-0.5 text-xs text-text-muted">
            Machine
            <Select value={machineId} onChange={(e) => setMachineId(e.target.value)}>
              {machines.map((m) => (
                <option key={m.machine_id} value={m.machine_id}>
                  {m.machine_id}
                </option>
              ))}
            </Select>
          </label>
          <label className="grid gap-0.5 text-xs text-text-muted">
            Speed ×
            <input
              type="number"
              min={0.1}
              step={0.1}
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              className="w-24 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
            />
          </label>
          <Button type="button" onClick={handleStart} disabled={busy || !machineId}>
            {busy ? 'Working…' : 'Start replay'}
          </Button>
          <Button type="button" variant="outline" onClick={handleStop} disabled={busy || !machineId}>
            Stop replay
          </Button>
          <Button type="button" variant="outline" onClick={loadStatus} disabled={busy}>
            Refresh
          </Button>
        </div>
      </Card>

      <Card className="panel-notch overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="bg-white/[0.04]">
            <tr className="text-xs uppercase text-text-muted">
              <th className="px-3 py-2">Machine</th>
              <th className="px-3 py-2">State</th>
              <th className="px-3 py-2">Cycle</th>
              <th className="px-3 py-2">Replayed</th>
              <th className="px-3 py-2">Last timestamp</th>
              <th className="px-3 py-2">Error</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-text-muted">No replays tracked yet.</td>
              </tr>
            ) : (
              rows.map(([id, s]) => (
                <tr key={id} className="border-t border-white/10">
                  <td className="px-3 py-2 font-mono text-text">{id}</td>
                  <td className="px-3 py-2">
                    <Badge variant={s.running ? 'healthy' : 'unknown'}>{s.running ? 'Running' : 'Stopped'}</Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-text-muted">{s.cycle ?? '—'}</td>
                  <td className="px-3 py-2 font-mono text-text-muted">{s.replayed}</td>
                  <td className="px-3 py-2 font-mono text-text-muted">{s.last_ts ?? '—'}</td>
                  <td className="px-3 py-2 text-critical">{s.error ?? '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
```

- [ ] **Step 4: Wire the route (admin+supervisor)**

In `frontend/src/App.tsx`, add the import after the `ModelPage` import:

```tsx
import { IngestionPage } from './pages/IngestionPage'
```

Then add the `ingestion` route inside the existing `RequireRole allow={['admin', 'supervisor']}` block that wraps `notifications` (lines 37-39), so it reads:

```tsx
                <Route element={<RequireRole allow={['admin', 'supervisor']} />}>
                  <Route path="notifications" element={<NotificationsPage />} />
                  <Route path="ingestion" element={<IngestionPage />} />
                </Route>
```

- [ ] **Step 5: Wire the nav item (admin+supervisor)**

In `frontend/src/layout/AppShell.tsx`, add `Waves` to the lucide-react import:

```tsx
import { Activity, Bell, Gauge, LayoutGrid, LogOut, PlayCircle, Radar, ShieldAlert, Users, Waves, Wrench } from 'lucide-react'
```

Then add to the `NAV` array after the Notifications item:

```tsx
  { to: '/ingestion', label: 'Ingestion', icon: Waves, roles: ['admin', 'supervisor'] },
```

- [ ] **Step 6: Run the page test then the full suite**

Run: `cd C:/projects/MaintainIQ/frontend && npm test -- src/pages/IngestionPage.test.tsx`
Expected: PASS
Run: `cd C:/projects/MaintainIQ/frontend && npm test`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd C:/projects/MaintainIQ && git add frontend/src/pages/IngestionPage.tsx frontend/src/pages/IngestionPage.test.tsx frontend/src/App.tsx frontend/src/layout/AppShell.tsx
git commit -m "feat(frontend): add ingestion control page (admin+supervisor)"
```

---

### Task 5: Reports page + route + nav (per-type RBAC in UI)

**Files:**
- Create: `frontend/src/pages/ReportsPage.tsx`
- Test: `frontend/src/pages/ReportsPage.test.tsx`
- Modify: `frontend/src/App.tsx` (import + route, any authed)
- Modify: `frontend/src/layout/AppShell.tsx` (icon import + nav item)

**Interfaces:**
- Consumes: `api.listReports`, `api.createReport`, `api.downloadReport`, `useAuth`, `Card`, `Button`, `Select`, `Badge`, `sonner` toast.
- Produces: route `/reports` (any authenticated role), nav item "Reports". Operators see only the `machine_prognostic` report type in the create form (per-type RBAC in UI; backend enforces too).

- [ ] **Step 1: Write the failing page test**

Create `frontend/src/pages/ReportsPage.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { ReportsPage } from './ReportsPage'
import { server } from '../test/server'
import { api } from '../api/client'
import { operatorUser } from '../test/fixtures'

function harness() {
  return (
    <MemoryRouter initialEntries={['/reports']}>
      <AuthProvider>
        <ReportsPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('ReportsPage', () => {
  it('lists existing reports', async () => {
    render(harness())
    expect(await screen.findByText(/fleet_summary/i)).toBeInTheDocument()
    expect(screen.getByText(/machine_prognostic/i)).toBeInTheDocument()
  })

  it('creates a report from the form', async () => {
    const spy = vi.spyOn(api, 'createReport')
    render(harness())
    await screen.findByText(/fleet_summary/i)

    await userEvent.selectOptions(screen.getByLabelText(/report type/i), 'fleet_summary')
    await userEvent.clear(screen.getByLabelText(/scope/i))
    await userEvent.type(screen.getByLabelText(/scope/i), 'fleet')
    await userEvent.click(screen.getByRole('button', { name: /generate/i }))

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ report_type: 'fleet_summary', scope: 'fleet' }),
      ),
    )
    spy.mockRestore()
  })

  it('downloads a report', async () => {
    const spy = vi.spyOn(api, 'downloadReport')
    const createObjectURL = vi.fn(() => 'blob:x')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(harness())
    await screen.findByText(/fleet_summary/i)
    await userEvent.click(screen.getAllByRole('button', { name: /download/i })[0])

    await waitFor(() => expect(spy).toHaveBeenCalled())
    expect(clickSpy).toHaveBeenCalled()
    spy.mockRestore()
    clickSpy.mockRestore()
  })

  it('restricts operators to the machine_prognostic report type', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    render(harness())
    await screen.findByText(/fleet_summary/i)

    const typeSelect = within(screen.getByLabelText(/report type/i).closest('label')!)
    expect(typeSelect.getByRole('option', { name: /machine_prognostic/i })).toBeInTheDocument()
    expect(typeSelect.queryByRole('option', { name: /fleet_summary/i })).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd C:/projects/MaintainIQ/frontend && npm test -- src/pages/ReportsPage.test.tsx`
Expected: FAIL — `./ReportsPage` module not found.

- [ ] **Step 3: Create the page**

Create `frontend/src/pages/ReportsPage.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { toast } from 'sonner'
import type { Report, ReportFormat, ReportType } from '../api/types'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Card } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Select } from '../components/ui/input'
import { Badge } from '../components/ui/badge'

const ALL_TYPES: ReportType[] = ['machine_prognostic', 'model_performance', 'fleet_summary']

export function ReportsPage() {
  const { user } = useAuth()
  const isOperator = user?.role === 'operator'
  const allowedTypes: ReportType[] = isOperator ? ['machine_prognostic'] : ALL_TYPES

  const [reports, setReports] = useState<Report[]>([])
  const [reportType, setReportType] = useState<ReportType>('machine_prognostic')
  const [scope, setScope] = useState('m1')
  const [format, setFormat] = useState<ReportFormat>('json')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    api
      .listReports()
      .then(setReports)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load reports'))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleGenerate(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.createReport({ report_type: reportType, scope, format })
      toast.success('Report generated')
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate report')
    } finally {
      setBusy(false)
    }
  }

  async function handleDownload(id: number) {
    try {
      const { filename, content } = await api.downloadReport(id)
      const blob = new Blob([content], { type: 'text/plain' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to download report')
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Reports</h1>
        <p className="text-xs text-text-muted">Generate and download prognostic, model-performance, and fleet reports</p>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      <Card className="panel-notch p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Generate a report</h2>
        <form onSubmit={handleGenerate} className="flex flex-wrap items-end gap-3">
          <label className="grid gap-0.5 text-xs text-text-muted">
            Report type
            <Select value={reportType} onChange={(e) => setReportType(e.target.value as ReportType)}>
              {allowedTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </label>
          <label className="grid gap-0.5 text-xs text-text-muted">
            Scope
            <input
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              className="w-40 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
            />
          </label>
          <label className="grid gap-0.5 text-xs text-text-muted">
            Format
            <Select value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
              <option value="json">json</option>
              <option value="markdown">markdown</option>
            </Select>
          </label>
          <Button type="submit" disabled={busy}>
            {busy ? 'Generating…' : 'Generate'}
          </Button>
        </form>
      </Card>

      <Card className="panel-notch overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="bg-white/[0.04]">
            <tr className="text-xs uppercase text-text-muted">
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Scope</th>
              <th className="px-3 py-2">Format</th>
              <th className="px-3 py-2">Generated</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {reports.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-text-muted">No reports generated yet.</td>
              </tr>
            ) : (
              reports.map((r) => (
                <tr key={r.id} className="border-t border-white/10">
                  <td className="px-3 py-2 text-text">{r.report_type}</td>
                  <td className="px-3 py-2 font-mono text-text-muted">{r.scope}</td>
                  <td className="px-3 py-2">
                    <Badge variant="unknown">{r.format}</Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-text-muted">{r.generated_at}</td>
                  <td className="px-3 py-2">
                    <Button type="button" variant="outline" size="sm" onClick={() => handleDownload(r.id)}>
                      Download
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
```

- [ ] **Step 4: Wire the route (any authed)**

In `frontend/src/App.tsx`, add the import after the `IngestionPage` import:

```tsx
import { ReportsPage } from './pages/ReportsPage'
```

Then add the route inside the `AppShell` block, after the `model` route:

```tsx
                <Route path="reports" element={<ReportsPage />} />
```

- [ ] **Step 5: Wire the nav item (all roles)**

In `frontend/src/layout/AppShell.tsx`, add `FileText` to the lucide-react import:

```tsx
import { Activity, Bell, FileText, Gauge, LayoutGrid, LogOut, PlayCircle, Radar, ShieldAlert, Users, Waves, Wrench } from 'lucide-react'
```

Then add to the `NAV` array after the Model item:

```tsx
  { to: '/reports', label: 'Reports', icon: FileText },
```

- [ ] **Step 6: Run the page test then the full suite**

Run: `cd C:/projects/MaintainIQ/frontend && npm test -- src/pages/ReportsPage.test.tsx`
Expected: PASS
Run: `cd C:/projects/MaintainIQ/frontend && npm test`
Expected: PASS — full suite green.

- [ ] **Step 7: Commit**

```bash
cd C:/projects/MaintainIQ && git add frontend/src/pages/ReportsPage.tsx frontend/src/pages/ReportsPage.test.tsx frontend/src/App.tsx frontend/src/layout/AppShell.tsx
git commit -m "feat(frontend): add reports page with per-type RBAC"
```

---

## Self-Review

**Spec coverage (roadmap Phase 7):**
- Remove temperature → Task 1 (types, component, fixtures, all 3 temperature-referencing test files). ✅
- Swap in RUL fields → Task 1 (`predicted_rul_minutes`, `rul_estimate_kind`, `out_of_distribution` on type + component facts). ✅
- API client + TS types + MSW handlers per endpoint → Task 2. ✅
- Model health/telemetry panel → Task 3. ✅
- Ingestion-control panel → Task 4 (admin+supervisor gated). ✅
- Reports view → Task 5 (per-type RBAC). ✅
- Vitest + RTL + MSW per endpoint → every task is test-first. ✅

**Type consistency:** `MachineSummary` RUL fields (Task 1) match `src/api/schemas.py:26-28` exactly. `ModelHealth`/`ModelTelemetry`/`ReplayStatus`/`Report` (Task 2) match the route handlers verbatim; `Report.content` optional because `list_reports` omits it. `api.*` signatures in Task 2's Produces block are the exact ones Tasks 3–5 call. `RequireRole allow` prop and `NavItem.roles` use the existing `Role` union.

**RBAC:** ingestion route + nav gated admin+supervisor (matches backend `require_role`); reports route/nav open to all, operator create-form restricted to `machine_prognostic` (matches backend `_authorize_type`), backend still enforces on elevated types.

**Placeholder scan:** every code step contains complete literal code; no TODO/TBD/"add error handling" placeholders.

**No new deps:** all icons (`Activity`, `Waves`, `FileText`) are lucide-react (already installed); charts unchanged; no new libraries.
