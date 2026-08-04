import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { MaintenancePage } from './MaintenancePage'
import { server } from '../test/server'

function MachineDetailPlaceholder() {
  const { id } = useParams()
  return <div>Selected machine: {id}</div>
}

function harness() {
  return (
    <MemoryRouter initialEntries={['/maintenance']}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route path="/maintenance" element={<MaintenancePage />} />
            <Route path="/machines/:id" element={<MachineDetailPlaceholder />} />
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('MaintenancePage', () => {
  it('shows a loading message before history resolves', () => {
    render(harness())
    expect(screen.getByText(/^loading…$/i)).toBeInTheDocument()
  })

  it('shows an empty state when no maintenance records exist', async () => {
    render(harness())
    expect(await screen.findByText(/no maintenance records logged yet/i)).toBeInTheDocument()
  })

  it('renders the maintenance history table and links back to the machine', async () => {
    server.use(
      http.get('/api/maintenance/:id', ({ params }) => {
        if (params.id !== 'm1') return HttpResponse.json([])
        return HttpResponse.json([
          {
            id: 1,
            machine_id: 'm1',
            performed_at: '2003-10-22T10:00:00+00:00',
            description: 'Replaced bearing',
            technician: 'Tina',
            created_at: '2003-10-22T10:05:00+00:00',
          },
        ])
      }),
    )
    render(harness())
    expect(await screen.findByText('Replaced bearing')).toBeInTheDocument()
    expect(screen.getByText('Tina')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'm1' }))
    expect(await screen.findByText('Selected machine: m1')).toBeInTheDocument()
  })

  it('shows an error banner when maintenance data fails to load', async () => {
    server.use(http.get('/api/machines', () => HttpResponse.json({ detail: 'machines unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/machines unavailable/i)).toBeInTheDocument()
  })

  it('logs a new maintenance record and adds it to the history table', async () => {
    render(harness())
    await screen.findByText(/no maintenance records logged yet/i)

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-23T10:00')
    await userEvent.type(screen.getByLabelText(/description/i), 'Replaced belt')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(await screen.findByText(/logged\./i)).toBeInTheDocument()
    expect(screen.getByText('Replaced belt')).toBeInTheDocument()
    expect(screen.queryByText(/no maintenance records logged yet/i)).not.toBeInTheDocument()
  })
})
