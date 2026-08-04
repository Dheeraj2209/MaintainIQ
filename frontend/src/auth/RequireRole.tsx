import { Navigate, Outlet } from 'react-router-dom'
import type { Role } from '../api/types'
import { useAuth } from './AuthContext'

// Nested under RequireAuth in the route tree, so `user` is already known to
// be non-null here — bounce to the dashboard rather than /login if the role
// just doesn't have access to this section.
export function RequireRole({ allow }: { allow: Role[] }) {
  const { user } = useAuth()
  if (!user || !allow.includes(user.role)) {
    return <Navigate to="/" replace />
  }
  return <Outlet />
}
