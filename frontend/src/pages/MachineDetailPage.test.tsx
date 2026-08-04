import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { MachineDetailPage } from './MachineDetailPage'
import { server } from '../test/server'
import { api } from '../api/client'
import { MockWebSocket } from '../test/mockWebSocket'

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
})
