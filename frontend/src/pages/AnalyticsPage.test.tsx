import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { AnalyticsPage } from './AnalyticsPage'
import { server } from '../test/server'
import { kpiSummary } from '../test/fixtures'

function harness() {
  return (
    <MemoryRouter initialEntries={['/analytics']}>
      <AuthProvider>
        <LiveEventsProvider>
          <AnalyticsPage />
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('AnalyticsPage', () => {
  it('shows a loading message before analytics resolve', () => {
    render(harness())
    expect(screen.getByText(/loading analytics/i)).toBeInTheDocument()
  })

  it('renders health distribution, prediction stats, and top-risk ranking', async () => {
    render(harness())
    expect(await screen.findByText(/fleet health distribution/i)).toBeInTheDocument()

    const predictionSection = within(screen.getByText(/prediction model/i).closest('.panel-notch')!)
    expect(predictionSection.getByText('logistic_regression')).toBeInTheDocument()
    expect(predictionSection.getByText('79.6%')).toBeInTheDocument()

    const riskSection = within(screen.getByText(/top at-risk machines/i).closest('.panel-notch')!)
    const links = riskSection.getAllByRole('link')
    expect(links[0]).toHaveTextContent('m1')
    expect(links[1]).toHaveTextContent('m2')
  })

  it('shows placeholders when there is no model or machine data', async () => {
    server.use(
      http.get('/api/kpis', () =>
        HttpResponse.json({
          ...kpiSummary,
          health_state_counts: {},
          prediction: { status: 'unavailable' },
        }),
      ),
      http.get('/api/machines', () => HttpResponse.json([])),
    )
    render(harness())

    expect(await screen.findByText(/no machines to summarize/i)).toBeInTheDocument()
    expect(screen.getByText(/no trained model metrics available yet/i)).toBeInTheDocument()
    expect(screen.getByText(/no machines to rank/i)).toBeInTheDocument()
  })

  it('shows an error message when analytics fail to load', async () => {
    server.use(http.get('/api/kpis', () => HttpResponse.json({ detail: 'analytics unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/analytics unavailable/i)).toBeInTheDocument()
  })
})
