import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { AppShell } from './AppShell'
import { server } from '../test/server'
import { alertFeedback, openAlerts, operatorUser, workOrders } from '../test/fixtures'
import { MockWebSocket } from '../test/mockWebSocket'
import { MOBILE_SUGGEST_DISMISSED_KEY } from '../components/MobileViewSuggestion'

function harness() {
  return (
    <MemoryRouter initialEntries={['/']}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route
                path="/"
                element={
                  <div>
                    dashboard content <Link to="/elsewhere">go elsewhere</Link>
                  </div>
                }
              />
              <Route path="/elsewhere" element={<div>elsewhere content</div>} />
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

  it('shows the Devices link under Monitor for every role, including operators', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    render(harness())
    await screen.findByText('dashboard content')

    const nav = within(screen.getByRole('navigation'))
    expect(nav.getByRole('link', { name: /^devices$/i })).toHaveAttribute('href', '/devices')
    // Listed right after Alerts, inside the Monitor section.
    const labels = nav.getAllByRole('link').map((l) => l.textContent)
    expect(labels.indexOf('Devices')).toBe(labels.indexOf('Alerts') + 1)
  })

  it('shows the Work orders link first under Operations for every role', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    render(harness())
    await screen.findByText('dashboard content')

    const nav = within(screen.getByRole('navigation'))
    expect(nav.getByRole('link', { name: /^work orders$/i })).toHaveAttribute('href', '/work-orders')
    const labels = nav.getAllByRole('link').map((l) => l.textContent)
    expect(labels.indexOf('Work orders')).toBe(labels.indexOf('Maintenance') - 1)
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

  it('marks the bell unseen when a sensor node goes silent but not when one comes back', async () => {
    render(harness())
    await screen.findByText('dashboard content')
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    const nav = screen.getByRole('navigation')
    const bell = screen
      .getAllByRole('link', { name: /^notifications$/i })
      .find((el) => !nav.contains(el))
    if (!bell) throw new Error('notification bell not found')

    const incident = {
      id: 1,
      device_id: 'simdev-02',
      machine_id: 'sim-02',
      kind: 'silent',
      status: 'resolved',
      opened_at: '2026-10-06T11:50:30Z',
      last_seen_at: '2026-10-06T12:00:00Z',
      resolved_at: '2026-10-06T12:00:00Z',
      acknowledged_at: null,
      acknowledged_by: null,
    }
    socket.emitMessage({ type: 'device_online', device_id: 'simdev-02', machine_id: 'sim-02', incident, at: 'x' })
    await new Promise((r) => setTimeout(r, 50))
    expect(bell.querySelector('.bg-accent-2')).toBeNull()

    // Silence pages admins/supervisors, so it lights the bell.
    socket.emitMessage({
      type: 'device_offline',
      device_id: 'simdev-02',
      machine_id: 'sim-02',
      incident: { ...incident, status: 'open', resolved_at: null },
      at: 'x',
    })
    await waitFor(() => expect(bell.querySelector('.bg-accent-2')).toBeTruthy())
  })

  it('lights the bell for a paged alert but not for work-order or acknowledgement events', async () => {
    render(harness())
    await screen.findByText('dashboard content')
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    const nav = screen.getByRole('navigation')
    const bell = screen
      .getAllByRole('link', { name: /^notifications$/i })
      .find((el) => !nav.contains(el))
    if (!bell) throw new Error('notification bell not found')

    // Neither pages anyone: work-order activity is realtime-only, and an
    // acknowledgement stops paging rather than starting it.
    socket.emitMessage({ type: 'work_order_updated', machine_id: 'm1', work_order: workOrders[2], change: 'started', at: 'x' })
    socket.emitMessage({ type: 'alert_acknowledged', machine_id: 'm1', alert: openAlerts[0], at: 'x' })
    await new Promise((r) => setTimeout(r, 50))
    expect(bell.querySelector('.bg-accent-2')).toBeNull()

    socket.emitMessage({ type: 'alert_paged', machine_id: 'm1', alert: openAlerts[0], page_level: 1, at: 'x' })
    await waitFor(() => expect(bell.querySelector('.bg-accent-2')).toBeTruthy())
  })

  it('keeps the header narrow on phones: status and log out text are sr-only below sm', async () => {
    // Regression: at 375 px 'Reconnecting…' plus the 'Log out' text overflowed
    // the non-wrapping header (sideways scroll, clipped Log out button).
    render(harness())
    await screen.findByText('dashboard content')
    for (const id of ['connection-label', 'logout-label']) {
      const label = screen.getByTestId(id)
      expect(label).toHaveClass('sr-only', 'sm:not-sr-only', 'whitespace-nowrap')
    }
    expect(screen.getByRole('button', { name: /log out/i })).toBeInTheDocument()
    expect(screen.getByTestId('connection-label').parentElement).toHaveClass('min-w-0')
  })

  it('logs out when the log out button is clicked', async () => {
    render(harness())
    await screen.findByText('dashboard content')

    await userEvent.click(screen.getByRole('button', { name: /log out/i }))

    await waitFor(() => expect(screen.queryByText('dashboard content')).not.toBeInTheDocument())
  })

  it('does not light the bell when an alert is closed or its outcome recorded', async () => {
    render(harness())
    await screen.findByText('dashboard content')
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    const socket = MockWebSocket.instances[0]
    socket.emitOpen()

    const nav = screen.getByRole('navigation')
    const bell = screen
      .getAllByRole('link', { name: /^notifications$/i })
      .find((el) => !nav.contains(el))
    if (!bell) throw new Error('notification bell not found')

    const alert = { ...openAlerts[0], status: 'resolved' }
    const feedback = { ...alertFeedback, alert_id: alert.id }
    socket.emitMessage({ type: 'alert_closed', machine_id: 'm1', alert, feedback, at: 'x' })
    socket.emitMessage({ type: 'alert_feedback_recorded', machine_id: 'm1', alert, feedback, at: 'x' })
    await new Promise((r) => setTimeout(r, 50))
    expect(bell.querySelector('.bg-accent-2')).toBeNull()
  })

  describe('on small screens', () => {
    // matchMedia answering `narrow` for the phone breakpoint (and false for
    // anything else, such as framer-motion's reduced-motion query).
    function viewport(narrow: boolean) {
      vi.spyOn(window, 'matchMedia').mockImplementation(
        (query: string) =>
          ({
            matches: narrow && query === '(max-width: 640px)',
            media: query,
            onchange: null,
            addListener: () => {},
            removeListener: () => {},
            addEventListener: () => {},
            removeEventListener: () => {},
            dispatchEvent: () => false,
          }) as unknown as MediaQueryList,
      )
    }

    afterEach(() => {
      vi.restoreAllMocks()
      localStorage.clear()
    })

    it('opens the navigation drawer from the menu button and closes it on Escape', async () => {
      render(harness())
      await screen.findByText('dashboard content')
      const menu = screen.getByRole('button', { name: 'Open navigation' })
      expect(menu).toHaveAttribute('aria-expanded', 'false')
      expect(menu).toHaveAttribute('aria-controls', 'app-nav-drawer')

      await userEvent.click(menu)

      const drawer = screen.getByRole('dialog', { name: /navigation/i })
      expect(drawer).toHaveAttribute('id', 'app-nav-drawer')
      expect(menu).toHaveAttribute('aria-expanded', 'true')
      expect(within(drawer).getByRole('link', { name: /^alerts$/i })).toHaveAttribute('href', '/alerts')

      await userEvent.keyboard('{Escape}')
      expect(screen.queryByRole('dialog', { name: /navigation/i })).not.toBeInTheDocument()
    })

    it('closes the drawer on navigation', async () => {
      render(harness())
      await screen.findByText('dashboard content')
      await userEvent.click(screen.getByRole('button', { name: 'Open navigation' }))
      expect(screen.getByRole('dialog', { name: /navigation/i })).toBeInTheDocument()

      await userEvent.click(screen.getByRole('link', { name: 'go elsewhere' }))

      expect(await screen.findByText('elsewhere content')).toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: /navigation/i })).not.toBeInTheDocument()
    })

    it('links to the mobile view for every role', async () => {
      server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
      render(harness())
      await screen.findByText('dashboard content')
      expect(screen.getByRole('link', { name: 'Mobile view' })).toHaveAttribute('href', '/m')
    })

    it('suggests the mobile view on a phone until dismissed, and remembers it', async () => {
      viewport(true)
      const first = render(harness())
      await screen.findByText('dashboard content')
      const banner = screen.getByRole('region', { name: /mobile view suggestion/i })
      expect(within(banner).getByText(/on a phone\? the operator view is built for it/i)).toBeInTheDocument()
      expect(within(banner).getByRole('link', { name: 'Open mobile view' })).toHaveAttribute('href', '/m')

      await userEvent.click(within(banner).getByRole('button', { name: 'Dismiss' }))

      expect(screen.queryByRole('region', { name: /mobile view suggestion/i })).not.toBeInTheDocument()
      expect(localStorage.getItem(MOBILE_SUGGEST_DISMISSED_KEY)).toBe('1')

      first.unmount()
      render(harness())
      await screen.findByText('dashboard content')
      expect(screen.queryByRole('region', { name: /mobile view suggestion/i })).not.toBeInTheDocument()
    })

    it('never suggests the mobile view on a wide screen', async () => {
      viewport(false)
      render(harness())
      await screen.findByText('dashboard content')
      expect(screen.queryByRole('region', { name: /mobile view suggestion/i })).not.toBeInTheDocument()
    })

    it('offers the push switch for this device from the header', async () => {
      render(harness())
      await screen.findByText('dashboard content')
      const button = screen.getByRole('button', { name: 'This device' })

      await userEvent.click(button)

      expect(button).toHaveAttribute('aria-expanded', 'true')
      expect(await screen.findByRole('switch', { name: /push notifications on this device/i })).toBeInTheDocument()

      await userEvent.click(screen.getByText('dashboard content'))
      expect(screen.queryByRole('switch', { name: /push notifications/i })).not.toBeInTheDocument()
    })
  })
})
