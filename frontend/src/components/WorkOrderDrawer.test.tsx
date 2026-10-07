import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../auth/AuthContext'
import { WorkOrderDrawer } from './WorkOrderDrawer'
import { server } from '../test/server'
import {
  openAlerts,
  operatorUser,
  resolvedAlertWithFeedback,
  supervisorUser,
  workOrderDetail,
  workOrderDetailFor,
  workOrders,
} from '../test/fixtures'
import type { LiveEvent, UserOut, WorkOrder } from '../api/types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() } }))

// The drawer only reads `lastEvent`; drive it directly instead of through a socket.
const live: { lastEvent: LiveEvent | null } = { lastEvent: null }
vi.mock('../realtime/LiveEventsProvider', () => ({
  useLiveEvents: () => ({ connected: true, lastEvent: live.lastEvent }),
}))

beforeEach(() => {
  live.lastEvent = null
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.error).mockClear()
})

function harness(id = 3, onClose = () => {}) {
  return (
    <MemoryRouter>
      <AuthProvider>
        <WorkOrderDrawer workOrderId={id} onClose={onClose} />
      </AuthProvider>
    </MemoryRouter>
  )
}

function asUser(user: UserOut) {
  server.use(http.get('/api/auth/me', () => HttpResponse.json(user)))
}

// Overrides GET /api/work-orders/:id. That pattern also matches /assignees,
// so the override falls through (returns nothing) for it, as the server's
// route order would.
function onGetOrder(resolve: () => Response) {
  return http.get('/api/work-orders/:id', ({ params }) => (params.id === 'assignees' ? undefined : resolve()))
}

function serveOrder(order: WorkOrder) {
  server.use(onGetOrder(() => HttpResponse.json(workOrderDetailFor(order))))
}

async function openDrawer(id = 3) {
  const drawer = await screen.findByRole('dialog', { name: `Work order #${id}` })
  // Wait for the detail to load (title shows) before asserting on actions.
  await within(drawer).findByRole('heading', { level: 3 })
  return drawer
}

const ACTION_NAMES = [/^(assign|reassign)$/i, /^start work$/i, /^complete$/i, /^cancel work order$/i, /^edit$/i]

function visibleActions(drawer: HTMLElement): string[] {
  return ACTION_NAMES.filter((name) => within(drawer).queryByRole('button', { name })).map(String)
}

