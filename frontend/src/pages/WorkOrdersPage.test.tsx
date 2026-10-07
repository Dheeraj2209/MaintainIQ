import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../auth/AuthContext'
import { WorkOrdersPage } from './WorkOrdersPage'
import { server } from '../test/server'
import { deviceIncidents, operatorUser, supervisorUser, workOrders } from '../test/fixtures'
import type { LiveEvent } from '../api/types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() } }))

// The page only reads `lastEvent`; drive it directly instead of through a socket.
const live: { lastEvent: LiveEvent | null } = { lastEvent: null }
vi.mock('../realtime/LiveEventsProvider', () => ({
  useLiveEvents: () => ({ connected: true, lastEvent: live.lastEvent }),
}))

beforeEach(() => {
  live.lastEvent = null
  vi.mocked(toast.success).mockClear()
})

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

function harness(path = '/work-orders') {
  return (
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/work-orders" element={<WorkOrdersPage />} />
          <Route path="/work-orders/:id" element={<WorkOrdersPage />} />
        </Routes>
        <LocationProbe />
      </AuthProvider>
    </MemoryRouter>
  )
}

// Records the query of every list request (not /assignees or /:id).
function recordListQueries() {
  const queries: URLSearchParams[] = []
  server.use(
    http.get('/api/work-orders', ({ request }) => {
      const params = new URL(request.url).searchParams
      queries.push(params)
      const status = params.get('status')
      const rows =
        status === 'active'
          ? workOrders.filter((w) => ['open', 'assigned', 'in_progress'].includes(w.status))
          : status
            ? workOrders.filter((w) => w.status === status)
            : workOrders
      return HttpResponse.json(structuredClone(rows))
    }),
  )
  return queries
}

async function table() {
  return within(await screen.findByRole('table', { name: /work orders/i }))
}

