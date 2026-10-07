import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { CreateWorkOrderDialog } from './CreateWorkOrderDialog'
import { server } from '../test/server'
import { openAlerts, operatorUser, supervisorUser } from '../test/fixtures'
import type { Alert, WorkOrder } from '../api/types'

const alert: Alert = openAlerts[0]

// The dialog is controlled; this opener mirrors how pages use it (a button
// that opens it and receives focus back when it closes).
function Harness({
  withAlert = true,
  machineId,
  onCreated = () => {},
}: {
  withAlert?: boolean
  machineId?: string
  onCreated?: (wo: WorkOrder) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        open dialog
      </button>
      <CreateWorkOrderDialog
        open={open}
        alert={withAlert ? alert : null}
        machineId={machineId}
        onClose={() => setOpen(false)}
        onCreated={(wo) => {
          onCreated(wo)
          setOpen(false)
        }}
      />
    </>
  )
}

function renderDialog(props: Parameters<typeof Harness>[0] = {}) {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Harness {...props} />
      </AuthProvider>
    </MemoryRouter>,
  )
}

async function openDialog() {
  await userEvent.click(screen.getByRole('button', { name: /open dialog/i }))
  return screen.findByRole('dialog', { name: /create work order/i })
}

describe('CreateWorkOrderDialog', () => {
  it('prefills the title from the alert message and the priority from its severity', async () => {
    renderDialog()
    const dialog = await openDialog()

    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByLabelText(/title/i)).toHaveValue('m1 critical')
    expect(within(dialog).getByLabelText(/priority/i)).toHaveValue('high')
    expect(within(dialog).getByText(/alert #2/i)).toBeInTheDocument()
  })

  it('keeps the panel scrollable within the viewport', async () => {
    // jsdom has no layout, so pin the classes: a centred fixed overlay cannot
    // scroll, so on a short (phone/landscape/keyboard-open) viewport the panel
    // itself must cap its height and scroll, or its header and submit button
    // end up off-screen.
    renderDialog()
    const dialog = await openDialog()
    expect(dialog).toHaveClass('overflow-y-auto')
    expect(dialog.className).toMatch(/max-h-\[calc\(100dvh-2rem\)\]/)
  })

  it('falls back to "{machine}: {state} alert" when the alert has no message', async () => {
    const noMessage = { ...alert, message: null }
    render(
      <MemoryRouter>
        <AuthProvider>
          <CreateWorkOrderDialog open alert={noMessage} onClose={() => {}} onCreated={() => {}} />
        </AuthProvider>
      </MemoryRouter>,
    )
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/title/i)).toHaveValue('m1: critical alert')
  })

  it.each([
    ['admin', undefined],
    ['supervisor', supervisorUser],
  ])('shows the assignee select to a %s, filled from /assignees', async (_role, user) => {
    if (user) server.use(http.get('/api/auth/me', () => HttpResponse.json(user)))
    renderDialog()
    const dialog = await openDialog()

    const select = await within(dialog).findByLabelText(/assign to/i)
    await waitFor(() => expect(within(select).getByRole('option', { name: /ollie operator/i })).toBeInTheDocument())
    expect(within(select).getByRole('option', { name: /unassigned/i })).toBeInTheDocument()
  })

  it('hides the assignee select from an operator and never requests assignees', async () => {
    let assigneeRequests = 0
    server.use(
      http.get('/api/auth/me', () => HttpResponse.json(operatorUser)),
      http.get('/api/work-orders/assignees', () => {
        assigneeRequests += 1
        return HttpResponse.json({ detail: 'forbidden' }, { status: 403 })
      }),
    )
    renderDialog()
    // Wait for /me to resolve so the role is known before opening.
    await new Promise((r) => setTimeout(r, 50))
    const dialog = await openDialog()

    expect(within(dialog).getByLabelText(/title/i)).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 50))
    expect(within(dialog).queryByLabelText(/assign to/i)).not.toBeInTheDocument()
    expect(assigneeRequests).toBe(0)
  })

  it('posts to the alert endpoint and reports the created order', async () => {
    const bodies: unknown[] = []
    const paths: string[] = []
    server.use(
      http.post('/api/alerts/:id/work-order', async ({ request }) => {
        paths.push(new URL(request.url).pathname)
        bodies.push(await request.json())
        return HttpResponse.json({ id: 10, machine_id: 'm1', status: 'assigned' }, { status: 201 })
      }),
    )
    const onCreated = vi.fn()
    renderDialog({ onCreated })
    const dialog = await openDialog()

    const select = await within(dialog).findByLabelText(/assign to/i)
    await within(select).findByRole('option', { name: /ollie operator/i })
    await userEvent.selectOptions(select, '3')
    await userEvent.type(within(dialog).getByLabelText(/description/i), 'Swap the drive-end bearing')
    await userEvent.click(within(dialog).getByRole('button', { name: /create work order/i }))

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: 10 })))
    expect(paths).toEqual(['/api/alerts/2/work-order'])
    expect(bodies[0]).toEqual({
      title: 'm1 critical',
      description: 'Swap the drive-end bearing',
      priority: 'high',
      assigned_to: 3,
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows a 409 inline with a link to the existing work order', async () => {
    server.use(
      http.post('/api/alerts/:id/work-order', () =>
        HttpResponse.json({ detail: 'alert 2 already has an active work order: #7' }, { status: 409 }),
      ),
    )
    const onCreated = vi.fn()
    renderDialog({ onCreated })
    const dialog = await openDialog()

    await userEvent.click(within(dialog).getByRole('button', { name: /create work order/i }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/already has an active work order: #7/i)
    expect(within(dialog).getByRole('link', { name: /open work order #7/i })).toHaveAttribute('href', '/work-orders/7')
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('lists machines in free-standing mode and posts to /work-orders', async () => {
    const bodies: Record<string, unknown>[] = []
    server.use(
      http.post('/api/work-orders', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>)
        return HttpResponse.json({ id: 11, machine_id: 'm2', status: 'open' }, { status: 201 })
      }),
    )
    const onCreated = vi.fn()
    renderDialog({ withAlert: false, onCreated })
    await userEvent.click(screen.getByRole('button', { name: /open dialog/i }))
    const dialog = await screen.findByRole('dialog', { name: /new work order/i })

    const machine = within(dialog).getByLabelText(/machine/i)
    await within(machine).findByRole('option', { name: 'm2' })
    await userEvent.selectOptions(machine, 'm2')
    expect(within(dialog).getByLabelText(/title/i)).toHaveValue('')
    expect(within(dialog).getByLabelText(/priority/i)).toHaveValue('medium')
    await userEvent.type(within(dialog).getByLabelText(/title/i), 'Inspect coupling')
    await userEvent.click(within(dialog).getByRole('button', { name: /create work order/i }))

    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    expect(bodies).toEqual([{ machine_id: 'm2', title: 'Inspect coupling', priority: 'medium' }])
  })

  it('uses a fixed machine instead of a picker when one is given', async () => {
    const bodies: Record<string, unknown>[] = []
    server.use(
      http.post('/api/work-orders', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>)
        return HttpResponse.json({ id: 11, machine_id: 'm1', status: 'open' }, { status: 201 })
      }),
    )
    renderDialog({ withAlert: false, machineId: 'm1' })
    await userEvent.click(screen.getByRole('button', { name: /open dialog/i }))
    const dialog = await screen.findByRole('dialog', { name: /new work order/i })

    expect(within(dialog).queryByRole('combobox', { name: /machine/i })).not.toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText(/title/i), 'Check m1')
    await userEvent.click(within(dialog).getByRole('button', { name: /create work order/i }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toMatchObject({ machine_id: 'm1', title: 'Check m1' })
  })

  it('closes on Escape and returns focus to the opener', async () => {
    renderDialog()
    await openDialog()

    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: /open dialog/i })).toHaveFocus()
  })
})
