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
    await api.getTrends('m1', 'rul_minutes', 100)
    expect(capturedUrl).toContain('metric=rul_minutes')
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
})
