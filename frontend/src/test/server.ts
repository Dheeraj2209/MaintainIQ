import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import {
  adminUser,
  kpiSummary,
  machineDetail,
  machineSummaries,
  notifications,
  openAlerts,
  trendPoints,
  users,
} from './fixtures'

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
  http.get('/api/machines', () => HttpResponse.json(machineSummaries)),
  http.get('/api/machines/:id', () => HttpResponse.json(machineDetail)),
  http.get('/api/machines/:id/trends', () => HttpResponse.json(trendPoints)),
  http.get('/api/alerts', () => HttpResponse.json(openAlerts)),
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

  http.get('/api/notifications', () => HttpResponse.json(notifications)),

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
]

export const server = setupServer(...handlers)
