import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider, useAuth } from './AuthContext'
import { RequireAuth } from './RequireAuth'
import { RequireRole } from './RequireRole'
import { server } from '../test/server'
import { operatorUser } from '../test/fixtures'
import { DEFAULT_ENDPOINT, fakeSubscription, installPwaStubs } from '../test/pwaStubs'

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

  function renderSignedIn() {
    render(
      <MemoryRouter>
        <AuthProvider>
          <LoginProbe />
        </AuthProvider>
      </MemoryRouter>,
    )
    return screen.findByText(/user:admin@maintainiq\.local/)
  }

  it('logout() removes this device push subscription before signing out', async () => {
    const sub = fakeSubscription()
    installPwaStubs({ subscription: sub })
    const order: string[] = []
    server.use(
      http.delete('/api/push/subscribe', async ({ request }) => {
        order.push(`DELETE ${((await request.json()) as { endpoint: string }).endpoint}`)
        return new HttpResponse(null, { status: 204 })
      }),
      http.post('/api/auth/logout', () => {
        order.push('logout')
        return HttpResponse.json({ status: 'ok' })
      }),
    )
    await renderSignedIn()

    await userEvent.click(screen.getByText('do-logout'))

    expect(await screen.findByText('anonymous')).toBeInTheDocument()
    expect(order).toEqual([`DELETE ${DEFAULT_ENDPOINT}`, 'logout'])
    expect(sub.unsubscribe).toHaveBeenCalled()
  })

  it('logout() still signs out when the unsubscribe fails', async () => {
    installPwaStubs({ subscription: fakeSubscription() })
    server.use(http.delete('/api/push/subscribe', () => HttpResponse.json({ detail: 'down' }, { status: 500 })))
    await renderSignedIn()

    await userEvent.click(screen.getByText('do-logout'))

    expect(await screen.findByText('anonymous')).toBeInTheDocument()
  })

  it('logout() gives up on a hung unsubscribe after 2 s', async () => {
    installPwaStubs({ subscription: fakeSubscription() })
    server.use(http.delete('/api/push/subscribe', () => new Promise<Response>(() => {})))
    await renderSignedIn()

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      fireEvent.click(screen.getByText('do-logout'))
      await vi.advanceTimersByTimeAsync(1900)
      expect(screen.queryByText('anonymous')).not.toBeInTheDocument()
      await vi.advanceTimersByTimeAsync(200)
    } finally {
      vi.useRealTimers()
    }
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

  it('shows a "You are offline" screen instead of the login form when the network is down', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.error()))
    render(harness('/'))
    expect(await screen.findByText(/you're offline/i)).toBeInTheDocument()
    expect(screen.queryByText('login page')).not.toBeInTheDocument()

    // Back online: Retry signs straight back in, at the same route.
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    await userEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(await screen.findByText('dashboard')).toBeInTheDocument()
  })

  it('retries by itself when the browser comes back online', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.error()))
    render(harness('/'))
    expect(await screen.findByText(/you're offline/i)).toBeInTheDocument()
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    fireEvent(window, new Event('online'))
    expect(await screen.findByText('dashboard')).toBeInTheDocument()
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
              <Route path="/dashboard" element={<div>dashboard</div>} />
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

  it('redirects to the dashboard when the role is not allowed', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    render(harness('/admin'))
    expect(await screen.findByText('dashboard')).toBeInTheDocument()
    expect(screen.queryByText('admin only')).not.toBeInTheDocument()
  })
})
