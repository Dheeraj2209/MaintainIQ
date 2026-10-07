import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AlertExplanationPanel } from './AlertExplanationPanel'
import { server } from '../test/server'
import { alertExplanation, alertExplanationReconstructed, openAlerts, workOrders } from '../test/fixtures'
import type { AlertExplanation, LiveEvent } from '../api/types'

// The panel only reads `lastEvent`; drive it directly instead of through a socket.
const live: { lastEvent: LiveEvent | null } = { lastEvent: null }
vi.mock('../realtime/LiveEventsProvider', () => ({
  useLiveEvents: () => ({ connected: true, lastEvent: live.lastEvent }),
}))

beforeEach(() => {
  live.lastEvent = null
})

function harness(id = 2, onClose = () => {}) {
  return (
    <MemoryRouter>
      <AlertExplanationPanel alertId={id} onClose={onClose} />
    </MemoryRouter>
  )
}

// Serves `body` (re-keyed to the requested id) and counts the requests.
function serve(body: AlertExplanation) {
  const calls = { count: 0 }
  server.use(
    http.get('/api/alerts/:id/explanation', ({ params }) => {
      calls.count += 1
      const copy = structuredClone(body)
      return HttpResponse.json({ ...copy, alert: { ...copy.alert, id: Number(params.id) } })
    }),
  )
  return calls
}

function variant(changes: Partial<AlertExplanation>): AlertExplanation {
  return { ...structuredClone(alertExplanation), ...changes }
}

async function openPanel() {
  const panel = await screen.findByRole('dialog', { name: /why this alert\? — m1/i })
  await within(panel).findByRole('heading', { name: 'Similar past incidents' })
  return panel
}

function section(panel: HTMLElement, name: string) {
  return within(panel).getByRole('region', { name })
}

