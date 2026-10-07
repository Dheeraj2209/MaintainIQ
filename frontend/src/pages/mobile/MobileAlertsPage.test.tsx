import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../../auth/AuthContext'
import { LiveEventsProvider } from '../../realtime/LiveEventsProvider'
import { server } from '../../test/server'
import { MockWebSocket } from '../../test/mockWebSocket'
import { openAlerts, workOrders } from '../../test/fixtures'
import type { Alert } from '../../api/types'
import { MobileAlertsPage } from './MobileAlertsPage'

function alert(changes: Partial<Alert> = {}): Alert {
  return {
    ...structuredClone(openAlerts[0]),
    opened_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    acknowledged_at: null,
    acknowledged_by: null,
    active_work_order_id: null,
    ...changes,
  }
}

// Serves `rows` for GET /api/alerts and counts the fetches.
function serveAlerts(rows: () => Alert[]) {
  const fetches = { count: 0 }
  server.use(
    http.get('/api/alerts', () => {
      fetches.count += 1
      return HttpResponse.json(rows())
    }),
  )
  return fetches
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((r) => (resolve = r))
  return { promise, resolve }
}

function Placeholder({ label }: { label: string }) {
  const { id } = useParams()
  return (
    <div>
      {label} {id}
    </div>
  )
}

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

