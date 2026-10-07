import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import {
  adminUser,
  alertExplanation,
  alertFeedback,
  assignees,
  deviceDetailFor,
  deviceIncidents,
  fieldAccuracy,
  kpiDetailWith,
  kpiSummary,
  machineDetail,
  machineSummaries,
  modelHealth,
  modelTelemetry,
  notifications,
  openAlerts,
  pushConfig,
  pushSubscription,
  replayStatus,
  reportDetail,
  reports,
  systemKpisNotApplicable,
  telemetryDevices,
  telemetryStatusEnabled,
  trendPoints,
  users,
  workOrderDetail,
  workOrders,
} from './fixtures'
import type { Alert, AlertFeedback, WorkOrder, WorkOrderStatus } from '../api/types'

const ACTIVE_WORK_ORDER_STATUSES: WorkOrderStatus[] = ['open', 'assigned', 'in_progress']
const NOW = '2026-10-07T12:00:00+00:00'

// A cloned list entry (the first one for an unknown id) with `changes`
// applied. Work-order handlers never mutate the shared fixtures.
function workOrderWith(id: number, changes: Partial<WorkOrder>): WorkOrder {
  const base = workOrders.find((w) => w.id === id) ?? workOrders[0]
  return { ...structuredClone(base), id, updated_at: NOW, ...changes }
}

// Action bodies are optional on the server (start never has one).
async function jsonBody(request: Request): Promise<Record<string, unknown>> {
  const text = await request.text()
  return text ? (JSON.parse(text) as Record<string, unknown>) : {}
}

// The fixture feedback rewritten for `alertId` from a close/PUT body.
function feedbackFor(alertId: number, body: Record<string, unknown>): AlertFeedback {
  return {
    ...structuredClone(alertFeedback),
    alert_id: alertId,
    recorded_by: 1,
    recorded_by_name: 'Ada Admin',
    recorded_at: NOW,
    actual_cause: null,
    actual_failure_at: null,
    notes: null,
    work_order_id: null,
    ...(body as Partial<AlertFeedback>),
  }
}