describe('AlertExplanationPanel', () => {
  it('is a labelled modal dialog that takes focus, closes on Escape and returns focus to its opener', async () => {
    function Opener() {
      const [open, setOpen] = useState(false)
      return (
        <MemoryRouter>
          <button type="button" onClick={() => setOpen(true)}>
            Open why
          </button>
          {open && <AlertExplanationPanel alertId={2} onClose={() => setOpen(false)} />}
        </MemoryRouter>
      )
    }
    render(<Opener />)
    const opener = screen.getByRole('button', { name: 'Open why' })
    await userEvent.click(opener)

    const panel = await openPanel()
    expect(panel).toHaveAttribute('aria-modal', 'true')
    expect(panel).toContainElement(document.activeElement as HTMLElement)

    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(opener).toHaveFocus()
  })

  it('shows a loading state first, then the five sections', async () => {
    render(harness())
    expect(screen.getByText('Loading explanation…')).toBeInTheDocument()
    const panel = await openPanel()
    for (const name of ['Triggering readings', 'Key factors', 'Probable cause', 'Model prediction', 'Similar past incidents']) {
      expect(within(panel).getByRole('heading', { name })).toBeInTheDocument()
    }
    expect(within(panel).getByText(/snapshot · captured/i)).toBeInTheDocument()
    expect(within(panel).getByText('Escalated')).toBeInTheDocument()
  })

  it('charts the trigger window with the trigger marked and a channel picker', async () => {
    render(harness())
    const panel = await openPanel()
    const readings = section(panel, 'Triggering readings')
    expect(within(readings).getByRole('combobox', { name: 'Channel' })).toHaveValue('vibration_h_kurtosis')
    expect(within(readings).getByRole('img', { name: /horizontal kurtosis.*trigger at 2003-10-22T12:55:00/i })).toBeInTheDocument()
    expect(within(readings).getByText(/trigger: reading #9182/i)).toBeInTheDocument()

    await userEvent.selectOptions(within(readings).getByRole('combobox', { name: 'Channel' }), 'vibration_v_rms')
    expect(within(readings).getByRole('img', { name: /vertical rms/i })).toBeInTheDocument()
  })

  it('captions a reading-less trigger as the feature vector the model scored', async () => {
    serve(variant({ triggering_readings: { ...alertExplanation.triggering_readings!, locate: 'snapshot_features', trigger_reading_id: null } }))
    render(harness())
    const panel = await openPanel()
    expect(within(panel).getByText(/feature vector the model scored/i)).toBeInTheDocument()
  })

  it("captions a trigger found through the alert's prediction", async () => {
    serve(variant({ triggering_readings: { ...alertExplanation.triggering_readings!, locate: 'prediction_reading' } }))
    render(harness())
    const panel = await openPanel()
    expect(within(panel).getByText(/reading #9182 scored by the alert's prediction/i)).toBeInTheDocument()
  })

  it('shows the out-of-distribution banner for an OOD prediction', async () => {
    render(harness())
    const panel = await openPanel()
    expect(within(panel).getByRole('status')).toHaveTextContent(
      "Outside the model's training range — treat this estimate with caution.",
    )
  })

  it('shows the banner when only a key factor is outside the training range, and hides it otherwise', async () => {
    const inRange = structuredClone(alertExplanation.prediction!)
    inRange.out_of_distribution = false
    serve(variant({ prediction: inRange }))
    const { unmount } = render(harness())
    let panel = await openPanel()
    expect(within(panel).getByRole('status')).toHaveTextContent(/outside the model's training range/i)
    unmount()

    const factors = structuredClone(alertExplanation.key_factors!)
    factors.factors = factors.factors.map((f) => ({ ...f, outside_training_bounds: false }))
    serve(variant({ prediction: inRange, key_factors: factors }))
    render(harness())
    panel = await openPanel()
    expect(within(panel).queryByRole('status')).not.toBeInTheDocument()
  })

  it('lists key factors in API order with direction, ratio, bars and the training-range badge', async () => {
    render(harness())
    const panel = await openPanel()
    const list = within(panel).getByRole('list', { name: 'Key factors' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Horizontal kurtosis')
    expect(rows[0]).toHaveTextContent('↑ 3.1× baseline')
    expect(rows[0]).toHaveTextContent('Outside training range')
    expect(within(rows[0]).getByRole('img', { name: 'Horizontal kurtosis: 3.1× baseline, up' })).toBeInTheDocument()
    expect(rows[1]).toHaveTextContent('Vertical RMS vibration')
    expect(rows[1]).toHaveTextContent('↓ 0.5× baseline')
    expect(rows[1]).not.toHaveTextContent('Outside training range')
    const factorsSection = section(panel, 'Key factors')
    expect(factorsSection).toHaveTextContent(/first 20 readings, weighted by model importance/i)
    expect(factorsSection).toHaveTextContent(/shaft speed/i)
  })

  it('says when the factors are unweighted because the model is not loaded', async () => {
    const factors = structuredClone(alertExplanation.key_factors!)
    factors.weighting = 'unweighted'
    factors.factors = factors.factors.map((f) => ({ ...f, importance: null, score: f.deviation }))
    serve(variant({ key_factors: factors }))
    render(harness())
    const panel = await openPanel()
    expect(section(panel, 'Key factors')).toHaveTextContent(/unweighted \(model not loaded\)/i)
  })

  it('words the cause as probable, with the disclaimer and the rule trace', async () => {
    render(harness())
    const panel = await openPanel()
    const cause = section(panel, 'Probable cause')
    expect(within(cause).getByText('Probable cause: bearing wear')).toBeInTheDocument()
    expect(within(cause).getByText(/a probable cause, not a diagnosis/i)).toBeInTheDocument()
    const trace = within(cause).getByRole('list', { name: 'Rule trace' })
    expect(within(trace).getAllByRole('listitem')[0]).toHaveTextContent('Horizontal kurtosis 9.4 ≥ 5.0 — passed')
    // The disclaimer is the only place the word appears.
    const diagnosisMentions = within(panel).getAllByText(/diagnos/i)
    expect(diagnosisMentions).toHaveLength(1)
    expect(diagnosisMentions[0]).toHaveTextContent(/a probable cause, not a diagnosis/i)
    expect(panel).not.toHaveTextContent(/root cause:/i)
  })

  it('marks failed rule checks as not met and notes a disagreement with the stored label', async () => {
    const cause = structuredClone(alertExplanation.probable_cause!)
    cause.checks = [
      { feature: 'vibration_h_kurtosis', value: 3.1, operator: '>=', threshold: 5, passed: false },
      { feature: 'vibration_h_rms', value: 0.42, operator: '>', threshold: 0, passed: true },
    ]
    cause.evaluated_label = 'imbalance'
    cause.matches_alert = false
    serve(variant({ probable_cause: cause }))
    render(harness())
    const panel = await openPanel()
    const items = within(within(panel).getByRole('list', { name: 'Rule trace' })).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Horizontal kurtosis 3.1 ≥ 5.0 — not met')
    expect(items[1]).toHaveTextContent('Horizontal RMS 0.42 > 0.0 — passed')
    expect(section(panel, 'Probable cause')).toHaveTextContent(/re-run on this reading gives imbalance/i)
  })

  it('shows the model prediction with its interval and the OOD warning first', async () => {
    render(harness())
    const panel = await openPanel()
    const prediction = section(panel, 'Model prediction')
    expect(prediction).toHaveTextContent('83%')
    expect(prediction).toHaveTextContent('42 min (90% interval 12–72)')
    expect(prediction).toHaveTextContent('xjtu_rul_20260930')
    const warnings = within(within(prediction).getByRole('list', { name: 'Model warnings' })).getAllByRole('listitem')
    expect(warnings[0]).toHaveTextContent(/^out_of_distribution:/)
  })

  it('shows a lower-bound RUL as such', async () => {
    const prediction = structuredClone(alertExplanation.prediction!)
    prediction.rul_estimate_kind = 'lower_bound'
    prediction.predicted_rul_minutes = 120
    serve(variant({ prediction }))
    render(harness())
    const panel = await openPanel()
    expect(section(panel, 'Model prediction')).toHaveTextContent('> 120 min (lower bound)')
  })

  it('lists similar incidents with their outcome, actual cause, work order and a link to their own explanation', async () => {
    render(harness())
    const panel = await openPanel()
    const list = within(panel).getByRole('list', { name: 'Similar past incidents' })
    const [incident] = within(list).getAllByRole('listitem')
    expect(incident).toHaveTextContent('Prevented by maintenance')
    expect(incident).toHaveTextContent(/actual cause: bearing wear \(matched\)/i)
    expect(incident).toHaveTextContent('86% similar')
    expect(incident).toHaveTextContent('Same probable cause')
    expect(incident).toHaveTextContent(/replaced drive-end bearing/i)
    expect(within(incident).getByRole('link', { name: /wo #7 · done/i })).toHaveAttribute('href', '/work-orders/7')
    expect(within(incident).getByRole('link', { name: 'Why this alert? Alert #1' })).toHaveAttribute('href', '/alerts/1')
  })

  it('labels a reconstructed explanation and the insufficient-history state', async () => {
    serve(alertExplanationReconstructed)
    render(harness())
    const panel = await openPanel()
    expect(within(panel).getByText('Reconstructed')).toBeInTheDocument()
    expect(section(panel, 'Key factors')).toHaveTextContent(/not enough history/i)
    expect(section(panel, 'Model prediction')).toHaveTextContent('No model prediction recorded')
    expect(within(panel).getByText(/no earlier alerts with this probable cause or on this machine/i)).toBeInTheDocument()
    expect(within(panel).getByText(/nearest reading to the alert time/i)).toBeInTheDocument()
    expect(within(panel).getByRole('list', { name: 'Notes' })).toHaveTextContent(/rebuilt from stored data/i)
  })

  it('labels a demo alert as synthetic', async () => {
    serve(
      variant({
        synthetic: true,
        alert: { ...openAlerts[0], source: 'demo' },
        prediction: null,
      }),
    )
    render(harness())
    const panel = await openPanel()
    expect(within(panel).getByText('Synthetic (demo)')).toBeInTheDocument()
    expect(section(panel, 'Model prediction')).toHaveTextContent('Synthetic demo reading — no model prediction')
  })

  it('says when the alert does not exist', async () => {
    render(harness(999))
    expect(await screen.findByText('Alert #999 not found')).toBeInTheDocument()
  })

  it('re-fetches on a live event about its alert, not on one about another alert', async () => {
    const calls = serve(alertExplanation)
    const { rerender } = render(harness())
    await openPanel()
    expect(calls.count).toBe(1)

    live.lastEvent = { type: 'alert_escalated', machine_id: 'm1', alert: { ...openAlerts[0], id: 3 }, at: 'x' }
    rerender(harness())
    await new Promise((r) => setTimeout(r, 50))
    expect(calls.count).toBe(1)

    live.lastEvent = { type: 'alert_escalated', machine_id: 'm1', alert: { ...openAlerts[0], id: 2 }, at: 'x' }
    rerender(harness())
    await waitFor(() => expect(calls.count).toBe(2))

    live.lastEvent = { type: 'work_order_created', machine_id: 'm1', work_order: { ...workOrders[0], alert_id: 2 }, at: 'y' }
    rerender(harness())
    await waitFor(() => expect(calls.count).toBe(3))
  })
})
