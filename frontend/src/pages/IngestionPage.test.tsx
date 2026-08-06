import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { IngestionPage } from './IngestionPage'
import { server } from '../test/server'
import { api } from '../api/client'

function harness() {
  return (
    <MemoryRouter initialEntries={['/ingestion']}>
      <AuthProvider>
        <IngestionPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('IngestionPage', () => {
  it('renders the replay status table', async () => {
    render(harness())

    const table = await screen.findByRole('table')
    const m1Row = (await within(table).findByText('m1')).closest('tr')!
    expect(within(m1Row).getByText(/running/i)).toBeInTheDocument()
    expect(within(m1Row).getByText('1500')).toBeInTheDocument()
  })

  it('starts a replay for the selected machine', async () => {
    const spy = vi.spyOn(api, 'startReplay')
    render(harness())

    await screen.findByLabelText(/machine/i)
    await userEvent.selectOptions(screen.getByLabelText(/machine/i), 'm2')
    await userEvent.click(screen.getByRole('button', { name: /start replay/i }))

    await waitFor(() => expect(spy).toHaveBeenCalledWith({ machine_id: 'm2', speed_multiplier: 1 }))
    spy.mockRestore()
  })

  it('shows an error banner when start fails', async () => {
    server.use(
      http.post('/api/ingestion/replay/start', () => HttpResponse.json({ detail: 'no readings for machine' }, { status: 404 })),
    )
    render(harness())

    await screen.findByLabelText(/machine/i)
    await userEvent.click(screen.getByRole('button', { name: /start replay/i }))
    expect(await screen.findByText(/no readings for machine/i)).toBeInTheDocument()
  })
})
