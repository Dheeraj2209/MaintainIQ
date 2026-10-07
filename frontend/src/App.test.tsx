import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import App from './App'
import { server } from './test/server'
import { operatorUser, supervisorUser } from './test/fixtures'

// App.tsx hardcodes BrowserRouter (not a testable Router prop), so deep-link
// tests seed the URL via history before render — BrowserRouter reads
// window.location at construction, same as a real page load would.
function goTo(path: string) {
  window.history.pushState({}, '', path)
}

afterEach(() => {
  window.history.pushState({}, '', '/')
})

async function machineList() {
  return within(await screen.findByRole('region', { name: /machine list/i }))
}

describe('App', () => {
  it('serves the public landing page at / without requiring auth', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })))
    render(<App />)
    expect(await screen.findByRole('heading', { name: /predict failures/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /start monitoring/i })).toBeInTheDocument()
  })

  it('redirects an unauthenticated visitor to /login', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })))
    goTo('/dashboard')
    render(<App />)
    expect(await screen.findByRole('heading', { name: /maintainiq/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
  })

  it('renders the dashboard for an authenticated admin and opens machine detail', async () => {
    goTo('/dashboard')
    render(<App />)

    expect(await screen.findByRole('region', { name: /kpi/i })).toBeInTheDocument()
    expect(screen.getByText(/ada admin/i)).toBeInTheDocument()

    const list = await machineList()
    const card = await list.findByRole('button', { name: /m1/ })
    await userEvent.click(card)

    expect(await screen.findByRole('heading', { name: /m1/ })).toBeInTheDocument()
    expect(await screen.findByText(/bearing_wear/)).toBeInTheDocument()
  })

  it('logs in from /login and lands on the originally requested page', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })))
    goTo('/alerts')
    render(<App />)

    await userEvent.type(await screen.findByLabelText(/email/i), 'admin@maintainiq.local')
    await userEvent.type(screen.getByLabelText(/password/i), 'whatever')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('heading', { name: /^alerts$/i })).toBeInTheDocument()
  })

  it('shows a login error on invalid credentials', async () => {
    server.use(
      http.get('/api/auth/me', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })),
      http.post('/api/auth/login', () => HttpResponse.json({ detail: 'invalid email or password' }, { status: 401 })),
    )
    goTo('/login')
    render(<App />)

    await userEvent.type(await screen.findByLabelText(/email/i), 'nope@maintainiq.local')
    await userEvent.type(screen.getByLabelText(/password/i), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid email or password/i)
  })

  it('logs out back to the login page', async () => {
    goTo('/dashboard')
    render(<App />)
    await screen.findByRole('region', { name: /kpi/i })

    await userEvent.click(screen.getByRole('button', { name: /log out/i }))

    expect(await screen.findByLabelText(/email/i)).toBeInTheDocument()
  })

  it('hides admin-only nav items for a supervisor', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(supervisorUser)))
    goTo('/dashboard')
    render(<App />)
    await screen.findByRole('region', { name: /kpi/i })

    const nav = within(screen.getByRole('navigation'))
    expect(nav.getByRole('link', { name: /notifications/i })).toBeInTheDocument()
    expect(nav.queryByRole('link', { name: /^users$/i })).not.toBeInTheDocument()
    expect(nav.queryByRole('link', { name: /demo control/i })).not.toBeInTheDocument()
  })

  it('redirects a supervisor away from an admin-only route', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(supervisorUser)))
    goTo('/admin/users')
    render(<App />)

    expect(await screen.findByRole('region', { name: /kpi/i })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^users$/i })).not.toBeInTheDocument()
  })

  it('hides notifications nav and route for an operator', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    goTo('/notifications')
    render(<App />)

    expect(await screen.findByRole('region', { name: /kpi/i })).toBeInTheDocument()
    const nav = within(screen.getByRole('navigation'))
    expect(nav.queryByRole('link', { name: /notifications/i })).not.toBeInTheDocument()
  })

  it('lets an operator open the Devices page from the nav', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    goTo('/dashboard')
    render(<App />)
    await screen.findByRole('region', { name: /kpi/i })

    const nav = within(screen.getByRole('navigation'))
    await userEvent.click(nav.getByRole('link', { name: /^devices$/i }))

    expect(await screen.findByRole('heading', { name: /sensor nodes/i })).toBeInTheDocument()
    expect(await screen.findByRole('table', { name: /telemetry devices/i })).toBeInTheDocument()
  })

  it.each([
    ['admin', undefined],
    ['supervisor', supervisorUser],
    ['operator', operatorUser],
  ])('lets a %s open Work orders from the nav', async (_role, user) => {
    if (user) server.use(http.get('/api/auth/me', () => HttpResponse.json(user)))
    goTo('/dashboard')
    render(<App />)
    await screen.findByRole('region', { name: /kpi/i })

    const nav = within(screen.getByRole('navigation'))
    await userEvent.click(nav.getByRole('link', { name: /^work orders$/i }))

    expect(await screen.findByRole('heading', { name: /^work orders$/i })).toBeInTheDocument()
    expect(await screen.findByRole('table', { name: /work orders/i })).toBeInTheDocument()
  })

  it('deep-links a work order into the drawer', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    goTo('/work-orders/3')
    render(<App />)

    expect(await screen.findByRole('dialog', { name: 'Work order #3' })).toBeInTheDocument()
  })

  it('keeps the Work orders filters when a row opens the drawer', async () => {
    goTo('/work-orders')
    render(<App />)
    const tabs = await screen.findByRole('tablist', { name: /work order status/i })
    await userEvent.click(within(tabs).getByRole('tab', { name: /^done$/i }))
    const table = within(await screen.findByRole('table', { name: /work orders/i }))

    await userEvent.click(await table.findByText('Grease m2 bearings'))

    expect(await screen.findByRole('dialog', { name: 'Work order #4' })).toBeInTheDocument()
    const tabsAfter = screen.getByRole('tablist', { name: /work order status/i })
    expect(within(tabsAfter).getByRole('tab', { name: /^done$/i })).toHaveAttribute('aria-selected', 'true')
  })

  it('deep-links an alert explanation into the drawer for any signed-in role', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    goTo('/alerts/2')
    render(<App />)

    expect(await screen.findByRole('dialog', { name: /why this alert\? — m1/i })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: /^alerts$/i })).toBeInTheDocument()
    // The Alerts nav item stays current on the drawer route.
    expect(screen.getByRole('link', { name: /^alerts$/i })).toHaveAttribute('aria-current', 'page')
  })

  it('sends an anonymous /alerts/:id visitor to login and back to the drawer after signing in', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })))
    goTo('/alerts/2')
    render(<App />)

    await userEvent.type(await screen.findByLabelText(/email/i), 'admin@maintainiq.local')
    await userEvent.type(screen.getByLabelText(/password/i), 'whatever')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('dialog', { name: /why this alert\? — m1/i })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/alerts/2')
  })

  it('opens the mobile operator view at /m on its alerts tab, without the desktop sidebar', async () => {
    goTo('/m')
    render(<App />)

    // The index route redirects (a Navigate), so wait for the alerts page.
    expect(await screen.findByRole('heading', { name: 'Alerts' })).toBeInTheDocument()
    const tabs = screen.getByRole('navigation', { name: 'Mobile' })
    expect(within(tabs).getByRole('link', { name: 'Alerts' })).toHaveAttribute('aria-current', 'page')
    expect(window.location.pathname).toBe('/m/alerts')
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('sends a signed-out QR deep link through login and back to the machine', async () => {
    let signedIn = false
    server.use(
      http.get('/api/auth/me', () =>
        signedIn ? HttpResponse.json(operatorUser) : HttpResponse.json({ detail: 'unauthorized' }, { status: 401 }),
      ),
      http.post('/api/auth/login', () => {
        signedIn = true
        return HttpResponse.json(operatorUser)
      }),
    )
    goTo('/m/machines/m1')
    render(<App />)

    await userEvent.type(await screen.findByLabelText(/email/i), 'operator@maintainiq.local')
    await userEvent.type(screen.getByLabelText(/password/i), 'whatever')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    const health = await screen.findByRole('region', { name: 'Machine health' })
    expect(within(health).getByRole('heading', { name: 'm1' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/m/machines/m1')
  })
})
