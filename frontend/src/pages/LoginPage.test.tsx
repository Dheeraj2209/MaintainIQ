import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse, delay } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { LoginPage } from './LoginPage'
import { server } from '../test/server'

function harness(initialEntry: string | { pathname: string; state?: unknown } = '/login') {
  return (
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>dashboard placeholder</div>} />
          <Route path="/alerts" element={<div>alerts placeholder</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('LoginPage', () => {
  it('renders the branded sign-in form when logged out', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })))
    render(harness())

    expect(await screen.findByRole('heading', { name: /maintainiq/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('disables the submit button and shows a busy label while signing in', async () => {
    server.use(
      http.get('/api/auth/me', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })),
      http.post('/api/auth/login', async () => {
        await delay(50)
        return HttpResponse.json({ detail: 'invalid email or password' }, { status: 401 })
      }),
    )
    render(harness())
    await userEvent.type(await screen.findByLabelText(/email/i), 'admin@maintainiq.local')
    await userEvent.type(screen.getByLabelText(/password/i), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(screen.getByRole('button', { name: /signing in/i })).toBeDisabled()
    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid email or password/i)
  })

  it('redirects an already-authenticated visitor straight to the dashboard', async () => {
    render(harness())
    expect(await screen.findByText('dashboard placeholder')).toBeInTheDocument()
  })

  it('redirects an already-authenticated visitor to the originally requested page', async () => {
    render(harness({ pathname: '/login', state: { from: { pathname: '/alerts' } } }))
    expect(await screen.findByText('alerts placeholder')).toBeInTheDocument()
  })
})
