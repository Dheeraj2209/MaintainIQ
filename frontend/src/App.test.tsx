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
  it('redirects an unauthenticated visitor to /login', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })))
    render(<App />)
    expect(await screen.findByRole('heading', { name: /maintainiq/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
  })

  it('renders the dashboard for an authenticated admin and opens machine detail', async () => {
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
    render(<App />)

    await userEvent.type(await screen.findByLabelText(/email/i), 'nope@maintainiq.local')
    await userEvent.type(screen.getByLabelText(/password/i), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid email or password/i)
  })

  it('logs out back to the login page', async () => {
    render(<App />)
    await screen.findByRole('region', { name: /kpi/i })

    await userEvent.click(screen.getByRole('button', { name: /log out/i }))

    expect(await screen.findByLabelText(/email/i)).toBeInTheDocument()
  })

  it('hides admin-only nav items for a supervisor', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(supervisorUser)))
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
})
