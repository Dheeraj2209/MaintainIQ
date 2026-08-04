import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider, useAuth } from './AuthContext'
import { RequireAuth } from './RequireAuth'
import { RequireRole } from './RequireRole'
import { server } from '../test/server'
import { operatorUser } from '../test/fixtures'

function Probe() {
  const { user, loading } = useAuth()
  return <div>{loading ? 'loading' : user ? `user:${user.role}` : 'anonymous'}</div>
}

function LoginProbe() {
  const { user, login, logout } = useAuth()
  return (
    <div>
      <div>{user ? `user:${user.email}` : 'anonymous'}</div>
      <button onClick={() => void login('admin@maintainiq.local', 'secret')}>do-login</button>
      <button onClick={() => void logout()}>do-logout</button>
    </div>
  )
}

describe('AuthProvider', () => {
  it('starts loading then resolves the current user from /api/auth/me', async () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>,
    )
    expect(screen.getByText('loading')).toBeInTheDocument()
    expect(await screen.findByText('user:admin')).toBeInTheDocument()
  })

  it('resolves to anonymous when /api/auth/me is unauthorized', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'nope' }, { status: 401 })))
    render(
      <MemoryRouter>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByText('anonymous')).toBeInTheDocument()
  })

  it('clears the user when a miq:unauthorized event fires', async () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>,
    )
    await screen.findByText('user:admin')
    window.dispatchEvent(new Event('miq:unauthorized'))
    expect(await screen.findByText('anonymous')).toBeInTheDocument()
  })

  it('login() and logout() update the current user', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'nope' }, { status: 401 })))
    render(
      <MemoryRouter>
        <AuthProvider>
          <LoginProbe />
        </AuthProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByText('anonymous')).toBeInTheDocument()

    await userEvent.click(screen.getByText('do-login'))
    expect(await screen.findByText(/user:admin@maintainiq\.local/)).toBeInTheDocument()

    await userEvent.click(screen.getByText('do-logout'))
    expect(await screen.findByText('anonymous')).toBeInTheDocument()
  })
})

describe('RequireAuth', () => {
  function harness(initialPath: string) {
    return (
      <MemoryRouter initialEntries={[initialPath]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<div>login page</div>} />
            <Route element={<RequireAuth />}>
              <Route path="/" element={<div>dashboard</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    )
  }

  it('shows a loading state before auth resolves', () => {
    render(harness('/'))
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('renders the protected route once authenticated', async () => {
    render(harness('/'))
    expect(await screen.findByText('dashboard')).toBeInTheDocument()
  })

  it('redirects to /login when unauthenticated', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json({ detail: 'nope' }, { status: 401 })))
    render(harness('/'))
    expect(await screen.findByText('login page')).toBeInTheDocument()
  })
})

describe('RequireRole', () => {
  // RequireRole has no loading guard of its own — it must sit inside
  // RequireAuth so it only ever evaluates against a resolved user.
  function harness(initialPath: string) {
    return (
      <MemoryRouter initialEntries={[initialPath]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<div>login page</div>} />
            <Route element={<RequireAuth />}>
              <Route path="/" element={<div>dashboard</div>} />
              <Route element={<RequireRole allow={['admin']} />}>
                <Route path="/admin" element={<div>admin only</div>} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    )
  }

  it('renders the nested route when the role is allowed', async () => {
    render(harness('/admin'))
    expect(await screen.findByText('admin only')).toBeInTheDocument()
  })

  it('redirects to / when the role is not allowed', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    render(harness('/admin'))
    expect(await screen.findByText('dashboard')).toBeInTheDocument()
    expect(screen.queryByText('admin only')).not.toBeInTheDocument()
  })
})
