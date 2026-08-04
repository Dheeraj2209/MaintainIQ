import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { AlertsPage } from './AlertsPage'
import { server } from '../test/server'
import type { Alert } from '../api/types'

function MachineDetailPlaceholder() {
  const { id } = useParams()
  return <div>Selected machine: {id}</div>
}

function openAlertsFixtureAcknowledged(): Alert {
  return {
    id: 2,
    machine_id: 'm1',
    opened_at: '2003-10-22T13:00:00+00:00',
    resolved_at: null,
    severity: 'high',
    health_state: 'critical',
    probable_cause: 'bearing_wear',
    message: 'm1 critical',
    status: 'open',
    source: 'ml',
    acknowledged_at: '2026-08-04T00:00:00+00:00',
    acknowledged_by: 1,
  }
}

function harness() {
  return (
    <MemoryRouter initialEntries={['/alerts']}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/machines/:id" element={<MachineDetailPlaceholder />} />
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('AlertsPage', () => {
  it('loads and renders open alerts by default', async () => {
    render(harness())
    expect(await screen.findByText(/m1 critical/i)).toBeInTheDocument()
    expect(screen.getByText('1 alerts matching this filter')).toBeInTheDocument()
  })

  it('shows an empty state when no alerts match the filter', async () => {
    server.use(http.get('/api/alerts', () => HttpResponse.json([])))
    render(harness())
    expect(await screen.findByText(/no alerts match this filter/i)).toBeInTheDocument()
  })

  it('shows an error banner when alerts fail to load', async () => {
    server.use(http.get('/api/alerts', () => HttpResponse.json({ detail: 'alerts unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/alerts unavailable/i)).toBeInTheDocument()
  })

  it('sorts by severity when requested', async () => {
    const alerts: Alert[] = [
      {
        id: 1,
        machine_id: 'm1',
        opened_at: '2003-10-22T12:00:00+00:00',
        resolved_at: null,
        severity: 'high',
        health_state: 'critical',
        probable_cause: null,
        message: 'm1 high',
        status: 'open',
        source: 'ml',
        acknowledged_at: null,
        acknowledged_by: null,
      },
      {
        id: 2,
        machine_id: 'm2',
        opened_at: '2003-10-22T13:00:00+00:00',
        resolved_at: null,
        severity: 'low',
        health_state: 'degrading',
        probable_cause: null,
        message: 'm2 low',
        status: 'open',
        source: 'ml',
        acknowledged_at: null,
        acknowledged_by: null,
      },
    ]
    server.use(http.get('/api/alerts', () => HttpResponse.json(alerts)))
    render(harness())
    await screen.findByText(/m1 high/i)

    // Default sort is "Newest" (opened_at desc) — m2 opened later, so it leads.
    const rowsBefore = screen.getAllByRole('row').slice(1)
    expect(within(rowsBefore[0]).getByText(/m2 low/i)).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText(/sort by/i), 'severity')

    // Switching to severity puts the high-severity alert first regardless of age.
    const rowsAfter = screen.getAllByRole('row').slice(1)
    expect(within(rowsAfter[0]).getByText(/m1 high/i)).toBeInTheDocument()
    expect(within(rowsAfter[1]).getByText(/m2 low/i)).toBeInTheDocument()
  })

  it('navigates to the machine detail page when a row is clicked', async () => {
    render(harness())
    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')
    await userEvent.click(row)
    expect(await screen.findByText('Selected machine: m1')).toBeInTheDocument()
  })

  it('acknowledges an alert and updates the row without navigating', async () => {
    render(harness())
    await screen.findByText(/m1 critical/i)

    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')
    const button = within(row).getByRole('button', { name: /acknowledge/i })
    await userEvent.click(button)

    expect(await within(row).findByText(/acknowledged/i)).toBeInTheDocument()
    // Clicking the button must not trigger the row's navigate-on-click handler.
    expect(screen.queryByText(/selected machine: m1/i)).not.toBeInTheDocument()
  })

  it('does not show an acknowledge button for an already-acknowledged alert', async () => {
    server.use(
      http.get('/api/alerts', () =>
        HttpResponse.json([{ ...openAlertsFixtureAcknowledged() }]),
      ),
    )
    render(harness())
    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')
    expect(within(row).queryByRole('button', { name: /acknowledge/i })).not.toBeInTheDocument()
  })
})
