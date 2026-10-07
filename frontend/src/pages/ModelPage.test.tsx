import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { toast } from 'sonner'
import { AuthProvider } from '../auth/AuthContext'
import { ModelPage } from './ModelPage'
import { server } from '../test/server'
import {
  alertFeedback,
  fieldAccuracy,
  fieldAccuracyNotApplicable,
  operatorUser,
  resolvedAlertWithFeedback,
  supervisorUser,
} from '../test/fixtures'
import type { LiveEvent, UserOut } from '../api/types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }))

// The page only reads `lastEvent`; drive it directly instead of through a socket.
const live: { lastEvent: LiveEvent | null } = { lastEvent: null }
vi.mock('../realtime/LiveEventsProvider', () => ({
  useLiveEvents: () => ({ connected: true, lastEvent: live.lastEvent }),
}))

beforeEach(() => {
  live.lastEvent = null
  vi.mocked(toast.success).mockClear()
})

function harness() {
  return (
    <MemoryRouter initialEntries={['/model']}>
      <AuthProvider>
        <ModelPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

function asUser(user: UserOut) {
  server.use(http.get('/api/auth/me', () => HttpResponse.json(user)))
}

async function fieldCard() {
  return screen.findByRole('region', { name: /field accuracy \(operator feedback\)/i })
}

describe('ModelPage', () => {
  it('renders model health and telemetry', async () => {
    render(harness())

    expect(await screen.findByText(/xjtu_rul_v1/i)).toBeInTheDocument()
    // health status label
    expect(screen.getByText(/healthy/i)).toBeInTheDocument()
    // telemetry: inference count and p95 latency. The count is a numeric
    // MetricCard that counts up, so wait for it to settle on the final value.
    expect(await screen.findByText('128')).toBeInTheDocument()
    expect(screen.getByText('21.7')).toBeInTheDocument()
  })

  it('shows an error banner when health fails to load', async () => {
    server.use(http.get('/api/model/health', () => HttpResponse.json({ detail: 'model unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/model unavailable/i)).toBeInTheDocument()
  })

  describe('field accuracy', () => {
    it('renders precision, lead time, RUL error, the offline benchmark and the per-version table', async () => {
      render(harness())
      const card = await fieldCard()

      const metric = (label: RegExp) => within(card).getByText(label, { selector: 'p' }).parentElement as HTMLElement
      await within(card).findByText(/^labelled alerts$/i)
      expect(metric(/^labelled alerts$/i)).toHaveTextContent('6')
      expect(metric(/^precision$/i)).toHaveTextContent('66.7%')
      expect(metric(/^false-alarm rate$/i)).toHaveTextContent('33.3%')
      expect(metric(/^median lead time \(min\)$/i)).toHaveTextContent('95.0')
      expect(metric(/^median lead time \(min\)$/i)).toHaveTextContent('2/2 within 120 min')
      expect(metric(/^rul mae \(min, point estimates\)$/i)).toHaveTextContent('12.5')
      expect(metric(/^rul mae \(min, point estimates\)$/i)).toHaveTextContent('1 censored lower bounds excluded')
      expect(metric(/^root-cause accuracy$/i)).toHaveTextContent('75.0%')

      expect(
        within(card).getByText('Offline benchmark (per snapshot): precision 71.0% · recall 83.0% (model xjtu_rul_20260930)'),
      ).toBeInTheDocument()

      const table = within(card).getByRole('table', { name: /by model version/i })
      const rows = within(table).getAllByRole('row')
      expect(rows).toHaveLength(3)
      expect(rows[1]).toHaveTextContent(/xjtu_rul_20260930\s*5\s*60\.0%\s*40\.0%\s*95\.0\s*12\.5/)
      expect(rows[2]).toHaveTextContent(/unlinked/i)
      expect(within(card).getByText(/missed failures can't be measured from alert feedback/i)).toBeInTheDocument()
    })

    it('says when no offline benchmark is recorded', async () => {
      server.use(
        http.get('/api/model/feedback-accuracy', () => HttpResponse.json({ ...structuredClone(fieldAccuracy), offline: null })),
      )
      render(harness())
      const card = await fieldCard()
      expect(await within(card).findByText(/no offline benchmark recorded for the active model/i)).toBeInTheDocument()
    })

    it('shows the empty state when nothing is labelled, and the rest of the page still renders', async () => {
      server.use(http.get('/api/model/feedback-accuracy', () => HttpResponse.json(fieldAccuracyNotApplicable)))
      render(harness())
      const card = await fieldCard()

      expect(
        await within(card).findByText(/no labelled alerts yet — close alerts with an outcome to start measuring field accuracy/i),
      ).toBeInTheDocument()
      expect(within(card).queryByRole('table')).not.toBeInTheDocument()
      // Numeric MetricCard values count up, so wait rather than assert synchronously.
      expect(await screen.findByText('128')).toBeInTheDocument()
    })

    it('shows a load failure inline without blanking health and telemetry', async () => {
      server.use(
        http.get('/api/model/feedback-accuracy', () => HttpResponse.json({ detail: 'accuracy unavailable' }, { status: 500 })),
      )
      render(harness())
      const card = await fieldCard()

      expect(await within(card).findByRole('alert')).toHaveTextContent(/accuracy unavailable/i)
      expect(screen.getByText(/xjtu_rul_v1/i)).toBeInTheDocument()
      expect(screen.getByText('21.7')).toBeInTheDocument()
    })

    it('shows the export button to admins and supervisors only', async () => {
      const { unmount } = render(harness())
      expect(await within(await fieldCard()).findByRole('button', { name: /export retraining csv/i })).toBeInTheDocument()
      unmount()

      asUser(supervisorUser)
      const second = render(harness())
      expect(await within(await fieldCard()).findByRole('button', { name: /export retraining csv/i })).toBeInTheDocument()
      second.unmount()

      asUser(operatorUser)
      render(harness())
      const card = await fieldCard()
      await within(card).findByText(/^precision$/i, { selector: 'p' })
      await new Promise((r) => setTimeout(r, 50))
      expect(within(card).queryByRole('button', { name: /export retraining csv/i })).not.toBeInTheDocument()
    })

    it('disables the export when there is no exportable episode', async () => {
      server.use(
        http.get('/api/model/feedback-accuracy', () =>
          HttpResponse.json({ ...structuredClone(fieldAccuracy), exportable_episode_count: 0 }),
        ),
      )
      render(harness())
      const button = await within(await fieldCard()).findByRole('button', { name: /export retraining csv/i })
      expect(button).toBeDisabled()
      expect(button).toHaveAttribute('title', 'No confirmed failures with a failure time yet')
    })

    it('downloads the export and toasts the episode count', async () => {
      let exports = 0
      server.use(
        http.get('/api/model/feedback/export', () => {
          exports += 1
          return undefined
        }),
      )
      const createObjectURL = vi.fn(() => 'blob:x')
      const revokeObjectURL = vi.fn()
      Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
      Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true })
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

      render(harness())
      await userEvent.click(await within(await fieldCard()).findByRole('button', { name: /export retraining csv/i }))

      await waitFor(() => expect(clickSpy).toHaveBeenCalled())
      expect(exports).toBe(1)
      expect(createObjectURL).toHaveBeenCalled()
      expect(toast.success).toHaveBeenCalledWith('Exported 1 episode(s)')
      clickSpy.mockRestore()
    })

    it('re-fetches when an alert is closed, but not on other events', async () => {
      let gets = 0
      server.use(
        http.get('/api/model/feedback-accuracy', () => {
          gets += 1
          return undefined
        }),
      )
      const { rerender } = render(harness())
      await fieldCard()
      await waitFor(() => expect(gets).toBe(1))

      live.lastEvent = { type: 'alert_acknowledged', machine_id: 'm1', alert: resolvedAlertWithFeedback, at: 'x' }
      rerender(harness())
      await new Promise((r) => setTimeout(r, 50))
      expect(gets).toBe(1)

      live.lastEvent = {
        type: 'alert_closed',
        machine_id: 'm1',
        alert: resolvedAlertWithFeedback,
        feedback: alertFeedback,
        at: 'x',
      }
      rerender(harness())
      await waitFor(() => expect(gets).toBe(2))
    })
  })
})