describe('WorkOrdersPage', () => {
  it('defaults to the Active tab and lists the active orders', async () => {
    const queries = recordListQueries()
    render(harness())

    expect(screen.getByRole('heading', { name: /^work orders$/i })).toBeInTheDocument()
    const rows = await table()
    expect(await rows.findByText('Replace m1 bearing')).toBeInTheDocument()
    expect(rows.getByText('m1 critical')).toBeInTheDocument()
    expect(rows.getByText('Inspect m2 coupling')).toBeInTheDocument()
    expect(rows.queryByText('Grease m2 bearings')).not.toBeInTheDocument()
    expect(queries.map((q) => q.get('status'))).toEqual(['active'])

    const tabs = screen.getByRole('tablist', { name: /work order status/i })
    expect(within(tabs).getByRole('tab', { name: /^active$/i })).toHaveAttribute('aria-selected', 'true')
    // Status, priority, assignee and alert link per row.
    const row = rows.getByText('Inspect m2 coupling').closest('tr')!
    expect(within(row).getByText('Assigned')).toBeInTheDocument()
    expect(within(row).getByText('medium')).toBeInTheDocument()
    expect(within(row).getByText('Ollie Operator')).toBeInTheDocument()
    const alertRow = rows.getByText('Replace m1 bearing').closest('tr')!
    expect(within(alertRow).getByText('#2')).toBeInTheDocument()
  })

  it('changes the status query with the tabs and adds assigned_to for Mine', async () => {
    const queries = recordListQueries()
    render(harness())
    await (await table()).findByText('Replace m1 bearing')
    const tabs = screen.getByRole('tablist', { name: /work order status/i })

    await userEvent.click(within(tabs).getByRole('tab', { name: /^done$/i }))
    expect(await (await table()).findByText('Grease m2 bearings')).toBeInTheDocument()
    expect(queries.at(-1)?.get('status')).toBe('done')

    await userEvent.click(within(tabs).getByRole('tab', { name: /^in progress$/i }))
    await waitFor(() => expect(queries.at(-1)?.get('status')).toBe('in_progress'))

    await userEvent.click(within(tabs).getByRole('tab', { name: /^all$/i }))
    await waitFor(() => expect(queries.at(-1)?.has('status')).toBe(false))

    const mine = screen.getByRole('button', { name: /^mine$/i })
    expect(mine).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(mine)
    await waitFor(() => expect(queries.at(-1)?.get('assigned_to')).toBe('1'))
    expect(mine).toHaveAttribute('aria-pressed', 'true')
  })

  it('filters by machine', async () => {
    const queries = recordListQueries()
    render(harness())
    await (await table()).findByText('Replace m1 bearing')

    const select = screen.getByLabelText(/^machine$/i)
    await within(select).findByRole('option', { name: 'm2' })
    await userEvent.selectOptions(select, 'm2')
    await waitFor(() => expect(queries.at(-1)?.get('machine_id')).toBe('m2'))
  })

  it('opens the drawer on a row click, with the id in the path, and closes back to the list', async () => {
    render(harness())
    const rows = await table()
    await userEvent.click(await rows.findByText('Replace m1 bearing'))

    expect(screen.getByTestId('location')).toHaveTextContent('/work-orders/3')
    expect(await screen.findByRole('dialog', { name: 'Work order #3' })).toBeInTheDocument()

    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /close/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/work-orders$/)
  })

  it('opens the drawer from a deep link', async () => {
    render(harness('/work-orders/3'))
    const drawer = await screen.findByRole('dialog', { name: 'Work order #3' })
    expect(await within(drawer).findByRole('heading', { name: 'Replace m1 bearing' })).toBeInTheDocument()
  })

  it.each([
    ['admin', undefined, true],
    ['supervisor', supervisorUser, true],
    ['operator', operatorUser, false],
  ])('shows "New work order" to %s: %s', async (_role, user, visible) => {
    if (user) server.use(http.get('/api/auth/me', () => HttpResponse.json(user)))
    render(harness())
    await (await table()).findByText('Replace m1 bearing')
    // Let /auth/me settle before asserting absence.
    await new Promise((r) => setTimeout(r, 50))

    const button = screen.queryByRole('button', { name: /new work order/i })
    if (visible) expect(button).toBeInTheDocument()
    else expect(button).not.toBeInTheDocument()
  })

  it('creates a free-standing order and opens it', async () => {
    render(harness())
    await (await table()).findByText('Replace m1 bearing')

    await userEvent.click(await screen.findByRole('button', { name: /new work order/i }))
    const dialog = await screen.findByRole('dialog', { name: /new work order/i })
    const machine = within(dialog).getByLabelText(/machine/i)
    await within(machine).findByRole('option', { name: 'm1' })
    await userEvent.selectOptions(machine, 'm1')
    await userEvent.type(within(dialog).getByLabelText(/title/i), 'Check m1 alignment')
    await userEvent.click(within(dialog).getByRole('button', { name: /create work order/i }))

    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    // The default handler answers with id 11.
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/work-orders/11'))
  })

  it('re-fetches on a work-order event but not on a device event', async () => {
    const queries = recordListQueries()
    const { rerender } = render(harness())
    await (await table()).findByText('Replace m1 bearing')
    expect(queries).toHaveLength(1)

    live.lastEvent = {
      type: 'device_offline',
      device_id: 'simdev-02',
      machine_id: 'sim-02',
      incident: deviceIncidents[0],
      at: 'x',
    }
    rerender(harness())
    await new Promise((r) => setTimeout(r, 50))
    expect(queries).toHaveLength(1)

    live.lastEvent = { type: 'work_order_created', machine_id: 'm1', work_order: workOrders[0], at: 'x' }
    rerender(harness())
    await waitFor(() => expect(queries).toHaveLength(2))
  })

  it('shows an empty state', async () => {
    server.use(http.get('/api/work-orders', () => HttpResponse.json([])))
    render(harness())
    expect(await screen.findByText(/no work orders match/i)).toBeInTheDocument()
  })
})
