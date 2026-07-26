import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import {
  kpiSummary,
  machineDetail,
  machineSummaries,
  openAlerts,
  trendPoints,
} from './fixtures'

// Default happy-path handlers. Individual tests override with server.use(...)
// to exercise loading/error/empty states.
export const handlers = [
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
]

export const server = setupServer(...handlers)
