import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, api } from '../api/client'
import type { UserOut } from '../api/types'
import { unsubscribeThisDevice } from '../push/pushSupport'

interface AuthState {
  user: UserOut | null
  loading: boolean
  // The bootstrap /auth/me never reached the server (no network). Not the
  // same as signed out: RequireAuth shows "You're offline", not the login.
  offline: boolean
  retry: () => void
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [offline, setOffline] = useState(false)
  // Bumped to re-run the bootstrap (Retry, or the browser coming back online).
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  // Session lives in an httpOnly cookie we can't read from JS, so the only
  // way to know "am I logged in" on first load is to ask the backend.
  // fetch() rejecting outright (not an ApiError) means the request never got
  // an answer: the installed app opened with no signal. That keeps the user
  // unknown rather than signed out.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .me()
      .then((u) => {
        if (cancelled) return
        setUser(u)
        setOffline(false)
      })
      .catch((e) => {
        if (cancelled) return
        setUser(null)
        setOffline(!(e instanceof ApiError))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [attempt])

  useEffect(() => {
    if (!offline) return
    window.addEventListener('online', retry)
    return () => window.removeEventListener('online', retry)
  }, [offline, retry])

  // api/client.ts dispatches this on any 401 response, so a session that
  // expires or is revoked mid-use flips the whole app to logged-out.
  useEffect(() => {
    const onUnauthorized = () => setUser(null)
    window.addEventListener('miq:unauthorized', onUnauthorized)
    return () => window.removeEventListener('miq:unauthorized', onUnauthorized)
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const u = await api.login(email, password)
    setUser(u)
    setOffline(false)
  }, [])

  // An explicit logout first stops push to this device, so a shared phone
  // doesn't keep paging the user who just left. Best-effort and capped at
  // 2 s (unsubscribeThisDevice); it never blocks signing out. A session that
  // merely expires keeps its subscription on purpose (operators stay paged).
  const logout = useCallback(async () => {
    await unsubscribeThisDevice()
    await api.logout()
    setUser(null)
  }, [])

  return <AuthContext.Provider value={{ user, loading, offline, retry, login, logout }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
