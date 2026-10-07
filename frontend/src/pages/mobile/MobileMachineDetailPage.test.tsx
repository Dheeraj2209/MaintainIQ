import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../../auth/AuthContext'
import { LiveEventsProvider } from '../../realtime/LiveEventsProvider'
import { server } from '../../test/server'
import { commissioningMachine, heldMachine, machineDetail } from '../../test/fixtures'
import { MobileMachineDetailPage } from './MobileMachineDetailPage'
import { MobileMachinesPage } from './MobileMachinesPage'

function harness(path: string) {
  return (
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route path="/m/machines" element={<MobileMachinesPage />} />
            <Route path="/m/machines/:id" element={<MobileMachineDetailPage />} />
            <Route path="/m/scan" element={<div>scan page</div>} />
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('MobileMachineDetailPage', () => {
  it('shows health, remaining life and the open alerts with quick actions', async () => {
    server.use(
      http.get('/api/machines/:id', () =>
        HttpResponse.json({
          ...structuredClone(machineDetail),
          alerts: machineDetail.alerts.map((a) => ({ ...a, acknowledged_at: null, acknowledged_by: null })),
        }),
      ),
    )
    render(harness('/m/machines/m1'))

    const health = await screen.findByRole('region', { name: 'Machine health' })
    expect(within(health).getByRole('heading', { name: 'm1' })).toBeInTheDocument()
    expect(within(health).getByText('Critical')).toBeInTheDocument()
    expect(within(health).getByText('43 min')).toBeInTheDocument()

    const card = within(screen.getByRole('article', { name: /alert #2 on m1/i }))
    expect(card.getByRole('button', { name: 'Acknowledge alert #2' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Full machine page' })).toHaveAttribute('href', '/machines/m1')
  })

  it('says the machine is unknown on a 404 and offers to scan again', async () => {
    server.use(
      http.get('/api/machines/:id', () => HttpResponse.json({ detail: 'unknown machine: nope' }, { status: 404 })),
    )
    render(harness('/m/machines/nope'))

    expect(await screen.findByText("Unknown machine 'nope'")).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Scan again' }))
    expect(await screen.findByText('scan page')).toBeInTheDocument()
  })
})

describe('MobileMachineDetailPage health ratchet', () => {
  it('shows the held badge and the held state in place of the remaining life', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json({ ...structuredClone(machineDetail), health: heldMachine })))
    render(harness('/m/machines/m1'))

    const health = await screen.findByRole('region', { name: 'Machine health' })
    expect(within(health).getByText(/held since/i)).toBeInTheDocument()
    expect(within(health).getByText('Held: Critical (current signal: Healthy)')).toBeInTheDocument()
    expect(within(health).queryByText('43 min')).not.toBeInTheDocument()
  })

  it('shows commissioning progress instead of healthy', async () => {
    server.use(
      http.get('/api/machines/:id', () => HttpResponse.json({ ...structuredClone(machineDetail), health: commissioningMachine })),
    )
    render(harness('/m/machines/m2'))

    const health = await screen.findByRole('region', { name: 'Machine health' })
    expect(within(health).getByText('Commissioning 7/20')).toBeInTheDocument()
    expect(within(health).queryByText('Healthy')).not.toBeInTheDocument()
    expect(within(health).getByText('Signal: Faulty')).toBeInTheDocument()
  })
})

describe('MobileMachinesPage', () => {
  it('lists machines and filters them by id', async () => {
    render(harness('/m/machines'))

    expect(await screen.findByRole('link', { name: /m1/ })).toHaveAttribute('href', '/m/machines/m1')
    expect(screen.getByRole('link', { name: /m2/ })).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/filter machines/i), 'm2')

    expect(screen.queryByRole('link', { name: /m1/ })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /m2/ })).toBeInTheDocument()
  })
})
