import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { NotificationsPage } from './NotificationsPage'
import { server } from '../test/server'
import { notifications } from '../test/fixtures'

function harness() {
  return (
    <MemoryRouter initialEntries={['/notifications']}>
      <AuthProvider>
        <LiveEventsProvider>
          <NotificationsPage />
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('NotificationsPage', () => {
  it('loads and renders the notification log', async () => {
    render(harness())
    expect(await screen.findByText('admin@maintainiq.local')).toBeInTheDocument()
    expect(screen.getByText('supervisor@maintainiq.local')).toBeInTheDocument()
    expect(screen.getAllByText(/maintainiq critical: m1 needs attention/i)).toHaveLength(2)
    expect(screen.getByText('Email delivery log — 2 entries')).toBeInTheDocument()
    expect(screen.getByText('sent')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
  })

  it('filters notifications by status', async () => {
    server.use(
      http.get('/api/notifications', ({ request }) => {
        const status = new URL(request.url).searchParams.get('status')
        const filtered = status ? notifications.filter((n) => n.status === status) : notifications
        return HttpResponse.json(filtered)
      }),
    )
    render(harness())
    await screen.findByText('Email delivery log — 2 entries')

    await userEvent.selectOptions(screen.getByLabelText(/status/i), 'sent')

    expect(await screen.findByText('Email delivery log — 1 entries')).toBeInTheDocument()
    expect(screen.getByText('admin@maintainiq.local')).toBeInTheDocument()
    expect(screen.queryByText('supervisor@maintainiq.local')).not.toBeInTheDocument()
  })

  it('expands a row to show the notification body and collapses it again', async () => {
    render(harness())
    const row = (await screen.findByText('admin@maintainiq.local')).closest('tr')
    if (!row) throw new Error('row not found')

    expect(screen.queryByText(/health state: critical/i)).not.toBeInTheDocument()
    await userEvent.click(row)
    expect(await screen.findByText(/health state: critical/i)).toBeInTheDocument()

    await userEvent.click(row)
    expect(screen.queryByText(/health state: critical/i)).not.toBeInTheDocument()
  })

  it('shows an empty state when there are no notifications', async () => {
    server.use(http.get('/api/notifications', () => HttpResponse.json([])))
    render(harness())
    expect(await screen.findByText(/no notifications logged yet/i)).toBeInTheDocument()
  })

  it('shows an error banner when notifications fail to load', async () => {
    server.use(
      http.get('/api/notifications', () => HttpResponse.json({ detail: 'notifications unavailable' }, { status: 500 })),
    )
    render(harness())
    expect(await screen.findByText(/notifications unavailable/i)).toBeInTheDocument()
  })
})
