import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'

export function RequireAuth() {
  const { user, loading, offline, retry } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg text-sm text-text-muted">
        Loading…
      </div>
    )
  }

  // Opened with no network (the cached PWA shell): say so instead of sending
  // the user to a login form that cannot work either.
  if (!user && offline) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-bg p-6 text-center">
        <p role="status" className="text-lg font-semibold text-text">
          You're offline
        </p>
        <p className="max-w-xs text-sm text-text-muted">
          MaintainIQ needs a connection to load alerts. It will reconnect when your signal returns.
        </p>
        <button
          type="button"
          onClick={retry}
          className="min-h-11 rounded-xl border border-white/10 bg-white/5 px-5 text-sm font-medium text-text"
        >
          Retry
        </button>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <Outlet />
}
