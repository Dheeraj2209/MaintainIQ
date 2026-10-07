import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { AlertsPage } from './AlertsPage'
import { server } from '../test/server'
import { toast } from 'sonner'
import { openAlerts, operatorUser, resolvedAlertWithFeedback } from '../test/fixtures'
import type { Alert } from '../api/types'

function MachineDetailPlaceholder() {
  const { id } = useParams()
  return <div>Selected machine: {id}</div>
}

function WorkOrderPlaceholder() {
  const { id } = useParams()
  return <div>Selected work order: {id}</div>
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
    page_level: 0,
    last_paged_at: null,
  }
}

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

// Mirrors App.tsx: one route for the list and /alerts/:id (the "Why?" drawer).
function harness(path = '/alerts') {
  return (
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <LiveEventsProvider>
          <LocationProbe />
          <Routes>
            <Route path="/alerts/:id?" element={<AlertsPage />} />
            <Route path="/machines/:id" element={<MachineDetailPlaceholder />} />
            <Route path="/work-orders/:id" element={<WorkOrderPlaceholder />} />
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
        page_level: 0,
        last_paged_at: null,
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
        page_level: 0,
        last_paged_at: null,
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

  it('creates a work order from a row without navigating, then re-fetches', async () => {
    let alertGets = 0
    const posted: string[] = []
    server.use(
      http.get('/api/alerts', () => {
        alertGets += 1
        return HttpResponse.json(structuredClone(openAlerts))
      }),
      http.post('/api/alerts/:id/work-order', ({ params }) => {
        posted.push(String(params.id))
        return HttpResponse.json({ id: 10, machine_id: 'm1', alert_id: 2, status: 'open' }, { status: 201 })
      }),
    )
    render(harness())
    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')
    await waitFor(() => expect(alertGets).toBe(1))

    await userEvent.click(within(row).getByRole('button', { name: /create work order/i }))
    const dialog = await screen.findByRole('dialog', { name: /create work order/i })
    // The row's navigate-on-click handler must not fire.
    expect(screen.queryByText(/selected machine/i)).not.toBeInTheDocument()
    expect(within(dialog).getByLabelText(/title/i)).toHaveValue('m1 critical')

    await userEvent.click(within(dialog).getByRole('button', { name: /create work order/i }))

    await waitFor(() => expect(posted).toEqual(['2']))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(alertGets).toBe(2))
    expect(screen.queryByText(/selected machine/i)).not.toBeInTheDocument()
  })

  it('links to the active work order instead of offering to create one', async () => {
    server.use(
      http.get('/api/alerts', () =>
        HttpResponse.json([{ ...openAlertsFixtureAcknowledged(), active_work_order_id: 7 }]),
      ),
    )
    render(harness())
    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')

    expect(within(row).queryByRole('button', { name: /create work order/i })).not.toBeInTheDocument()
    await userEvent.click(within(row).getByRole('link', { name: /wo #7/i }))
    expect(await screen.findByText('Selected work order: 7')).toBeInTheDocument()
    expect(screen.queryByText(/selected machine/i)).not.toBeInTheDocument()
  })

  it('badges the paging level of an unacknowledged alert', async () => {
    server.use(
      http.get('/api/alerts', () =>
        HttpResponse.json([
          { ...structuredClone(openAlerts[0]), page_level: 2, last_paged_at: '2026-10-07T12:30:00+00:00' },
        ]),
      ),
    )
    render(harness())
    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')

    const badge = within(row).getByText('Paged L2')
    expect(badge).toHaveAttribute('title', 'Last paged 2026-10-07T12:30:00+00:00')
  })

  it('shows no paging badge for an alert that was only paged at level 0', async () => {
    render(harness())
    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')
    expect(within(row).queryByText(/paged/i)).not.toBeInTheDocument()
  })

  it('opens the close dialog from a row without navigating, then toasts and re-fetches', async () => {
    let alertGets = 0
    const closed: string[] = []
    server.use(
      http.get('/api/alerts', () => {
        alertGets += 1
        return HttpResponse.json(structuredClone(openAlerts))
      }),
      http.post('/api/alerts/:id/close', ({ params }) => {
        closed.push(String(params.id))
        return undefined
      }),
    )
    const success = vi.spyOn(toast, 'success')
    render(harness())
    const row = (await screen.findByText(/m1 critical/i)).closest('tr')
    if (!row) throw new Error('row not found')
    await waitFor(() => expect(alertGets).toBe(1))

    await userEvent.click(within(row).getByRole('button', { name: /^close…$/i }))
    const dialog = await screen.findByRole('dialog', { name: /close alert/i })
    expect(screen.queryByText(/selected machine/i)).not.toBeInTheDocument()
    // The row's alert is the one handed to the dialog.
    expect(within(dialog).getByText(/alert #2 on m1/i)).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('radio', { name: /false alarm/i }))
    await userEvent.click(within(dialog).getByRole('button', { name: /^close alert$/i }))

    await waitFor(() => expect(closed).toEqual(['2']))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(alertGets).toBe(2))
    expect(success).toHaveBeenCalledWith('Alert #2 closed')
    expect(screen.queryByText(/selected machine/i)).not.toBeInTheDocument()
    success.mockRestore()
  })

  it('offers "Record outcome" on a resolved alert without feedback', async () => {
    server.use(
      http.get('/api/alerts', () =>
        HttpResponse.json([{ ...structuredClone(resolvedAlertWithFeedback), feedback: null }]),
      ),
    )
    const success = vi.spyOn(toast, 'success')
    render(harness())
    const row = (await screen.findByText(/m1 faulty/i)).closest('tr')
    if (!row) throw new Error('row not found')
    expect(within(row).queryByRole('button', { name: /^close…$/i })).not.toBeInTheDocument()

    await userEvent.click(within(row).getByRole('button', { name: /record outcome/i }))
    const dialog = await screen.findByRole('dialog', { name: /record outcome/i })
    await userEvent.click(within(dialog).getByRole('radio', { name: /confirmed failure/i }))
    await userEvent.click(within(dialog).getByRole('button', { name: /^record outcome$/i }))
    await waitFor(() => expect(success).toHaveBeenCalledWith('Outcome recorded'))
    success.mockRestore()
  })

  it('badges a recorded outcome and opens it for editing', async () => {
    server.use(http.get('/api/alerts', () => HttpResponse.json([structuredClone(resolvedAlertWithFeedback)])))
    render(harness())
    const row = (await screen.findByText(/m1 faulty/i)).closest('tr')
    if (!row) throw new Error('row not found')

    const badge = within(row).getByRole('button', { name: /prevented by maintenance/i })
    expect(badge).toHaveAttribute('title', 'Bearing wear · recorded by Sam Supervisor')
    expect(within(row).queryByRole('button', { name: /record outcome/i })).not.toBeInTheDocument()

    await userEvent.click(badge)
    expect(await screen.findByRole('dialog', { name: /edit outcome/i })).toBeInTheDocument()
    expect(screen.queryByText(/selected machine/i)).not.toBeInTheDocument()
  })

  it("shows a plain badge to an operator who can't edit the outcome", async () => {
    server.use(
      http.get('/api/auth/me', () => HttpResponse.json(operatorUser)),
      http.get('/api/alerts', () => HttpResponse.json([structuredClone(resolvedAlertWithFeedback)])),
    )
    render(harness())
    const row = (await screen.findByText(/m1 faulty/i)).closest('tr')
    if (!row) throw new Error('row not found')
    await waitFor(() => expect(within(row).queryByRole('button', { name: /prevented by maintenance/i })).toBeNull())
    expect(within(row).getByText(/prevented by maintenance/i)).toBeInTheDocument()
  })

  it('opens the "Why this alert?" drawer from a row without navigating to the machine', async () => {
    render(harness())
    await userEvent.click(await screen.findByRole('button', { name: 'Why this alert? Alert #2' }))
    expect(await screen.findByRole('dialog', { name: /why this alert\? — m1/i })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/alerts/2')
    expect(screen.queryByText(/selected machine/i)).not.toBeInTheDocument()
  })

  it('deep-links to /alerts/:id with the list still rendered, and closing returns to /alerts', async () => {
    render(harness('/alerts/2'))
    const dialog = await screen.findByRole('dialog', { name: /why this alert\? — m1/i })
    expect(await screen.findByText('1 alerts matching this filter')).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/alerts$/)
    expect(screen.getByText('m1 critical')).toBeInTheDocument()
  })
})
