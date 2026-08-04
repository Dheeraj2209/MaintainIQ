import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider, useLiveEvents } from './LiveEventsProvider'
import { server } from '../test/server'
import { MockWebSocket } from '../test/mockWebSocket'

vi.mock('sonner', () => ({
  toast: { warning: vi.fn(), success: vi.fn() },
}))

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
})
