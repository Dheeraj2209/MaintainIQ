import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { MachineDetailPage } from './MachineDetailPage'
import { server } from '../test/server'
import { api } from '../api/client'
import { MockWebSocket } from '../test/mockWebSocket'
import { toast } from 'sonner'
import { workOrders } from '../test/fixtures'

function harness(initialPath = '/machines/m1') {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route path="/machines" element={<div>machines list page</div>} />
            <Route path="/machines/:id" element={<MachineDetailPage />} />
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('MachineDetailPage', () => {
  it('renders the machine detail and a link back to the machines list', async () => {
    render(harness())

    expect(await screen.findByRole('heading', { name: /^m1$/i })).toBeInTheDocument()
    expect(screen.getByText(/bearing_wear/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: /back to machines/i }))
    expect(await screen.findByText('machines list page')).toBeInTheDocument()
  })

  it('shows an error state when the machine fails to load', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json({ detail: 'machine not found' }, { status: 404 })))
    render(harness())

    expect(await screen.findByText(/machine not found/i)).toBeInTheDocument()
  })

  it('refreshes the detail after a maintenance record is logged', async () => {
    const spy = vi.spyOn(api, 'getMachine')
    render(harness())
    await screen.findByRole('heading', { name: /^m1$/i })
    expect(spy).toHaveBeenCalledTimes(1)

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-23T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(await screen.findByText(/logged\./i)).toBeInTheDocument()
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    spy.mockRestore()
  })

  it('refreshes when a live event arrives for this machine but not for another', async () => {
    const spy = vi.spyOn(api, 'getMachine')
    render(harness())
    await screen.findByRole('heading', { name: /^m1$/i })
    expect(spy).toHaveBeenCalledTimes(1)

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    socket.emitMessage({ type: 'alert_created', machine_id: 'm2', alert: { severity: 'high', health_state: 'critical' } })
    await new Promise((r) => setTimeout(r, 0))
    expect(spy).toHaveBeenCalledTimes(1)

    socket.emitMessage({ type: 'alert_resolved', machine_id: 'm1', alert: { severity: 'high', health_state: 'healthy' } })
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    spy.mockRestore()
  })

  it('refreshes the node chip on a device event for this machine but not for an unassigned node', async () => {
    const spy = vi.spyOn(api, 'getTelemetryDevices')
    render(harness('/machines/sim-02'))
    await screen.findByText('Sensor node')
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1))

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    const incident = {
      id: 7,
      device_id: 'esp-07',
      machine_id: null,
      kind: 'silent',
      status: 'open',
      opened_at: '2026-10-06T11:50:30Z',
      last_seen_at: null,
      resolved_at: null,
      acknowledged_at: null,
      acknowledged_by: null,
    }
    socket.emitMessage({ type: 'device_offline', device_id: 'esp-07', machine_id: null, incident, at: 'x' })
    await new Promise((r) => setTimeout(r, 0))
    expect(spy).toHaveBeenCalledTimes(1)

    socket.emitMessage({
      type: 'device_offline',
      device_id: 'simdev-02',
      machine_id: 'sim-02',
      incident: { ...incident, id: 1, device_id: 'simdev-02', machine_id: 'sim-02' },
      at: 'x',
    })
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    spy.mockRestore()
  })

  it('keeps the work-order dialog open across a live event that remounts the detail', async () => {
    const spy = vi.spyOn(api, 'getMachine')
    render(harness())
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    await userEvent.click(await screen.findByText('m1 critical'))
    await userEvent.click(screen.getByRole('button', { name: /create work order/i }))
    const dialog = await screen.findByRole('dialog', { name: /create work order/i })
    await userEvent.type(within(dialog).getByLabelText(/description/i), 'half-typed')

    socket.emitMessage({ type: 'alert_escalated', machine_id: 'm1', alert: { severity: 'high', health_state: 'critical' } })
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))

    const still = screen.getByRole('dialog', { name: /create work order/i })
    expect(within(still).getByLabelText(/description/i)).toHaveValue('half-typed')
    spy.mockRestore()
  })

  it('creates the work order, toasts and refreshes the detail', async () => {
    const posted: string[] = []
    server.use(
      http.post('/api/alerts/:id/work-order', ({ params }) => {
        posted.push(String(params.id))
        return HttpResponse.json({ ...workOrders[0], id: 10, alert_id: 2 }, { status: 201 })
      }),
    )
    const spy = vi.spyOn(api, 'getMachine')
    render(harness())

    await userEvent.click(await screen.findByText('m1 critical'))
    await userEvent.click(screen.getByRole('button', { name: /create work order/i }))
    const dialog = await screen.findByRole('dialog', { name: /create work order/i })
    await userEvent.click(within(dialog).getByRole('button', { name: /create work order/i }))

    await waitFor(() => expect(posted).toEqual(['2']))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    spy.mockRestore()
  })

  it('keeps the close dialog open across a live event that remounts the detail', async () => {
    const spy = vi.spyOn(api, 'getMachine')
    render(harness())
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    await userEvent.click(await screen.findByText('m1 critical'))
    await userEvent.click(screen.getByRole('button', { name: /^close alert$/i }))
    const dialog = await screen.findByRole('dialog', { name: /close alert/i })
    await userEvent.type(within(dialog).getByLabelText(/notes/i), 'half-typed')

    socket.emitMessage({ type: 'alert_escalated', machine_id: 'm1', alert: { severity: 'high', health_state: 'critical' } })
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))

    const still = screen.getByRole('dialog', { name: /close alert/i })
    expect(within(still).getByLabelText(/notes/i)).toHaveValue('half-typed')
    spy.mockRestore()
  })

  it('opens the "Why this alert?" panel for the selected alert and keeps it across a remount', async () => {
    const spy = vi.spyOn(api, 'getMachine')
    render(harness())
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    expect(await screen.findByRole('button', { name: /^why this alert\?$/i })).toBeDisabled()
    await userEvent.click(await screen.findByText('m1 critical'))
    await userEvent.click(screen.getByRole('button', { name: /^why this alert\?$/i }))
    const panel = await screen.findByRole('dialog', { name: /why this alert\? — m1/i })
    await within(panel).findByRole('heading', { name: 'Similar past incidents' })

    socket.emitMessage({ type: 'alert_escalated', machine_id: 'm1', alert: { id: 7, severity: 'high', health_state: 'critical' } })
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('dialog', { name: /why this alert\? — m1/i })).toBeInTheDocument()

    await userEvent.click(within(panel).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    spy.mockRestore()
  })

  it('opens the panel from an alert row "Why?" button', async () => {
    render(harness())
    await userEvent.click(await screen.findByRole('button', { name: 'Why this alert? Alert #2' }))
    expect(await screen.findByRole('dialog', { name: /why this alert\? — m1/i })).toBeInTheDocument()
  })

  it('closes the alert, toasts and refreshes the detail', async () => {
    const closed: string[] = []
    server.use(
      http.post('/api/alerts/:id/close', ({ params }) => {
        closed.push(String(params.id))
        return undefined
      }),
    )
    const success = vi.spyOn(toast, 'success')
    const spy = vi.spyOn(api, 'getMachine')
    render(harness())

    await userEvent.click(await screen.findByText('m1 critical'))
    await userEvent.click(screen.getByRole('button', { name: /^close alert$/i }))
    const dialog = await screen.findByRole('dialog', { name: /close alert/i })
    await userEvent.click(within(dialog).getByRole('radio', { name: /maintenance/i }))
    await userEvent.click(within(dialog).getByRole('button', { name: /^close alert$/i }))

    await waitFor(() => expect(closed).toEqual(['2']))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    expect(success).toHaveBeenCalledWith('Alert #2 closed')
    spy.mockRestore()
    success.mockRestore()
  })

  it('opens the printable QR label from the QR label button', async () => {
    render(harness())
    await screen.findByRole('heading', { name: /^m1$/i })

    await userEvent.click(screen.getByRole('button', { name: /qr label/i }))

    const dialog = await screen.findByRole('dialog', { name: /qr label/i })
    expect(await within(dialog).findByRole('img', { name: 'QR code for machine m1' })).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: /^close$/i }))
    expect(screen.queryByRole('dialog', { name: /qr label/i })).not.toBeInTheDocument()
  })
})