function harness() {
  return (
    <MemoryRouter initialEntries={['/m/alerts']}>
      <AuthProvider>
        <LiveEventsProvider>
          <LocationProbe />
          <Routes>
            <Route path="/m/alerts" element={<MobileAlertsPage />} />
            <Route path="/m/alerts/:id" element={<Placeholder label="alert detail" />} />
            <Route path="/work-orders/:id" element={<Placeholder label="work order" />} />
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

async function card(id = 2) {
  return within(await screen.findByRole('article', { name: new RegExp(`alert #${id} on`, 'i') }))
}

describe('MobileAlertsPage', () => {
  it('lists the open alerts with machine, relative time and status chips', async () => {
    const fetched: string[] = []
    server.use(
      http.get('/api/alerts', ({ request }) => {
        fetched.push(new URL(request.url).search)
        return HttpResponse.json([alert({ page_level: 1, last_paged_at: '2026-10-07T00:15:00Z', active_work_order_id: 12, acknowledged_at: '2026-10-07T00:00:00Z' })])
      }),
    )
    render(harness())

    const c = await card()
    expect(c.getByText('m1')).toBeInTheDocument()
    expect(c.getByText(/5 min ago/)).toBeInTheDocument()
    expect(c.getByText(/critical · bearing wear/i)).toBeInTheDocument()
    expect(c.getByText('Acknowledged')).toBeInTheDocument()
    expect(c.getByText('Paged L1')).toBeInTheDocument()
    expect(c.getByRole('link', { name: 'WO #12' })).toHaveAttribute('href', '/work-orders/12')
    expect(fetched[0]).toBe('?status=open')
  })

  it('shows the healthy empty state and switches to recent alerts', async () => {
    const fetched: string[] = []
    server.use(
      http.get('/api/alerts', ({ request }) => {
        const search = new URL(request.url).search
        fetched.push(search)
        return HttpResponse.json(search ? [] : [alert({ status: 'resolved', resolved_at: '2026-10-07T00:00:00Z' })])
      }),
    )
    render(harness())
    expect(await screen.findByText('No open alerts. Machines are healthy.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('radio', { name: 'Recent' }))

    const c = await card()
    expect(c.getByText('Resolved')).toBeInTheDocument()
    expect(c.getByRole('button', { name: 'Record outcome for alert #2' })).toBeInTheDocument()
    expect(fetched).toEqual(['?status=open', ''])
  })

  it('names every action with the alert id and gives it a 44 px target', async () => {
    serveAlerts(() => [alert()])
    render(harness())
    const c = await card()
    for (const name of ['Acknowledge alert #2', 'Create work order for alert #2', 'Close alert #2', 'Why alert #2?']) {
      expect(c.getByRole('button', { name })).toHaveClass('min-h-11', 'min-w-11')
    }
  })

  it('acknowledges optimistically, before the server answers', async () => {
    const gate = deferred()
    let posted = 0
    serveAlerts(() => [alert()])
    server.use(
      http.post('/api/alerts/:id/acknowledge', async () => {
        posted += 1
        await gate.promise
        return HttpResponse.json(alert({ acknowledged_at: '2026-10-07T12:00:00Z', acknowledged_by: 1 }))
      }),
    )
    render(harness())
    const c = await card()

    await userEvent.click(c.getByRole('button', { name: 'Acknowledge alert #2' }))

    expect(c.getByText('Acknowledged')).toBeInTheDocument()
    expect(c.queryByRole('button', { name: 'Acknowledge alert #2' })).not.toBeInTheDocument()
    await waitFor(() => expect(posted).toBe(1))
    gate.resolve()
    await waitFor(() => expect(c.getByText('Acknowledged')).toBeInTheDocument())
  })

  it('rolls an acknowledgement back and toasts when the server refuses it', async () => {
    const error = vi.spyOn(toast, 'error')
    serveAlerts(() => [alert()])
    server.use(http.post('/api/alerts/:id/acknowledge', () => HttpResponse.json({ detail: 'nope' }, { status: 500 })))
    render(harness())
    const c = await card()

    await userEvent.click(c.getByRole('button', { name: 'Acknowledge alert #2' }))

    expect(await c.findByRole('button', { name: 'Acknowledge alert #2' })).toBeInTheDocument()
    expect(c.queryByText('Acknowledged')).not.toBeInTheDocument()
    expect(error).toHaveBeenCalledWith(expect.stringMatching(/alert #2.*nope/i))
    error.mockRestore()
  })

  it('creates a work order in one tap and links to it', async () => {
    const gate = deferred()
    serveAlerts(() => [alert()])
    server.use(
      http.post('/api/alerts/:id/work-order', async ({ request }) => {
        expect(await request.text()).toBe('{}')
        await gate.promise
        return HttpResponse.json({ ...structuredClone(workOrders[0]), id: 10, alert_id: 2 }, { status: 201 })
      }),
    )
    render(harness())
    const c = await card()

    await userEvent.click(c.getByRole('button', { name: 'Create work order for alert #2' }))

    expect(c.getByText('Creating work order…')).toBeInTheDocument()
    expect(c.getByText('Acknowledged')).toBeInTheDocument()
    gate.resolve()
    const link = await c.findByRole('link', { name: 'WO #10' })
    expect(link).toHaveAttribute('href', '/work-orders/10')
    expect(c.queryByText('Creating work order…')).not.toBeInTheDocument()
  })

  it('rolls a work order back on 409, re-fetches and says one already exists', async () => {
    const info = vi.spyOn(toast, 'info')
    const fetches = serveAlerts(() => [alert()])
    server.use(
      http.post('/api/alerts/:id/work-order', () =>
        HttpResponse.json({ detail: 'alert 2 already has an active work order' }, { status: 409 }),
      ),
    )
    render(harness())
    const c = await card()
    const before = fetches.count

    await userEvent.click(c.getByRole('button', { name: 'Create work order for alert #2' }))

    expect(await c.findByRole('button', { name: 'Create work order for alert #2' })).toBeInTheDocument()
    expect(c.queryByText('Creating work order…')).not.toBeInTheDocument()
    expect(info).toHaveBeenCalledWith('Alert #2 already has a work order')
    await waitFor(() => expect(fetches.count).toBeGreaterThan(before))
    info.mockRestore()
  })

  it('closes with an outcome through the close dialog and drops the card from Open', async () => {
    let open = true
    serveAlerts(() => (open ? [alert()] : []))
    render(harness())
    const c = await card()

    await userEvent.click(c.getByRole('button', { name: 'Close alert #2' }))
    const dialog = await screen.findByRole('dialog', { name: /close alert/i })
    await userEvent.click(within(dialog).getByRole('radio', { name: /false alarm/i }))
    open = false
    await userEvent.click(within(dialog).getByRole('button', { name: /^close alert$/i }))

    await waitFor(() => expect(screen.queryByRole('article')).not.toBeInTheDocument())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens the alert page from Why?', async () => {
    serveAlerts(() => [alert()])
    render(harness())
    const c = await card()

    await userEvent.click(c.getByRole('button', { name: 'Why alert #2?' }))

    expect(await screen.findByText('alert detail 2')).toBeInTheDocument()
  })

  it('re-fetches on a live alert event while keeping a pending acknowledgement', async () => {
    const gate = deferred()
    const fetches = serveAlerts(() => [alert(), alert({ id: 7, machine_id: 'm2' })])
    server.use(
      http.post('/api/alerts/:id/acknowledge', async () => {
        await gate.promise
        return HttpResponse.json(alert({ acknowledged_at: '2026-10-07T12:00:00Z', acknowledged_by: 1 }))
      }),
    )
    render(harness())
    const c = await card()
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    MockWebSocket.instances[0].emitOpen()

    await userEvent.click(c.getByRole('button', { name: 'Acknowledge alert #2' }))
    const before = fetches.count
    MockWebSocket.instances[0].emitMessage({ type: 'alert_created', machine_id: 'm2', alert: alert({ id: 7, machine_id: 'm2' }), at: 'x' })

    await waitFor(() => expect(fetches.count).toBe(before + 1))
    // The fetched row is still unacknowledged; the pending edit stays on top.
    expect((await card()).getByText('Acknowledged')).toBeInTheDocument()
    expect(await card(7)).toBeTruthy()
    gate.resolve()
    await waitFor(() => expect(c.queryByRole('button', { name: 'Acknowledge alert #2' })).not.toBeInTheDocument())
  })
})
