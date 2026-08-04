import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { AppShell } from './AppShell'
import { server } from '../test/server'
import { operatorUser } from '../test/fixtures'
import { MockWebSocket } from '../test/mockWebSocket'

function harness() {
  return (
    <MemoryRouter initialEntries={['/']}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<div>dashboard content</div>} />
              <Route path="/notifications" element={<div>notifications content</div>} />
            </Route>
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('AppShell', () => {
  it('renders the full nav and user info for an admin', async () => {
    render(harness())
    expect(await screen.findByText('dashboard content')).toBeInTheDocument()

    const nav = within(screen.getByRole('navigation'))
    expect(nav.getByRole('link', { name: /dashboard/i })).toBeInTheDocument()
    expect(nav.getByRole('link', { name: /^users$/i })).toBeInTheDocument()
    expect(nav.getByRole('link', { name: /demo control/i })).toBeInTheDocument()
    expect(screen.getByText('Ada Admin')).toBeInTheDocument()
    expect(screen.getByText('admin')).toBeInTheDocument()
  })

  it('hides admin-only nav items and the notification bell for an operator', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    render(harness())
    await screen.findByText('dashboard content')

    const nav = within(screen.getByRole('navigation'))
    expect(nav.queryByRole('link', { name: /^users$/i })).not.toBeInTheDocument()
    expect(nav.queryByRole('link', { name: /demo control/i })).not.toBeInTheDocument()
    expect(nav.queryByRole('link', { name: /notifications/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /notifications/i })).not.toBeInTheDocument()
  })

  it('shows a reconnecting indicator until the socket opens', async () => {
    render(harness())
    await screen.findByText('dashboard content')
    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument()

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    MockWebSocket.instances[0].emitOpen()

    expect(await screen.findByText(/^live$/i)).toBeInTheDocument()
  })

  it('marks the notification bell as unseen on a new alert and clears it when visiting notifications', async () => {
    render(harness())
    await screen.findByText('dashboard content')
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    // Admin sees "Notifications" twice (sidebar item + bell aria-label) —
    // the bell is the one outside the sidebar <nav>.
    const nav = screen.getByRole('navigation')
    const bell = screen
      .getAllByRole('link', { name: /^notifications$/i })
      .find((el) => !nav.contains(el))
    if (!bell) throw new Error('notification bell not found')
    expect(bell.querySelector('.bg-accent-2')).toBeNull()

    socket.emitMessage({
      type: 'alert_created',
      machine_id: 'm1',
      alert: { severity: 'high', health_state: 'critical' },
    })

    await waitFor(() => expect(bell.querySelector('.bg-accent-2')).toBeTruthy())

    await userEvent.click(bell)
    expect(await screen.findByText('notifications content')).toBeInTheDocument()
  })

  it('logs out when the log out button is clicked', async () => {
    render(harness())
    await screen.findByText('dashboard content')

    await userEvent.click(screen.getByRole('button', { name: /log out/i }))

    await waitFor(() => expect(screen.queryByText('dashboard content')).not.toBeInTheDocument())
  })
})
