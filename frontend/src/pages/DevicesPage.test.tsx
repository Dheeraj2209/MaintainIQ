import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { DevicesPage } from './DevicesPage'
import { server } from '../test/server'
import {
  deviceIncidents,
  deviceWatchdogArmed,
  deviceWatchdogDisabled,
  telemetryStatusDisabled,
  telemetryStatusEnabled,
} from '../test/fixtures'
import type { LiveEvent } from '../api/types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }))

// The page only reads `lastEvent`; drive it directly instead of through a socket.
const live: { lastEvent: LiveEvent | null } = { lastEvent: null }
vi.mock('../realtime/LiveEventsProvider', () => ({
  useLiveEvents: () => ({ connected: true, lastEvent: live.lastEvent }),
}))

function harness(props: { pollMs?: number } = {}) {
  return (
    <MemoryRouter initialEntries={['/devices']}>
      <DevicesPage {...props} />
    </MemoryRouter>
  )
}

function statusWith(overrides: Partial<typeof telemetryStatusEnabled>) {
  return http.get('/api/telemetry/status', () => HttpResponse.json({ ...telemetryStatusEnabled, ...overrides }))
}

beforeEach(() => {
  live.lastEvent = null
  vi.mocked(toast.success).mockClear()
})

describe('DevicesPage', () => {
  it('renders a row per device and the per-state summary counts', async () => {
    render(harness())

    expect(screen.getByRole('heading', { name: /sensor nodes/i })).toBeInTheDocument()
    const table = await screen.findByRole('table', { name: /telemetry devices/i })
    expect(await within(table).findByText('simdev-01')).toBeInTheDocument()
    expect(within(table).getByText('simdev-02')).toBeInTheDocument()

    const summary = screen.getByRole('list', { name: /node states/i })
    expect(within(summary).getByLabelText('1 online')).toBeInTheDocument()
    expect(within(summary).getByLabelText('1 offline')).toBeInTheDocument()
    expect(within(summary).getByLabelText('0 stale')).toBeInTheDocument()
    expect(within(summary).getByLabelText('0 never reported')).toBeInTheDocument()

    // Armed watchdog + connected broker: nothing to warn about.
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('acknowledges an open incident, toasts and re-fetches both lists', async () => {
    const acked: string[] = []
    let incidentGets = 0
    let deviceGets = 0
    server.use(
      http.post('/api/telemetry/incidents/:id/acknowledge', ({ params }) => {
        acked.push(String(params.id))
        return HttpResponse.json({ ...deviceIncidents[0], acknowledged_at: '2026-10-06T12:00:00Z', acknowledged_by: 1 })
      }),
      http.get('/api/telemetry/incidents', () => {
        incidentGets += 1
        return HttpResponse.json(structuredClone(deviceIncidents))
      }),
      http.get('/api/telemetry/devices', () => {
        deviceGets += 1
        return HttpResponse.json([])
      }),
    )
    // Long poll so only the ack triggers the second load.
    render(harness({ pollMs: 60_000 }))

    const table = await screen.findByRole('table', { name: /device incidents/i })
    const button = await within(table).findByRole('button', { name: /acknowledge incident 1/i })
    await waitFor(() => expect(deviceGets).toBe(1))
    const incidentsBefore = incidentGets

    await userEvent.click(button)

    await waitFor(() => expect(acked).toEqual(['1']))
    expect(toast.success).toHaveBeenCalled()
    await waitFor(() => expect(deviceGets).toBe(2))
    expect(incidentGets).toBeGreaterThan(incidentsBefore)
  })

  it('switches the incident history status query with the tabs', async () => {
    const statuses: (string | null)[] = []
    server.use(
      http.get('/api/telemetry/incidents', ({ request }) => {
        statuses.push(new URL(request.url).searchParams.get('status'))
        return HttpResponse.json([])
      }),
    )
    render(harness({ pollMs: 60_000 }))

    const tabs = await screen.findByRole('tablist', { name: /incident history/i })
    expect(within(tabs).getByRole('tab', { name: /open/i })).toHaveAttribute('aria-selected', 'true')

    statuses.length = 0
    await userEvent.click(within(tabs).getByRole('tab', { name: /resolved/i }))
    await waitFor(() => expect(statuses).toContain('resolved'))
    expect(within(tabs).getByRole('tab', { name: /resolved/i })).toHaveAttribute('aria-selected', 'true')

    statuses.length = 0
    await userEvent.click(within(tabs).getByRole('tab', { name: /all/i }))
    // "All" omits the status filter entirely.
    await waitFor(() => expect(statuses).toContain(null))
  })

  it('drops a stale history response that settles after a tab switch', async () => {
    // Every status=open request is held so the test decides the order in
    // which the two loads settle: the old "open" load must land last.
    const held: (() => void)[] = []
    server.use(
      http.get('/api/telemetry/incidents', async ({ request }) => {
        const status = new URL(request.url).searchParams.get('status')
        if (status === 'open') {
          await new Promise<void>((resolve) => held.push(resolve))
          return HttpResponse.json(structuredClone(deviceIncidents.filter((i) => i.status === 'open')))
        }
        return HttpResponse.json([])
      }),
    )
    render(harness({ pollMs: 60_000 }))

    const tabs = await screen.findByRole('tablist', { name: /incident history/i })
    await waitFor(() => expect(held).toHaveLength(2)) // load('open'): open list + history
    await userEvent.click(within(tabs).getByRole('tab', { name: /resolved/i }))
    await waitFor(() => expect(held).toHaveLength(3)) // load('resolved'): open list

    const table = screen.getByRole('table', { name: /device incidents/i })
    await act(async () => held[2]())
    expect(await within(table).findByText(/no incidents match/i)).toBeInTheDocument()

    // Now the superseded "open" load settles; it must not repaint the Resolved tab.
    await act(async () => {
      held[0]()
      held[1]()
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    expect(within(tabs).getByRole('tab', { name: /resolved/i })).toHaveAttribute('aria-selected', 'true')
    expect(within(table).queryByText('Still open')).not.toBeInTheDocument()
    expect(within(table).getByText(/no incidents match/i)).toBeInTheDocument()
  })

  it('warns when live ingest is off', async () => {
    server.use(http.get('/api/telemetry/status', () => HttpResponse.json(telemetryStatusDisabled)))
    render(harness())
    expect(await screen.findByRole('status')).toHaveTextContent(/live ingest is off/i)
  })

  it('warns when the broker is disconnected', async () => {
    server.use(statusWith({ connected: false }))
    render(harness())
    expect(await screen.findByRole('status')).toHaveTextContent(/broker disconnected/i)
  })

  it('warns when silence alerting is disabled', async () => {
    server.use(statusWith({ device_watchdog: deviceWatchdogDisabled }))
    render(harness())
    expect(await screen.findByRole('status')).toHaveTextContent(/MAINTAINIQ_SWEEP_INTERVAL_S/)
  })

  it('warns when the watchdog is enabled but not armed yet', async () => {
    server.use(statusWith({ device_watchdog: { ...deviceWatchdogArmed, armed: false } }))
    render(harness())
    expect(await screen.findByRole('status')).toHaveTextContent(/arms 30 s after the broker connects/i)
  })

  it('re-fetches immediately on a device live event', async () => {
    let deviceGets = 0
    server.use(
      http.get('/api/telemetry/devices', () => {
        deviceGets += 1
        return HttpResponse.json([])
      }),
    )
    const { rerender } = render(harness({ pollMs: 60_000 }))
    await waitFor(() => expect(deviceGets).toBe(1))

    live.lastEvent = {
      type: 'device_offline',
      device_id: 'simdev-02',
      machine_id: 'sim-02',
      incident: deviceIncidents[0],
      at: '2026-10-06T12:00:00Z',
    }
    rerender(harness({ pollMs: 60_000 }))
    await waitFor(() => expect(deviceGets).toBe(2))
  })

  it('polls every 5 s by default', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let deviceGets = 0
      server.use(
        http.get('/api/telemetry/devices', () => {
          deviceGets += 1
          return HttpResponse.json([])
        }),
      )
      render(harness())
      await waitFor(() => expect(deviceGets).toBe(1))

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      await waitFor(() => expect(deviceGets).toBe(2))
    } finally {
      vi.useRealTimers()
    }
  })
})