// Default happy-path handlers. Individual tests override with server.use(...)
// to exercise loading/error/empty/role-gated states. Auth defaults to "already
// logged in as admin" so most page tests don't need to think about auth at
// all; auth-specific tests override /api/auth/me to simulate anonymous/other
// roles instead.
export const handlers = [
  http.get('/api/auth/me', () => HttpResponse.json(adminUser)),
  http.post('/api/auth/login', () => HttpResponse.json(adminUser)),
  http.post('/api/auth/logout', () => HttpResponse.json({ status: 'ok' })),

  http.get('/api/kpis', () => HttpResponse.json(kpiSummary)),
  http.get('/api/kpis/detail', () => HttpResponse.json(kpiDetailWith(systemKpisNotApplicable))),
  http.get('/api/machines', () => HttpResponse.json(machineSummaries)),
  http.get('/api/machines/:id', () => HttpResponse.json(machineDetail)),
  http.get('/api/machines/:id/trends', () => HttpResponse.json(trendPoints)),
  http.get('/api/alerts', () => HttpResponse.json(openAlerts)),
  http.post('/api/alerts/:id/acknowledge', ({ params }) => {
    const alert = openAlerts.find((a) => a.id === Number(params.id)) ?? openAlerts[0]
    // Mutate in place so a subsequent GET /api/alerts (triggered by the page's
    // post-acknowledge reload) reflects the acknowledged state instead of
    // silently reverting to the pristine fixture.
    alert.acknowledged_at = '2026-08-04T00:00:00+00:00'
    alert.acknowledged_by = 1
    return HttpResponse.json(alert)
  }),
  // Prediction feedback. Like the work-order handlers, these answer with
  // clones and never mutate the shared fixtures.
  http.post('/api/alerts/:id/close', async ({ request, params }) => {
    const id = Number(params.id)
    const base: Alert = openAlerts.find((a) => a.id === id) ?? { ...openAlerts[0], id }
    const feedback = feedbackFor(id, await jsonBody(request))
    const wasOpen = base.status === 'open'
    return HttpResponse.json({
      alert: {
        ...structuredClone(base),
        status: 'resolved',
        resolved_at: base.resolved_at ?? NOW,
        closed_by: wasOpen ? 1 : (base.closed_by ?? null),
        feedback,
      },
      feedback,
      closed: wasOpen,
    })
  }),
  http.put('/api/alerts/:id/feedback', async ({ request, params }) =>
    HttpResponse.json(feedbackFor(Number(params.id), await jsonBody(request))),
  ),
  http.get('/api/alerts/:id/feedback', () => HttpResponse.json(null)),
  // "Why this alert?": the fixture snapshot re-keyed to the requested alert;
  // 999 is the unknown alert (the route's 404).
  http.get('/api/alerts/:id/explanation', ({ params }) => {
    const id = Number(params.id)
    if (id === 999) return HttpResponse.json({ detail: 'unknown alert: 999' }, { status: 404 })
    const body = structuredClone(alertExplanation)
    return HttpResponse.json({ ...body, alert: { ...body.alert, id } })
  }),
  http.post('/api/alerts/:id/work-order', async ({ request, params }) => {
    const body = await jsonBody(request)
    const alert = openAlerts.find((a) => a.id === Number(params.id)) ?? openAlerts[0]
    return HttpResponse.json(
      workOrderWith(10, {
        alert_id: alert.id,
        machine_id: alert.machine_id,
        title: alert.message ?? `${alert.machine_id}: ${alert.health_state} alert`,
        ...body,
        status: body.assigned_to ? 'assigned' : 'open',
        created_at: NOW,
      }),
      { status: 201 },
    )
  }),

  // Work orders. Honors ?status= (including `active`), ?assigned_to= and
  // ?machine_id= like the backend; /assignees is registered before /:id, as
  // on the server.
  http.get('/api/work-orders', ({ request }) => {
    const params = new URL(request.url).searchParams
    const status = params.get('status')
    const assignedTo = params.get('assigned_to')
    const machineId = params.get('machine_id')
    let rows = workOrders
    if (status === 'active') rows = rows.filter((w) => ACTIVE_WORK_ORDER_STATUSES.includes(w.status))
    else if (status) rows = rows.filter((w) => w.status === status)
    if (assignedTo) rows = rows.filter((w) => w.assigned_to === Number(assignedTo))
    if (machineId) rows = rows.filter((w) => w.machine_id === machineId)
    return HttpResponse.json(structuredClone(rows))
  }),
  http.get('/api/work-orders/assignees', () => HttpResponse.json(structuredClone(assignees))),
  http.get('/api/work-orders/:id', ({ params }) =>
    HttpResponse.json({ ...structuredClone(workOrderDetail), id: Number(params.id) }),
  ),
  http.post('/api/work-orders', async ({ request }) => {
    const body = await jsonBody(request)
    return HttpResponse.json(
      workOrderWith(11, { alert_id: null, ...body, status: body.assigned_to ? 'assigned' : 'open', created_at: NOW }),
      { status: 201 },
    )
  }),
  http.patch('/api/work-orders/:id', async ({ request, params }) =>
    HttpResponse.json(workOrderWith(Number(params.id), await jsonBody(request))),
  ),
  http.post('/api/work-orders/:id/assign', async ({ request, params }) => {
    const body = await jsonBody(request)
    const id = Number(params.id)
    const current = workOrders.find((w) => w.id === id)
    const assignee = assignees.find((a) => a.id === body.assigned_to)
    return HttpResponse.json(
      workOrderWith(id, {
        status: !current || current.status === 'open' ? 'assigned' : current.status,
        assigned_to: (body.assigned_to as number | undefined) ?? null,
        assigned_to_name: assignee?.name ?? null,
      }),
    )
  }),
  http.post('/api/work-orders/:id/start', ({ params }) =>
    HttpResponse.json(workOrderWith(Number(params.id), { status: 'in_progress', started_at: NOW })),
  ),
  http.post('/api/work-orders/:id/complete', async ({ request, params }) => {
    const body = await jsonBody(request)
    return HttpResponse.json(
      workOrderWith(Number(params.id), {
        status: 'done',
        completed_at: NOW,
        maintenance_record_id: 99,
        notes: (body.notes as string | undefined) ?? null,
      }),
    )
  }),
  http.post('/api/work-orders/:id/cancel', ({ params }) =>
    HttpResponse.json(workOrderWith(Number(params.id), { status: 'cancelled', cancelled_at: NOW })),
  ),

  http.get('/api/maintenance/:id', () => HttpResponse.json([])),
  http.post('/api/maintenance', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json(
      { id: 1, created_at: '2026-07-23T00:00:00+00:00', ...body },
      { status: 201 },
    )
  }),

  http.get('/api/users', () => HttpResponse.json(users)),
  http.post('/api/users', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    return HttpResponse.json(
      { id: 99, is_active: true, created_at: '2026-07-23T00:00:00+00:00', ...body },
      { status: 201 },
    )
  }),
  http.patch('/api/users/:id', async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>
    const existing = users.find((u) => u.id === Number(params.id)) ?? adminUser
    return HttpResponse.json({ ...existing, ...body })
  }),

  // Honors ?status= and ?channel= like the backend.
  http.get('/api/notifications', ({ request }) => {
    const params = new URL(request.url).searchParams
    const status = params.get('status')
    const channel = params.get('channel')
    let rows = notifications
    if (status) rows = rows.filter((n) => n.status === status)
    if (channel) rows = rows.filter((n) => (n.channel ?? 'email') === channel)
    return HttpResponse.json(structuredClone(rows))
  }),

  // Web Push (design/2026-10-07-mobile-operator-pwa-design.md): push is
  // configured, and every call succeeds. The AppShell's "This device" menu
  // and /m/settings read the config.
  http.get('/api/push/vapid-public-key', () => HttpResponse.json(structuredClone(pushConfig))),
  http.post('/api/push/subscribe', async ({ request }) => {
    const body = await jsonBody(request)
    return HttpResponse.json({ ...structuredClone(pushSubscription), endpoint: body.endpoint ?? pushSubscription.endpoint })
  }),
  http.delete('/api/push/subscribe', () => new HttpResponse(null, { status: 204 })),
  http.post('/api/push/test', () => HttpResponse.json({ sent: 1, failed: 0, expired: 0 })),

  http.post('/api/demo/simulate-fault', async ({ request }) => {
    const body = (await request.json()) as { machine_id: string; severity: string }
    return HttpResponse.json({
      machine_id: body.machine_id,
      health_state: body.severity,
      probable_cause: body.severity === 'healthy' ? null : 'bearing_wear',
      alert: null,
      emails_sent: 0,
    })
  }),

  http.get('/api/model/health', () => HttpResponse.json(modelHealth)),
  http.get('/api/model/telemetry', () => HttpResponse.json(modelTelemetry)),
  http.get('/api/model/feedback-accuracy', () => HttpResponse.json(structuredClone(fieldAccuracy))),
  http.get('/api/model/feedback/export', () =>
    new HttpResponse('bearing_id,condition,cycle,elapsed_minutes,rul_minutes,speed_rpm,load_kn,source_file\n', {
      headers: {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': 'attachment; filename="maintainiq-feedback-features-20261007.csv"',
        'X-Episode-Count': '1',
        'X-Row-Count': '0',
      },
    }),
  ),

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

  http.get('/api/telemetry/status', () => HttpResponse.json(telemetryStatusEnabled)),
  // Honors ?machine_id= like the backend, so the machine-detail "Sensor node"
  // fact sees no node for the XJTU-style machines (m1, m2) by default.
  http.get('/api/telemetry/devices', ({ request }) => {
    const machineId = new URL(request.url).searchParams.get('machine_id')
    const rows = machineId ? telemetryDevices.filter((d) => d.machine_id === machineId) : telemetryDevices
    return HttpResponse.json(structuredClone(rows))
  }),
  http.get('/api/telemetry/devices/:deviceId', ({ params }) =>
    HttpResponse.json(deviceDetailFor(String(params.deviceId))),
  ),
  http.get('/api/telemetry/incidents', ({ request }) => {
    const status = new URL(request.url).searchParams.get('status')
    const rows = status ? deviceIncidents.filter((i) => i.status === status) : deviceIncidents
    return HttpResponse.json(structuredClone(rows))
  }),
  // Returns an acknowledged *copy*: unlike the alert ack handler above, it
  // never mutates the shared fixture, so tests cannot leak state into each other.
  http.post('/api/telemetry/incidents/:id/acknowledge', ({ params }) => {
    const incident = deviceIncidents.find((i) => i.id === Number(params.id)) ?? deviceIncidents[0]
    return HttpResponse.json({
      ...structuredClone(incident),
      acknowledged_at: incident.acknowledged_at ?? '2026-10-06T12:00:00Z',
      acknowledged_by: incident.acknowledged_by ?? 1,
    })
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
]

export const server = setupServer(...handlers)
