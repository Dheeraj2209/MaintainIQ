import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import App from './App'
import { server } from './test/server'

function machineList() {
  return within(screen.getByRole('region', { name: /machine list/i }))
}

describe('App', () => {
  it('renders KPI summary, machine grid and open alerts', async () => {
    render(<App />)

    // KPI cards from /api/kpis
    expect(await screen.findByRole('region', { name: /kpi/i })).toBeInTheDocument()
    // Both machines from /api/machines
    expect(await machineList().findByRole('button', { name: /m1/ })).toBeInTheDocument()
    expect(machineList().getByRole('button', { name: /m2/ })).toBeInTheDocument()
  })

  it('opens machine detail when a machine is selected', async () => {
    render(<App />)

    const card = await machineList().findByRole('button', { name: /m1/ })
    await userEvent.click(card)

    expect(await screen.findByRole('heading', { name: /m1/ })).toBeInTheDocument()
    expect(await screen.findByText(/bearing_wear/)).toBeInTheDocument()
  })

  it('shows an error banner when the machine list fails to load', async () => {
    server.use(
      http.get('/api/machines', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })),
    )
    render(<App />)
    expect(await screen.findByText(/boom|failed/i)).toBeInTheDocument()
  })

  it('refreshes detail after maintenance is logged', async () => {
    let getCount = 0
    server.use(
      http.get('/api/machines/:id', () => {
        getCount += 1
        return HttpResponse.json({
          health: {
            machine_id: 'm1',
            health_state: 'critical',
            confidence: 0.95,
            prediction_source: 'ml',
            probable_cause: 'bearing_wear',
            last_reading_at: '2003-10-22T13:00:00+00:00',
            vibration_severity: 'high',
            temperature_severity: 'high',
            risk_score: 100,
            abnormal_event_count: 2,
            open_alert_count: 1,
          },
          maintenance: {
            machine_id: 'm1',
            last_maintenance_at: null,
            days_since_last_maintenance: null,
            completed_maintenance_count: 0,
            unresolved_alert_count: 1,
            avg_alert_resolution_hours: 0.08,
            due_for_inspection: true,
          },
          alerts: [],
          maintenance_history: [],
        })
      }),
    )

    render(<App />)
    await userEvent.click(await machineList().findByRole('button', { name: /m1/ }))
    await screen.findByRole('heading', { name: /m1/ })
    const before = getCount

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    await waitFor(() => expect(getCount).toBeGreaterThan(before))
  })
})
