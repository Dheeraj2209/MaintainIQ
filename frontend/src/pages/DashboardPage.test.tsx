import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { DashboardPage } from './DashboardPage'
import { server } from '../test/server'
import { api } from '../api/client'
import { MockWebSocket } from '../test/mockWebSocket'

function MachineDetailPlaceholder() {
  const { id } = useParams()
  return <div>Selected machine: {id}</div>
}

function harness() {
  return (
    <MemoryRouter initialEntries={['/']}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/machines/:id" element={<MachineDetailPlaceholder />} />
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

async function machineList() {
  return within(await screen.findByRole('region', { name: /machine list/i }))
}

describe('DashboardPage', () => {
  it('loads KPIs, the machine grid, and open alerts', async () => {
    render(harness())

    expect(await screen.findByRole('region', { name: /kpi/i })).toBeInTheDocument()
    const list = await machineList()
    expect(list.getByRole('button', { name: /m1/ })).toBeInTheDocument()
    expect(list.getByRole('button', { name: /m2/ })).toBeInTheDocument()

    const alertsSection = within(await screen.findByRole('region', { name: /open alerts/i }))
    expect(alertsSection.getByText(/m1 critical/i)).toBeInTheDocument()
  })

  it('navigates to a machine detail page when a machine card is clicked', async () => {
    render(harness())
    const list = await machineList()
    await userEvent.click(await list.findByRole('button', { name: /m1/ }))

    expect(await screen.findByText('Selected machine: m1')).toBeInTheDocument()
  })

  it('navigates to the alert\'s machine detail page when an open alert is clicked', async () => {
    render(harness())
    const alertsSection = within(await screen.findByRole('region', { name: /open alerts/i }))
    await userEvent.click(await alertsSection.findByText(/m1 critical/i))

    expect(await screen.findByText('Selected machine: m1')).toBeInTheDocument()
  })

  it('shows an error banner when fleet data fails to load', async () => {
    server.use(http.get('/api/machines', () => HttpResponse.json({ detail: 'machines unavailable' }, { status: 500 })))
    render(harness())

    expect(await screen.findByText(/machines unavailable/i)).toBeInTheDocument()
  })

  it('refetches fleet data when a live event arrives', async () => {
    const spy = vi.spyOn(api, 'getMachines')
    render(harness())
    await machineList()
    expect(spy).toHaveBeenCalledTimes(1)

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()
    socket.emitMessage({ type: 'alert_resolved', machine_id: 'm1', alert: { severity: 'high', health_state: 'healthy' } })

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    spy.mockRestore()
  })
})
