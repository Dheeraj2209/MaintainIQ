import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { AlertCloseDialog } from './AlertCloseDialog'
import { AlertCloseForm } from './AlertCloseForm'
import { server } from '../test/server'
import { alertFeedback, openAlerts, operatorUser, resolvedAlertWithFeedback } from '../test/fixtures'
import type { Alert, AlertFeedback, UserOut } from '../api/types'

type Saved = { alert: Alert; feedback: AlertFeedback; closed: boolean }

function openAlert(changes: Partial<Alert> = {}): Alert {
  return { ...structuredClone(openAlerts[0]), acknowledged_at: null, acknowledged_by: null, ...changes }
}

function resolvedAlert(changes: Partial<Alert> = {}): Alert {
  return { ...structuredClone(resolvedAlertWithFeedback), feedback: null, closed_by: null, ...changes }
}

function asUser(user: UserOut) {
  server.use(http.get('/api/auth/me', () => HttpResponse.json(user)))
}

// Mirrors how pages use the dialog: an opener button that gets focus back.
function Harness({ alert, onSaved = () => {} }: { alert: Alert; onSaved?: (r: Saved) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        open dialog
      </button>
      <AlertCloseDialog
        open={open}
        alert={open ? alert : null}
        onClose={() => setOpen(false)}
        onSaved={(r) => {
          setOpen(false)
          onSaved(r)
        }}
      />
    </>
  )
}

function renderDialog(alert: Alert, onSaved?: (r: Saved) => void) {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Harness alert={alert} onSaved={onSaved} />
      </AuthProvider>
    </MemoryRouter>,
  )
}

async function openDialog(alert: Alert, onSaved?: (r: Saved) => void) {
  renderDialog(alert, onSaved)
  await userEvent.click(screen.getByRole('button', { name: /open dialog/i }))
  return screen.findByRole('dialog')
}