describe('WorkOrderDrawer', () => {
  it('renders the facts and the timeline oldest first', async () => {
    render(harness())
    const drawer = await openDrawer()

    expect(within(drawer).getByRole('heading', { name: 'Replace m1 bearing' })).toBeInTheDocument()
    expect(within(drawer).getByText('In progress')).toBeInTheDocument()
    expect(within(drawer).getByRole('link', { name: 'm1' })).toHaveAttribute('href', '/machines/m1')
    expect(within(drawer).getByText('Assignee').nextElementSibling).toHaveTextContent('Sam Supervisor')
    expect(within(drawer).getByText('Created by').nextElementSibling).toHaveTextContent('Ada Admin')
    // The linked alert, with its severity.
    const linked = within(drawer).getByRole('region', { name: /linked alert/i })
    expect(within(linked).getByText(/alert #2/i)).toBeInTheDocument()
    expect(within(linked).getByText('high')).toBeInTheDocument()
    expect(within(linked).queryByText(/paged/i)).not.toBeInTheDocument()

    const timeline = within(drawer).getByRole('list', { name: /timeline/i })
    const items = within(timeline).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent(/Ada Admin.*created/i)
    expect(items[0]).toHaveTextContent('from alert #2')
    expect(items[1]).toHaveTextContent(/assigned to Sam Supervisor/i)
    expect(items[2]).toHaveTextContent(/Sam Supervisor.*started/i)
    // Absolute time on hover.
    expect(within(items[0]).getByText(/ago|just now/i)).toHaveAttribute('title', '2026-10-07T07:00:00+00:00')
  })

  it('shows the paging level of a paged linked alert', async () => {
    server.use(
      onGetOrder(() =>
        HttpResponse.json({ ...workOrderDetailFor(workOrders[2]), alert: { ...openAlerts[0], page_level: 2, last_paged_at: '2026-10-07T12:30:00Z' } }),
      ),
    )
    render(harness())
    const drawer = await openDrawer()
    expect(within(drawer).getByText('Paged L2')).toBeInTheDocument()
  })

  it('offers an operator only Start on an order assigned to them', async () => {
    asUser(operatorUser)
    serveOrder(workOrders[1]) // assigned to Ollie Operator
    render(harness(2))
    const drawer = await openDrawer(2)

    await waitFor(() => expect(visibleActions(drawer)).toEqual([String(/^start work$/i)]))
  })

  it("offers an operator no actions on someone else's order", async () => {
    asUser(operatorUser)
    render(harness()) // #3: in progress, assigned to Sam Supervisor
    const drawer = await openDrawer()

    await new Promise((r) => setTimeout(r, 50))
    expect(visibleActions(drawer)).toEqual([])
  })

  it('offers a supervisor Assign and Cancel', async () => {
    asUser(supervisorUser)
    serveOrder({ ...workOrders[0] }) // open, unassigned
    render(harness(1))
    const drawer = await openDrawer(1)

    await waitFor(() =>
      expect(visibleActions(drawer)).toEqual([
        String(/^(assign|reassign)$/i),
        String(/^cancel work order$/i),
        String(/^edit$/i),
      ]),
    )
    const select = within(drawer).getByLabelText(/assignee/i)
    await within(select).findByRole('option', { name: /ollie operator/i })
  })

  it('offers no actions on a terminal order', async () => {
    serveOrder(workOrders[3]) // done
    render(harness(4))
    const drawer = await openDrawer(4)

    expect(within(drawer).getByText('Maintenance record').nextElementSibling).toHaveTextContent('#12')
    await new Promise((r) => setTimeout(r, 50))
    expect(visibleActions(drawer)).toEqual([])
  })

  it('assigns the chosen user and re-fetches', async () => {
    asUser(supervisorUser)
    serveOrder({ ...workOrders[0] })
    const bodies: unknown[] = []
    server.use(
      http.post('/api/work-orders/:id/assign', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json({ ...workOrders[0], status: 'assigned', assigned_to: 3 })
      }),
    )
    render(harness(1))
    const drawer = await openDrawer(1)

    const select = within(drawer).getByLabelText(/assignee/i)
    await within(select).findByRole('option', { name: /ollie operator/i })
    await userEvent.selectOptions(select, '3')
    await userEvent.click(within(drawer).getByRole('button', { name: /^assign$/i }))

    await waitFor(() => expect(bodies).toEqual([{ assigned_to: 3 }]))
    expect(toast.success).toHaveBeenCalled()
  })

  it('reveals the completion form and posts notes, performed_at and type', async () => {
    const bodies: Record<string, unknown>[] = []
    server.use(
      http.post('/api/work-orders/:id/complete', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>)
        return HttpResponse.json({ ...workOrders[2], status: 'done', maintenance_record_id: 99 })
      }),
    )
    render(harness())
    const drawer = await openDrawer()

    expect(within(drawer).queryByLabelText(/completion notes/i)).not.toBeInTheDocument()
    await userEvent.click(within(drawer).getByRole('button', { name: /^complete$/i }))

    const performed = within(drawer).getByLabelText(/performed at/i)
    expect(performed).not.toHaveValue('')
    await userEvent.clear(performed)
    await userEvent.type(performed, '2026-10-07T10:30')
    await userEvent.type(within(drawer).getByLabelText(/completion notes/i), 'Bearing replaced')
    await userEvent.selectOptions(within(drawer).getByLabelText(/^type$/i), 'preventive')
    await userEvent.click(within(drawer).getByRole('button', { name: /confirm completion/i }))

    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({
      notes: 'Bearing replaced',
      performed_at: new Date('2026-10-07T10:30').toISOString(),
      maintenance_type: 'preventive',
    })
    expect(toast.success).toHaveBeenCalled()
  })

  it('cancels with a reason', async () => {
    const bodies: unknown[] = []
    server.use(
      http.post('/api/work-orders/:id/cancel', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json({ ...workOrders[2], status: 'cancelled' })
      }),
    )
    render(harness())
    const drawer = await openDrawer()

    await userEvent.click(within(drawer).getByRole('button', { name: /^cancel work order$/i }))
    await userEvent.type(within(drawer).getByLabelText(/cancel reason/i), 'Duplicate')
    await userEvent.click(within(drawer).getByRole('button', { name: /confirm cancel/i }))

    await waitFor(() => expect(bodies).toEqual([{ reason: 'Duplicate' }]))
  })

  it('edits only the changed fields, pre-filled from the order', async () => {
    asUser(supervisorUser)
    serveOrder({ ...workOrders[2], description: 'Old notes' })
    const bodies: unknown[] = []
    server.use(
      http.patch('/api/work-orders/:id', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json({ ...workOrders[2], priority: 'low' })
      }),
    )
    render(harness())
    const drawer = await openDrawer()

    expect(within(drawer).queryByLabelText(/^title$/i)).not.toBeInTheDocument()
    await userEvent.click(within(drawer).getByRole('button', { name: /^edit$/i }))
    expect(within(drawer).getByLabelText(/^title$/i)).toHaveValue('Replace m1 bearing')
    expect(within(drawer).getByLabelText(/^description$/i)).toHaveValue('Old notes')
    expect(within(drawer).getByLabelText(/^priority$/i)).toHaveValue('high')

    await userEvent.selectOptions(within(drawer).getByLabelText(/^priority$/i), 'low')
    await userEvent.clear(within(drawer).getByLabelText(/^description$/i))
    await userEvent.type(within(drawer).getByLabelText(/^due$/i), '2026-10-09T08:00')
    await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

    await waitFor(() =>
      expect(bodies).toEqual([
        { description: null, priority: 'low', due_at: new Date('2026-10-09T08:00').toISOString() },
      ]),
    )
    expect(toast.success).toHaveBeenCalled()
  })

  it('closes the edit form without a request when nothing changed', async () => {
    asUser(supervisorUser)
    const patch = vi.fn()
    server.use(
      http.patch('/api/work-orders/:id', () => {
        patch()
        return HttpResponse.json(workOrders[2])
      }),
    )
    render(harness())
    const drawer = await openDrawer()

    await userEvent.click(within(drawer).getByRole('button', { name: /^edit$/i }))
    await userEvent.click(within(drawer).getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(within(drawer).queryByLabelText(/^title$/i)).not.toBeInTheDocument())
    expect(patch).not.toHaveBeenCalled()
  })

  it('toasts the 409 detail from Start and re-fetches', async () => {
    asUser(operatorUser)
    let gets = 0
    server.use(
      onGetOrder(() => {
        gets += 1
        return HttpResponse.json(workOrderDetailFor(workOrders[1]))
      }),
      http.post('/api/work-orders/:id/start', () =>
        HttpResponse.json({ detail: 'invalid transition: in_progress -> start' }, { status: 409 }),
      ),
    )
    render(harness(2))
    const drawer = await openDrawer(2)
    const start = await within(drawer).findByRole('button', { name: /^start work$/i })
    expect(gets).toBe(1)

    await userEvent.click(start)

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('invalid transition: in_progress -> start'))
    await waitFor(() => expect(gets).toBe(2))
  })

  it('re-fetches on a work_order_updated event for this order only', async () => {
    let gets = 0
    server.use(
      onGetOrder(() => {
        gets += 1
        return HttpResponse.json(workOrderDetailFor(workOrders[2]))
      }),
    )
    const { rerender } = render(harness())
    await openDrawer()
    expect(gets).toBe(1)

    live.lastEvent = { type: 'work_order_updated', machine_id: 'm2', work_order: workOrders[1], change: 'started', at: 'x' }
    rerender(harness())
    await new Promise((r) => setTimeout(r, 50))
    expect(gets).toBe(1)

    live.lastEvent = { type: 'work_order_updated', machine_id: 'm1', work_order: workOrders[2], change: 'edited', at: 'x' }
    rerender(harness())
    await waitFor(() => expect(gets).toBe(2))
  })

  it('closes on Escape and from the close button', async () => {
    const onClose = vi.fn()
    render(harness(3, onClose))
    const drawer = await openDrawer()

    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
    await userEvent.click(within(drawer).getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  describe('recording what happened after completion', () => {
    async function complete(drawer: HTMLElement) {
      await userEvent.click(within(drawer).getByRole('button', { name: /^complete$/i }))
      await userEvent.type(within(drawer).getByLabelText(/completion notes/i), 'Bearing replaced')
      await userEvent.click(within(drawer).getByRole('button', { name: /confirm completion/i }))
    }

    it('offers the outcome step for a linked alert and sends this order as work_order_id', async () => {
      const closes: Record<string, unknown>[] = []
      server.use(
        http.post('/api/alerts/:id/close', async ({ request }) => {
          closes.push((await request.clone().json()) as Record<string, unknown>)
          return undefined
        }),
      )
      render(harness())
      const drawer = await openDrawer()
      await complete(drawer)

      const step = await within(drawer).findByRole('region', { name: /record what happened/i })
      // Completion notes carry over as the outcome's notes.
      expect(within(step).getByLabelText(/notes/i)).toHaveValue('Bearing replaced')
      await userEvent.click(within(step).getByRole('radio', { name: /prevented by maintenance/i }))
      await userEvent.click(within(step).getByRole('button', { name: /^close alert$/i }))

      await waitFor(() => expect(closes).toHaveLength(1))
      expect(closes[0]).toMatchObject({ outcome: 'maintenance_prevented', work_order_id: 3, notes: 'Bearing replaced' })
      await waitFor(() => expect(within(drawer).queryByRole('region', { name: /record what happened/i })).toBeNull())
      expect(toast.success).toHaveBeenCalledWith('Alert #2 closed')
    })

    it('skips the step without a request', async () => {
      const requests: string[] = []
      server.use(
        http.post('/api/alerts/:id/close', ({ request }) => {
          requests.push(request.url)
          return undefined
        }),
        http.put('/api/alerts/:id/feedback', ({ request }) => {
          requests.push(request.url)
          return undefined
        }),
      )
      render(harness())
      const drawer = await openDrawer()
      await complete(drawer)

      const step = await within(drawer).findByRole('region', { name: /record what happened/i })
      await userEvent.click(within(step).getByRole('button', { name: /^skip$/i }))
      expect(within(drawer).queryByRole('region', { name: /record what happened/i })).not.toBeInTheDocument()
      expect(requests).toEqual([])
    })

    it('goes straight back to the actions for an order without an alert', async () => {
      serveOrder({ ...workOrders[2], alert_id: null })
      render(harness())
      const drawer = await openDrawer()
      await complete(drawer)

      await waitFor(() => expect(toast.success).toHaveBeenCalled())
      await new Promise((r) => setTimeout(r, 50))
      expect(within(drawer).queryByRole('region', { name: /record what happened/i })).not.toBeInTheDocument()
    })

    it("isn't offered when the operator can't edit the linked alert's outcome", async () => {
      asUser(operatorUser)
      const order = { ...workOrders[2], assigned_to: 3, assigned_to_name: 'Ollie Operator' }
      server.use(
        onGetOrder(() =>
          HttpResponse.json({ ...structuredClone(workOrderDetail), ...order, alert: structuredClone(resolvedAlertWithFeedback) }),
        ),
      )
      render(harness())
      const drawer = await openDrawer()
      await complete(drawer)

      await waitFor(() => expect(toast.success).toHaveBeenCalled())
      await new Promise((r) => setTimeout(r, 50))
      expect(within(drawer).queryByRole('region', { name: /record what happened/i })).not.toBeInTheDocument()
    })

    it('badges the linked alert with its recorded outcome', async () => {
      server.use(
        onGetOrder(() =>
          HttpResponse.json({ ...structuredClone(workOrderDetail), alert: structuredClone(resolvedAlertWithFeedback) }),
        ),
      )
      render(harness())
      const drawer = await openDrawer()
      const linked = within(drawer).getByRole('region', { name: /linked alert/i })
      expect(within(linked).getByText(/prevented by maintenance/i)).toBeInTheDocument()
    })
  })
})
