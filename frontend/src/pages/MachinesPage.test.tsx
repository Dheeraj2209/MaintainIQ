import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { MachinesPage } from './MachinesPage'
import { server } from '../test/server'

function MachineDetailPlaceholder() {
  const { id } = useParams()
  return <div>Selected machine: {id}</div>
}

function harness() {
  return (
    <MemoryRouter initialEntries={['/machines']}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route path="/machines" element={<MachinesPage />} />
            <Route path="/machines/:id" element={<MachineDetailPlaceholder />} />
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('MachinesPage', () => {
  it('loads and renders every machine in the fleet', async () => {
    render(harness())
    expect(await screen.findByRole('button', { name: /m1/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /m2/ })).toBeInTheDocument()
    expect(screen.getByText('2 machines in the fleet')).toBeInTheDocument()
  })

  it('filters machines by search text', async () => {
    render(harness())
    await screen.findByRole('button', { name: /m1/ })

    await userEvent.type(screen.getByLabelText(/search machines/i), 'm2')

    expect(screen.queryByRole('button', { name: /^m1/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /m2/ })).toBeInTheDocument()
  })

  it('filters machines by health state', async () => {
    render(harness())
    await screen.findByRole('button', { name: /m1/ })

    await userEvent.selectOptions(screen.getByLabelText(/filter by health state/i), 'healthy')

    expect(screen.queryByRole('button', { name: /^m1/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /m2/ })).toBeInTheDocument()
  })

  it('shows an empty state when no machine matches the filter', async () => {
    render(harness())
    await screen.findByRole('button', { name: /m1/ })

    await userEvent.type(screen.getByLabelText(/search machines/i), 'does-not-exist')

    expect(await screen.findByText(/no machines to display/i)).toBeInTheDocument()
  })

  it('shows an error banner when machines fail to load', async () => {
    server.use(http.get('/api/machines', () => HttpResponse.json({ detail: 'machines unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/machines unavailable/i)).toBeInTheDocument()
  })

  it('navigates to the machine detail page when a card is clicked', async () => {
    render(harness())
    await userEvent.click(await screen.findByRole('button', { name: /m1/ }))
    expect(await screen.findByText('Selected machine: m1')).toBeInTheDocument()
  })
})
