import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { AnalyticsPage } from './AnalyticsPage'
import { server } from '../test/server'
import { commissioningMachine, heldMachine, kpiSummary } from '../test/fixtures'

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
    const metric = (label: string) => predictionSection.getByText(label).parentElement!
    expect(metric('Active model')).toHaveTextContent('xjtu-rul-20260930T164318Z')
    expect(metric('Active model')).toHaveTextContent('ExtraTreesRegressor')
    expect(metric('Failure-detection F1')).toHaveTextContent('67.7%')
    expect(metric('Precision')).toHaveTextContent('72.5%')
    expect(metric('Recall')).toHaveTextContent('63.4%')
    expect(metric('ROC AUC')).toHaveTextContent('0.81')
    expect(metric('RUL error (MAE)')).toHaveTextContent('30.9 min')
    // Integer counts animate up from 0 (MetricCard count-up).
    await waitFor(() => expect(metric('False alarms')).toHaveTextContent('400'))
    await waitFor(() => expect(metric('Missed failure windows')).toHaveTextContent('607'))
    // Retired classifier metrics are gone.
    expect(predictionSection.queryByText(/^Accuracy$/)).not.toBeInTheDocument()
    expect(predictionSection.queryByText(/mean confidence/i)).not.toBeInTheDocument()
    expect(predictionSection.queryByText(/logistic_regression/)).not.toBeInTheDocument()

    const riskSection = within(screen.getByText(/top at-risk machines/i).closest('.panel-notch')!)
    const links = riskSection.getAllByRole('link')
    expect(links[0]).toHaveTextContent('m1')
    expect(links[1]).toHaveTextContent('m2')
  })

  it('shows held and commissioning machines in the ranking, not plain states', async () => {
    server.use(http.get('/api/machines', () => HttpResponse.json([heldMachine, commissioningMachine])))
    render(harness())

    const riskSection = within((await screen.findByText(/top at-risk machines/i)).closest('.panel-notch')!)
    expect(await riskSection.findByText(/held since/i)).toBeInTheDocument()
    expect(riskSection.getByText('Commissioning 7/20')).toBeInTheDocument()
    expect(riskSection.queryByText('Healthy')).not.toBeInTheDocument()
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
