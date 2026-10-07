import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider, useLiveEvents } from './LiveEventsProvider'
import { server } from '../test/server'
import { MockWebSocket } from '../test/mockWebSocket'
import { adminUser, alertFeedback, openAlerts, resolvedAlertWithFeedback, workOrders } from '../test/fixtures'

vi.mock('sonner', () => ({
  toast: { warning: vi.fn(), success: vi.fn(), info: vi.fn() },
}))

beforeEach(() => {
  vi.mocked(toast.warning).mockClear()
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.info).mockClear()
})

const incident = {
  id: 1,
  device_id: 'simdev-02',
  machine_id: 'sim-02',
  kind: 'silent',
  status: 'open',
  opened_at: '2026-10-06T11:50:30Z',
  last_seen_at: '2026-10-06T11:50:00Z',
  resolved_at: null,
  acknowledged_at: null,
  acknowledged_by: null,
}

function Probe() {
  const { connected, lastEvent } = useLiveEvents()
  return (
    <div>
      <div>{connected ? 'connected' : 'disconnected'}</div>
      <div>{lastEvent ? lastEvent.type : 'no-event'}</div>
    </div>
  )
}

function renderProvider() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <LiveEventsProvider>
          <Probe />
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>,
  )
}

async function openSocket() {
  renderProvider()
  await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
  const socket = MockWebSocket.instances[0]
  socket.emitOpen()
  return socket
}