describe('AlertCloseDialog', () => {
  it('offers "Close alert" for an open alert, with submit disabled until an outcome is picked', async () => {
    const dialog = await openDialog(openAlert())

    expect(within(dialog).getByRole('heading', { name: /close alert/i })).toBeInTheDocument()
    const submit = within(dialog).getByRole('button', { name: /^close alert$/i })
    expect(submit).toBeDisabled()

    await userEvent.click(within(dialog).getByRole('radio', { name: /false alarm/i }))
    expect(submit).toBeEnabled()
    expect(within(dialog).getByText(/next reading opens a new alert/i)).toBeInTheDocument()
  })

  it('preselects no outcome and defaults the cause to "Not specified" with the model hint', async () => {
    const dialog = await openDialog(openAlert())

    const group = within(dialog).getByRole('radiogroup', { name: /what actually happened/i })
    const radios = within(group).getAllByRole('radio')
    expect(radios).toHaveLength(4)
    radios.forEach((r) => expect(r).not.toBeChecked())

    const cause = within(dialog).getByLabelText(/actual cause/i)
    expect(cause).toHaveValue('')
    expect(within(cause).getByRole('option', { name: /not specified/i })).toBeInTheDocument()
    expect(within(cause).getAllByRole('option')).toHaveLength(6)
    expect(within(dialog).getByText(/model said: bearing wear/i)).toBeInTheDocument()
  })

  it('shows the failure-time input only for a confirmed failure', async () => {
    const dialog = await openDialog(openAlert())

    expect(within(dialog).queryByLabelText(/failure time/i)).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('radio', { name: /confirmed failure/i }))
    expect(within(dialog).getByLabelText(/failure time/i)).toHaveAttribute('type', 'datetime-local')
    await userEvent.click(within(dialog).getByRole('radio', { name: /false alarm/i }))
    expect(within(dialog).queryByLabelText(/failure time/i)).not.toBeInTheDocument()
  })

  it('posts the exact body to /close for an open alert and reports the result', async () => {
    const bodies: unknown[] = []
    const paths: string[] = []
    server.use(
      http.post('/api/alerts/:id/close', async ({ request }) => {
        paths.push(new URL(request.url).pathname)
        bodies.push(await request.clone().json())
        return undefined
      }),
    )
    const onSaved = vi.fn()
    const dialog = await openDialog(openAlert(), onSaved)

    await userEvent.click(within(dialog).getByRole('radio', { name: /confirmed failure/i }))
    await userEvent.selectOptions(within(dialog).getByLabelText(/actual cause/i), 'imbalance')
    await userEvent.type(within(dialog).getByLabelText(/failure time/i), '2026-10-01T10:30')
    await userEvent.type(within(dialog).getByLabelText(/notes/i), '  Coupling sheared  ')
    await userEvent.click(within(dialog).getByRole('button', { name: /^close alert$/i }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(paths).toEqual(['/api/alerts/2/close'])
    expect(bodies).toEqual([
      {
        outcome: 'confirmed_failure',
        actual_cause: 'imbalance',
        actual_failure_at: new Date('2026-10-01T10:30').toISOString(),
        notes: 'Coupling sheared',
        work_order_id: null,
      },
    ])
    const result = onSaved.mock.calls[0][0] as Saved
    expect(result.closed).toBe(true)
    expect(result.alert.status).toBe('resolved')
    expect(result.feedback.outcome).toBe('confirmed_failure')
  })

  it('PUTs /feedback for a resolved alert without an outcome', async () => {
    const bodies: unknown[] = []
    server.use(
      http.put('/api/alerts/:id/feedback', async ({ request }) => {
        bodies.push(await request.clone().json())
        return undefined
      }),
      http.post('/api/alerts/:id/close', () => HttpResponse.json({ detail: 'should not close' }, { status: 500 })),
    )
    const onSaved = vi.fn()
    const dialog = await openDialog(resolvedAlert(), onSaved)

    expect(within(dialog).getByRole('heading', { name: /record outcome/i })).toBeInTheDocument()
    expect(within(dialog).queryByText(/next reading opens a new alert/i)).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('radio', { name: /false alarm/i }))
    await userEvent.click(within(dialog).getByRole('button', { name: /^record outcome$/i }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(bodies).toEqual([
      { outcome: 'false_alarm', actual_cause: null, actual_failure_at: null, notes: null, work_order_id: null },
    ])
    const result = onSaved.mock.calls[0][0] as Saved
    expect(result.closed).toBe(false)
    expect(result.alert.feedback?.outcome).toBe('false_alarm')
  })

  it('pre-fills the form from existing feedback in edit mode', async () => {
    const dialog = await openDialog(structuredClone(resolvedAlertWithFeedback))

    expect(await within(dialog).findByRole('heading', { name: /edit outcome/i })).toBeInTheDocument()
    expect(within(dialog).getByRole('radio', { name: /prevented by maintenance/i })).toBeChecked()
    expect(within(dialog).getByLabelText(/actual cause/i)).toHaveValue('bearing_wear')
    expect(within(dialog).getByLabelText(/notes/i)).toHaveValue(alertFeedback.notes)
    expect(within(dialog).getByRole('button', { name: /save outcome/i })).toBeEnabled()
  })

  it("is read-only for an operator viewing someone else's feedback", async () => {
    asUser(operatorUser)
    const dialog = await openDialog(structuredClone(resolvedAlertWithFeedback))

    expect(
      await within(dialog).findByText(/recorded by sam supervisor — only they, an admin or a supervisor can change it/i),
    ).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /save outcome/i })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('radio')).not.toBeInTheDocument()
    expect(within(dialog).getByText(/prevented by maintenance/i)).toBeInTheDocument()
  })

  it('shows a server error inline and keeps the dialog open', async () => {
    server.use(
      http.post('/api/alerts/:id/close', () =>
        HttpResponse.json({ detail: 'actual_failure_at is in the future' }, { status: 400 }),
      ),
    )
    const onSaved = vi.fn()
    const dialog = await openDialog(openAlert(), onSaved)

    await userEvent.click(within(dialog).getByRole('radio', { name: /unknown/i }))
    await userEvent.click(within(dialog).getByRole('button', { name: /^close alert$/i }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/actual_failure_at is in the future/i)
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('links the active work order and says it stays open', async () => {
    const bodies: Record<string, unknown>[] = []
    server.use(
      http.post('/api/alerts/:id/close', async ({ request }) => {
        bodies.push((await request.clone().json()) as Record<string, unknown>)
        return undefined
      }),
    )
    const dialog = await openDialog(openAlert({ active_work_order_id: 7 }))

    expect(within(dialog).getByText(/linked to work order #7/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/work order #7 stays open — complete or cancel it separately/i)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('radio', { name: /prevented by maintenance/i }))
    await userEvent.click(within(dialog).getByRole('button', { name: /^close alert$/i }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0].work_order_id).toBe(7)
  })

  describe('what closing does to the held health state', () => {
    it('says the machine stays held after a confirmed failure, linking to work-order creation', async () => {
      const dialog = await openDialog(openAlert())

      await userEvent.click(within(dialog).getByRole('radio', { name: /confirmed failure/i }))
      expect(within(dialog).getByText(/the machine stays held at critical until a repair is recorded/i)).toBeInTheDocument()
      expect(within(dialog).getByRole('link', { name: /create a work order/i })).toHaveAttribute('href', '/machines/m1')
      expect(within(dialog).queryByText(/health tracking re-armed/i)).not.toBeInTheDocument()
    })

    it('links the active work order to complete after prevented-by-maintenance', async () => {
      const dialog = await openDialog(openAlert({ active_work_order_id: 7 }))

      await userEvent.click(within(dialog).getByRole('radio', { name: /prevented by maintenance/i }))
      expect(within(dialog).getByText(/stays held at critical/i)).toBeInTheDocument()
      expect(within(dialog).getByRole('link', { name: /complete work order #7/i })).toHaveAttribute('href', '/work-orders/7')
    })

    it('says health tracking re-arms on a false alarm', async () => {
      const dialog = await openDialog(openAlert())

      await userEvent.click(within(dialog).getByRole('radio', { name: /false alarm/i }))
      expect(within(dialog).getByText(/health tracking re-armed/i)).toBeInTheDocument()
      expect(within(dialog).queryByText(/stays held/i)).not.toBeInTheDocument()
    })

    it('says nothing about holding when recording on an already-resolved alert', async () => {
      const dialog = await openDialog(resolvedAlert())

      await userEvent.click(within(dialog).getByRole('radio', { name: /confirmed failure/i }))
      expect(within(dialog).queryByText(/stays held/i)).not.toBeInTheDocument()
    })
  })

  it('closes on Escape and returns focus to the opener', async () => {
    renderDialog(openAlert())
    const opener = screen.getByRole('button', { name: /open dialog/i })
    await userEvent.click(opener)
    await screen.findByRole('dialog')

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('is labelled by its heading and modal', async () => {
    const dialog = await openDialog(openAlert())
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('dialog', { name: /close alert/i })).toBe(dialog)
  })
})

describe('AlertCloseForm', () => {
  it('uses the given work order, initial notes and cancel label', async () => {
    const onCancel = vi.fn()
    const bodies: Record<string, unknown>[] = []
    server.use(
      http.post('/api/alerts/:id/close', async ({ request }) => {
        bodies.push((await request.clone().json()) as Record<string, unknown>)
        return undefined
      }),
    )
    render(
      <MemoryRouter>
        <AuthProvider>
          <AlertCloseForm
            alert={openAlert({ active_work_order_id: 3 })}
            workOrderId={3}
            initialNotes="Bearing replaced"
            cancelLabel="Skip"
            onSaved={() => {}}
            onCancel={onCancel}
          />
        </AuthProvider>
      </MemoryRouter>,
    )

    expect(screen.getByLabelText(/notes/i)).toHaveValue('Bearing replaced')
    await userEvent.click(screen.getByRole('button', { name: /^skip$/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)

    await userEvent.click(screen.getByRole('radio', { name: /prevented by maintenance/i }))
    await userEvent.click(screen.getByRole('button', { name: /^close alert$/i }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toMatchObject({ work_order_id: 3, notes: 'Bearing replaced' })
  })
})
