import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse, delay } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { DemoPage } from './DemoPage'
import { server } from '../test/server'

function harness() {
  return (
    <MemoryRouter initialEntries={['/demo']}>
      <AuthProvider>
        <DemoPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('DemoPage', () => {
  it('loads machines and preselects the first healthy one', async () => {
    render(harness())
    expect(await screen.findByText(/no simulations run yet this session/i)).toBeInTheDocument()
    // m1 is fixtured as 'critical' (already alerted) and m2 as 'healthy' —
    // defaulting to m1 would make the default action a silent no-op.
    expect(screen.getByLabelText(/machine/i)).toHaveValue('m2')
  })

  it('simulates a fault and logs the result', async () => {
    render(harness())
    await screen.findByLabelText(/machine/i)

    await userEvent.selectOptions(screen.getByLabelText(/severity/i), 'faulty')
    await userEvent.click(screen.getByRole('button', { name: /simulate reading/i }))

    const entryHeader = await screen.findByText('m2 → faulty')
    const entry = within(entryHeader.closest('li')!)
    expect(entry.getByText('faulty')).toBeInTheDocument()
    expect(entry.getByText(/cause: bearing_wear/i)).toBeInTheDocument()
    expect(entry.getByText(/no alert state change/i)).toBeInTheDocument()
    expect(screen.queryByText(/no simulations run yet this session/i)).not.toBeInTheDocument()
  })

  it('disables the submit button and shows a busy label while simulating', async () => {
    server.use(
      http.post('/api/demo/simulate-fault', async ({ request }) => {
        const body = (await request.json()) as { machine_id: string; severity: string }
        await delay(50)
        return HttpResponse.json({
          machine_id: body.machine_id,
          health_state: body.severity,
          probable_cause: null,
          alert: null,
          emails_sent: 0,
        })
      }),
    )
    render(harness())
    await screen.findByLabelText(/machine/i)

    await userEvent.click(screen.getByRole('button', { name: /simulate reading/i }))
    expect(screen.getByRole('button', { name: /simulating/i })).toBeDisabled()

    await waitFor(() => expect(screen.getByRole('button', { name: /simulate reading/i })).toBeEnabled())
  })

  it('shows an error banner when the simulation fails', async () => {
    server.use(
      http.post('/api/demo/simulate-fault', () => HttpResponse.json({ detail: 'simulation unavailable' }, { status: 500 })),
    )
    render(harness())
    await screen.findByLabelText(/machine/i)

    await userEvent.click(screen.getByRole('button', { name: /simulate reading/i }))
    expect(await screen.findByText(/simulation unavailable/i)).toBeInTheDocument()
  })

  it('shows an error when machines fail to load', async () => {
    server.use(http.get('/api/machines', () => HttpResponse.json({ detail: 'machines unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/machines unavailable/i)).toBeInTheDocument()
  })
})
