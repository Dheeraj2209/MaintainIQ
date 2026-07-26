import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import { api, ApiError } from './client'

describe('api client', () => {
  it('fetches the KPI summary', async () => {
    const summary = await api.getKpiSummary()
    expect(summary.machine_count).toBe(2)
    expect(summary.prediction.status).toBe('available')
  })

  it('fetches the machine list', async () => {
    const machines = await api.getMachines()
    expect(machines).toHaveLength(2)
    expect(machines[0].machine_id).toBe('m1')
  })

  it('fetches trends with metric + limit as query params', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/machines/:id/trends', ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json([])
      }),
    )
    await api.getTrends('m1', 'temperature_c', 100)
    expect(capturedUrl).toContain('metric=temperature_c')
    expect(capturedUrl).toContain('limit=100')
  })

  it('posts a maintenance record', async () => {
    const rec = await api.logMaintenance({
      machine_id: 'm1',
      performed_at: '2026-07-20T10:00:00',
      description: 'greased',
    })
    expect(rec.id).toBeGreaterThan(0)
    expect(rec.description).toBe('greased')
  })

  it('throws ApiError with the backend detail on a 4xx', async () => {
    server.use(
      http.post('/api/maintenance', () =>
        HttpResponse.json({ detail: 'unknown machine_id' }, { status: 400 }),
      ),
    )
    await expect(
      api.logMaintenance({ machine_id: 'ghost', performed_at: '2026-07-20T10:00:00' }),
    ).rejects.toThrow(ApiError)
    await expect(
      api.logMaintenance({ machine_id: 'ghost', performed_at: '2026-07-20T10:00:00' }),
    ).rejects.toThrow('unknown machine_id')
  })
})
