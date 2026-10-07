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

  it('passes machine_id to the devices endpoint only when given', async () => {
    const urls: string[] = []
    server.use(
      http.get('/api/telemetry/devices', ({ request }) => {
        urls.push(request.url)
        return HttpResponse.json([])
      }),
    )
    await api.getTelemetryDevices()
    await api.getTelemetryDevices({ machineId: 'sim 02' })
    expect(urls[0]).not.toContain('machine_id')
    expect(urls[1]).toContain('machine_id=sim%2002')
  })

  it('builds the incidents query from the given filters', async () => {
    const urls: string[] = []
    server.use(
      http.get('/api/telemetry/incidents', ({ request }) => {
        urls.push(request.url)
        return HttpResponse.json([])
      }),
    )
    await api.getDeviceIncidents()
    await api.getDeviceIncidents({ status: 'open', deviceId: 'simdev-02', limit: 50 })
    expect(new URL(urls[0]).search).toBe('')
    const params = new URL(urls[1]).searchParams
    expect(params.get('status')).toBe('open')
    expect(params.get('device_id')).toBe('simdev-02')
    expect(params.get('limit')).toBe('50')
  })

  it('fetches one device with its recent incidents and acknowledges an incident', async () => {
    const detail = await api.getTelemetryDevice('simdev-02')
    expect(detail.state).toBe('offline')
    expect(detail.recent_incidents.map((i) => i.id)).toEqual([1])

    const acked = await api.acknowledgeDeviceIncident(1)
    expect(acked.id).toBe(1)
    expect(acked.acknowledged_at).not.toBeNull()
  })

  it('builds the work-order list query from the given filters', async () => {
    const urls: string[] = []
    server.use(
      http.get('/api/work-orders', ({ request }) => {
        urls.push(request.url)
        return HttpResponse.json([])
      }),
    )
    await api.listWorkOrders()
    await api.listWorkOrders({ status: 'active', machineId: 'm 1', assignedTo: 3, alertId: 2 })
    expect(new URL(urls[0]).search).toBe('')
    const params = new URL(urls[1]).searchParams
    expect(params.get('status')).toBe('active')
    expect(params.get('machine_id')).toBe('m 1')
    expect(params.get('assigned_to')).toBe('3')
    expect(params.get('alert_id')).toBe('2')
  })

  it('fetches a work order with its timeline and the assignee list', async () => {
    const detail = await api.getWorkOrder(3)
    expect(detail.id).toBe(3)
    expect(detail.events.map((e) => e.event)).toEqual(['created', 'assigned', 'started'])

    const assignees = await api.getAssignees()
    expect(assignees.map((a) => a.role)).toEqual(['admin', 'supervisor', 'operator'])
  })

  it('posts work-order creation and every action to its own endpoint', async () => {
    const calls: { method: string; path: string; body: unknown }[] = []
    const record = async ({ request }: { request: Request }) => {
      const text = await request.text()
      calls.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null })
      return HttpResponse.json({ id: 9, machine_id: 'm1', status: 'open' })
    }
    server.use(
      http.post('/api/work-orders', record),
      http.post('/api/alerts/:id/work-order', record),
      http.patch('/api/work-orders/:id', record),
      http.post('/api/work-orders/:id/:action', record),
    )

    await api.createWorkOrder({ machine_id: 'm1', title: 'Replace bearing' })
    await api.createWorkOrderFromAlert(2)
    await api.createWorkOrderFromAlert(2, { assigned_to: 3 })
    await api.updateWorkOrder(9, { priority: 'high' })
    await api.assignWorkOrder(9, 3, 'take this')
    await api.startWorkOrder(9)
    await api.completeWorkOrder(9, { notes: 'done', maintenance_type: 'preventive' })
    await api.cancelWorkOrder(9, 'duplicate')

    expect(calls).toEqual([
      { method: 'POST', path: '/api/work-orders', body: { machine_id: 'm1', title: 'Replace bearing' } },
      { method: 'POST', path: '/api/alerts/2/work-order', body: {} },
      { method: 'POST', path: '/api/alerts/2/work-order', body: { assigned_to: 3 } },
      { method: 'PATCH', path: '/api/work-orders/9', body: { priority: 'high' } },
      { method: 'POST', path: '/api/work-orders/9/assign', body: { assigned_to: 3, note: 'take this' } },
      { method: 'POST', path: '/api/work-orders/9/start', body: null },
      { method: 'POST', path: '/api/work-orders/9/complete', body: { notes: 'done', maintenance_type: 'preventive' } },
      { method: 'POST', path: '/api/work-orders/9/cancel', body: { reason: 'duplicate' } },
    ])
  })

  it('closes an alert and records or reads its outcome on the feedback endpoints', async () => {
    const calls: { method: string; path: string; body: unknown }[] = []
    server.use(
      http.post('/api/alerts/:id/close', async ({ request }) => {
        calls.push({ method: request.method, path: new URL(request.url).pathname, body: await request.clone().json() })
        return undefined
      }),
      http.put('/api/alerts/:id/feedback', async ({ request }) => {
        calls.push({ method: request.method, path: new URL(request.url).pathname, body: await request.clone().json() })
        return undefined
      }),
    )

    const closed = await api.closeAlert(2, { outcome: 'false_alarm', notes: 'sensor knocked' })
    const put = await api.submitAlertFeedback(5, { outcome: 'confirmed_failure', actual_cause: 'imbalance' })
    const current = await api.getAlertFeedback(5)

    expect(calls).toEqual([
      { method: 'POST', path: '/api/alerts/2/close', body: { outcome: 'false_alarm', notes: 'sensor knocked' } },
      { method: 'PUT', path: '/api/alerts/5/feedback', body: { outcome: 'confirmed_failure', actual_cause: 'imbalance' } },
    ])
    expect(closed.closed).toBe(true)
    expect(closed.alert.status).toBe('resolved')
    expect(closed.feedback.outcome).toBe('false_alarm')
    expect(put.alert_id).toBe(5)
    expect(current).toBeNull()
  })

  it('fetches an alert explanation, with similar_limit only when given', async () => {
    const urls: string[] = []
    server.use(
      http.get('/api/alerts/:id/explanation', ({ request }) => {
        urls.push(request.url)
        return undefined
      }),
    )
    const explanation = await api.getAlertExplanation(2)
    await api.getAlertExplanation(2, 0)

    expect(explanation.alert.id).toBe(2)
    expect(new URL(urls[0]).pathname).toBe('/api/alerts/2/explanation')
    expect(new URL(urls[0]).search).toBe('')
    expect(new URL(urls[1]).search).toBe('?similar_limit=0')
  })

  it('fetches field accuracy, optionally for one model version', async () => {
    const urls: string[] = []
    server.use(
      http.get('/api/model/feedback-accuracy', ({ request }) => {
        urls.push(request.url)
        return undefined
      }),
    )
    const all = await api.getFieldAccuracy()
    await api.getFieldAccuracy({ modelVersion: 'xjtu rul' })

    expect(all.status).toBe('available')
    expect(new URL(urls[0]).search).toBe('')
    expect(new URL(urls[1]).searchParams.get('model_version')).toBe('xjtu rul')
  })

  it('downloads the feedback export with its filename and episode count', async () => {
    const result = await api.downloadFeedbackExport()
    expect(result.filename).toBe('maintainiq-feedback-features-20261007.csv')
    expect(result.episodeCount).toBe(1)
    expect(result.content).toMatch(/^bearing_id,condition,cycle/)
  })

  it('surfaces a 403 from the feedback export as an ApiError', async () => {
    server.use(
      http.get('/api/model/feedback/export', () => HttpResponse.json({ detail: 'forbidden' }, { status: 403 })),
    )
    await expect(api.downloadFeedbackExport()).rejects.toThrow('forbidden')
  })

  it('adds status and channel to the notifications query only when given', async () => {
    const urls: string[] = []
    server.use(
      http.get('/api/notifications', ({ request }) => {
        urls.push(request.url)
        return HttpResponse.json([])
      }),
    )
    await api.listNotifications()
    await api.listNotifications('failed', 'push')
    await api.listNotifications(undefined, 'email')
    expect(new URL(urls[0]).search).toBe('')
    expect(new URL(urls[1]).searchParams.get('status')).toBe('failed')
    expect(new URL(urls[1]).searchParams.get('channel')).toBe('push')
    expect(new URL(urls[2]).search).toBe('?channel=email')
  })

  it('reads the push config and posts, deletes and tests this device subscription', async () => {
    const calls: { method: string; path: string; body: string }[] = []
    const record = async ({ request }: { request: Request }) => {
      calls.push({ method: request.method, path: new URL(request.url).pathname, body: await request.text() })
    }
    server.use(
      http.post('/api/push/subscribe', async (info) => {
        await record(info)
        return HttpResponse.json({
          id: 3,
          endpoint: 'https://fcm.googleapis.com/fcm/send/abc',
          is_active: true,
          created_at: 'x',
          updated_at: 'x',
          last_used_at: null,
        })
      }),
      http.delete('/api/push/subscribe', async (info) => {
        await record(info)
        return new HttpResponse(null, { status: 204 })
      }),
      http.post('/api/push/test', async (info) => {
        await record(info)
        return HttpResponse.json({ sent: 2, failed: 0, expired: 0 })
      }),
    )

    expect(await api.getPushConfig()).toEqual({ enabled: true, public_key: expect.any(String) })
    const subscription = {
      endpoint: 'https://fcm.googleapis.com/fcm/send/abc',
      expirationTime: null,
      keys: { p256dh: 'BN', auth: 'tB' },
    }
    expect((await api.subscribePush(subscription)).id).toBe(3)
    expect(await api.unsubscribePush('https://fcm.googleapis.com/fcm/send/abc')).toBeUndefined()
    expect(await api.sendTestPush()).toEqual({ sent: 2, failed: 0, expired: 0 })

    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'POST /api/push/subscribe',
      'DELETE /api/push/subscribe',
      'POST /api/push/test',
    ])
    expect(JSON.parse(calls[0].body)).toEqual(subscription)
    expect(JSON.parse(calls[1].body)).toEqual({ endpoint: 'https://fcm.googleapis.com/fcm/send/abc' })
  })
})