describe('LiveEventsProvider', () => {
  it('does not open a socket until a user is known', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'nope' }, { status: 401 })))
    renderProvider()
    await waitFor(() => expect(screen.getByText('disconnected')).toBeInTheDocument())
    expect(MockWebSocket.instances).toHaveLength(0)
  })

  it('opens a socket once the user resolves and reports connected on open', async () => {
    renderProvider()
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))

    MockWebSocket.instances[0].emitOpen()

    expect(await screen.findByText('connected')).toBeInTheDocument()
  })

  it('records the last event and raises a warning toast for a new alert', async () => {
    renderProvider()
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    socket.emitMessage({
      type: 'alert_created',
      machine_id: 'm1',
      alert: { severity: 'high', health_state: 'critical' },
    })

    expect(await screen.findByText('alert_created')).toBeInTheDocument()
    expect(toast.warning).toHaveBeenCalled()
  })

  it('raises a success toast for a resolved alert', async () => {
    renderProvider()
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    socket.emitMessage({ type: 'alert_resolved', machine_id: 'm1' })

    expect(await screen.findByText('alert_resolved')).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalled()
  })

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

  it('raises a warning toast naming the node and machine when a sensor node goes silent', async () => {
    const socket = await openSocket()

    socket.emitMessage({ type: 'device_offline', device_id: 'simdev-02', machine_id: 'sim-02', incident, at: 'x' })

    expect(await screen.findByText('device_offline')).toBeInTheDocument()
    expect(toast.warning).toHaveBeenCalledWith('Sensor node simdev-02 (sim-02) went silent')
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('says "disconnected" for an LWT incident and omits an unknown machine', async () => {
    const socket = await openSocket()

    socket.emitMessage({
      type: 'device_offline',
      device_id: 'esp-07',
      machine_id: null,
      incident: { ...incident, device_id: 'esp-07', machine_id: null, kind: 'lwt' },
      at: 'x',
    })

    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith('Sensor node esp-07 disconnected'))
  })

  it('raises a success toast when a sensor node is back online', async () => {
    const socket = await openSocket()

    socket.emitMessage({
      type: 'device_online',
      device_id: 'simdev-02',
      machine_id: 'sim-02',
      incident: { ...incident, status: 'resolved', resolved_at: '2026-10-06T12:00:00Z' },
      at: 'x',
    })

    expect(await screen.findByText('device_online')).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalledWith('Sensor node simdev-02 is back online')
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('ignores an unknown event type: no toast and lastEvent unchanged', async () => {
    const socket = await openSocket()
    socket.emitMessage({ type: 'alert_created', machine_id: 'm1', alert: { severity: 'high', health_state: 'critical' } })
    expect(await screen.findByText('alert_created')).toBeInTheDocument()
    vi.mocked(toast.warning).mockClear()

    // A newer server's event (e.g. a future prediction-feedback feature).
    socket.emitMessage({ type: 'feedback_recorded', machine_id: 'm1', feedback: { id: 9 } })
    await new Promise((r) => setTimeout(r, 0))

    expect(screen.getByText('alert_created')).toBeInTheDocument()
    expect(toast.warning).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
    expect(toast.info).not.toHaveBeenCalled()
  })

  it('raises an info toast naming the order and machine when a work order is created', async () => {
    const socket = await openSocket()

    socket.emitMessage({ type: 'work_order_created', machine_id: 'm1', work_order: workOrders[0], at: 'x' })

    expect(await screen.findByText('work_order_created')).toBeInTheDocument()
    expect(toast.info).toHaveBeenCalledWith('Work order #1 opened for m1')
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('describes each work-order change, with a success toast for completion', async () => {
    const socket = await openSocket()

    socket.emitMessage({
      type: 'work_order_updated',
      machine_id: 'm2',
      work_order: workOrders[1],
      change: 'assigned',
      at: 'x',
    })
    expect(await screen.findByText('work_order_updated')).toBeInTheDocument()
    expect(toast.info).toHaveBeenCalledWith('Work order #2 assigned to Ollie Operator')

    socket.emitMessage({ type: 'work_order_updated', machine_id: 'm1', work_order: workOrders[2], change: 'started', at: 'x' })
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('Work order #3 started'))

    socket.emitMessage({ type: 'work_order_updated', machine_id: 'm2', work_order: workOrders[3], change: 'completed', at: 'x' })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Work order #4 completed'))
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('raises a warning toast with the level when an alert is paged', async () => {
    const socket = await openSocket()

    socket.emitMessage({
      type: 'alert_paged',
      machine_id: 'm1',
      alert: { ...openAlerts[0], page_level: 2 },
      page_level: 2,
      at: 'x',
    })

    expect(await screen.findByText('alert_paged')).toBeInTheDocument()
    expect(toast.warning).toHaveBeenCalledWith('m1: alert unacknowledged — paged level 2')
  })

  it('reconnects with backoff after the socket closes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderProvider()
      await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
      MockWebSocket.instances[0].emitOpen()
      expect(await screen.findByText('connected')).toBeInTheDocument()

      MockWebSocket.instances[0].emitClose()
      expect(await screen.findByText('disconnected')).toBeInTheDocument()

      await vi.advanceTimersByTimeAsync(1000)
      await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2))
    } finally {
      vi.useRealTimers()
    }
  })

  // The server rejects an expired session before accepting the socket, so
  // the browser only ever sees a close (1006) on a socket that never opened.
  it('checks the session when a socket closes before opening, and stops on a 401', async () => {
    let meCalls = 0
    let authorized = true
    server.use(
      http.get('/api/auth/me', () => {
        meCalls += 1
        return authorized
          ? HttpResponse.json(adminUser)
          : HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
      }),
    )
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderProvider()
      await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
      expect(meCalls).toBe(1)

      authorized = false
      MockWebSocket.instances[0].emitClose(1006)
      await waitFor(() => expect(meCalls).toBe(2))

      await vi.advanceTimersByTimeAsync(5000)
      expect(MockWebSocket.instances).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('reconnects after the session check passes', async () => {
    let meCalls = 0
    server.use(
      http.get('/api/auth/me', () => {
        meCalls += 1
        return HttpResponse.json(adminUser)
      }),
    )
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderProvider()
      await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
      MockWebSocket.instances[0].emitClose(1006)
      await waitFor(() => expect(meCalls).toBe(2))
      await vi.advanceTimersByTimeAsync(1000)
      await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2))
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not check the session when an open socket drops', async () => {
    let meCalls = 0
    server.use(
      http.get('/api/auth/me', () => {
        meCalls += 1
        return HttpResponse.json(adminUser)
      }),
    )
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderProvider()
      await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
      MockWebSocket.instances[0].emitOpen()
      MockWebSocket.instances[0].emitClose(1006)
      await vi.advanceTimersByTimeAsync(1000)
      await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2))
      expect(meCalls).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('raises a success toast naming the outcome when an alert is closed', async () => {
    const socket = await openSocket()

    socket.emitMessage({
      type: 'alert_closed',
      machine_id: 'm1',
      alert: resolvedAlertWithFeedback,
      feedback: alertFeedback,
      at: 'x',
    })

    expect(await screen.findByText('alert_closed')).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalledWith('m1: alert closed — Prevented by maintenance')
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('raises an info toast when an outcome is recorded', async () => {
    const socket = await openSocket()

    socket.emitMessage({
      type: 'alert_feedback_recorded',
      machine_id: 'm1',
      alert: resolvedAlertWithFeedback,
      feedback: { ...alertFeedback, outcome: 'false_alarm' },
      at: 'x',
    })

    expect(await screen.findByText('alert_feedback_recorded')).toBeInTheDocument()
    expect(toast.info).toHaveBeenCalledWith('m1: outcome recorded — False alarm')
    expect(toast.warning).not.toHaveBeenCalled()
  })
})
