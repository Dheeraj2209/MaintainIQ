import { Activity, Bell, FileText, Gauge, LayoutGrid, LogOut, PlayCircle, Radar, ShieldAlert, Users, Waves, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import type { Role } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { Button } from '../components/ui/button'

interface NavItem {
  to: string
  label: string
  icon: typeof Gauge
  roles?: Role[]
}

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutGrid },
  { to: '/machines', label: 'Machines', icon: Gauge },
  { to: '/alerts', label: 'Alerts', icon: ShieldAlert },
  { to: '/analytics', label: 'Analytics', icon: Radar },
  { to: '/model', label: 'Model', icon: Activity },
  { to: '/reports', label: 'Reports', icon: FileText },
  { to: '/maintenance', label: 'Maintenance', icon: Wrench },
  { to: '/notifications', label: 'Notifications', icon: Bell, roles: ['admin', 'supervisor'] },
  { to: '/ingestion', label: 'Ingestion', icon: Waves, roles: ['admin', 'supervisor'] },
  { to: '/admin/users', label: 'Users', icon: Users, roles: ['admin'] },
  { to: '/demo', label: 'Demo control', icon: PlayCircle, roles: ['admin'] },
]

function NotificationBell() {
  const { lastEvent } = useLiveEvents()
  const location = useLocation()
  const [hasUnseen, setHasUnseen] = useState(false)

  useEffect(() => {
    if (lastEvent && lastEvent.type !== 'alert_resolved') setHasUnseen(true)
  }, [lastEvent])

  useEffect(() => {
    if (location.pathname === '/notifications') setHasUnseen(false)
  }, [location.pathname])

  return (
    <NavLink
      to="/notifications"
      aria-label="Notifications"
      className="relative rounded-md p-2 text-text-muted transition hover:bg-white/10 hover:text-text"
    >
      <Bell className="h-5 w-5" aria-hidden />
      {hasUnseen && (
        <motion.span
          aria-hidden
          className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-accent-2"
          initial={{ scale: 0.6, opacity: 0 }}
          animate={{ scale: [1, 1.4, 1], opacity: 1 }}
          transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
    </NavLink>
  )
}

export function AppShell() {
  const { user, logout } = useAuth()
  const { connected } = useLiveEvents()
  const location = useLocation()

  // RequireAuth always mounts this shell after `user` resolves; this null
  // check exists only to satisfy the type checker below.
  if (!user) return null

  const items = NAV.filter((item) => !item.roles || item.roles.includes(user.role))
  const canSeeNotifications = items.some((item) => item.to === '/notifications')

  return (
    <div className="flex min-h-screen bg-aurora text-text">
      <aside className="glass-panel m-3 mr-0 flex w-60 shrink-0 flex-col rounded-2xl border">
        <div className="px-5 py-6">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-accent to-red-800 shadow-[0_6px_20px_-6px_rgba(226,58,58,0.7)]">
              <Radar className="h-5 w-5 text-text" aria-hidden />
            </span>
            <div>
              <div className="text-lg font-bold leading-none tracking-tight text-text">
                Maintain<span className="text-accent text-glow">IQ</span>
              </div>
              <p className="mt-1 text-[11px] text-text-muted">Predictive maintenance</p>
            </div>
          </div>
        </div>
        <nav aria-label="Main" className="flex flex-col gap-1 px-3">
          {items.map((item) => {
            const isActive =
              item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to)
            const Icon = item.icon
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className="relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition"
              >
                {isActive && (
                  <motion.span
                    layoutId="nav-active-indicator"
                    className="absolute inset-0 rounded-xl border border-accent/30 bg-gradient-to-r from-accent/15 to-transparent shadow-[inset_0_0_20px_-8px_rgba(226,58,58,0.6)]"
                    transition={{ type: 'spring', stiffness: 400, damping: 32 }}
                  />
                )}
                <Icon
                  className={`relative h-4 w-4 shrink-0 transition-colors ${isActive ? 'text-accent' : 'text-text-muted'}`}
                  aria-hidden
                />
                <span className={`relative transition-colors ${isActive ? 'text-text' : 'text-text-muted'}`}>
                  {item.label}
                </span>
              </NavLink>
            )
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="glass-panel sticky top-0 z-20 mx-3 mt-3 flex items-center justify-between rounded-2xl border px-5 py-3">
          <div className="flex items-center gap-2 text-xs text-text-muted">
            <span className="relative flex h-2 w-2">
              {connected && (
                <motion.span
                  className="absolute inline-flex h-full w-full rounded-full bg-healthy"
                  animate={{ scale: [1, 2.2], opacity: [0.7, 0] }}
                  transition={{ duration: 1.6, repeat: Infinity, ease: 'easeOut' }}
                />
              )}
              <span
                aria-hidden
                className={`relative inline-flex h-2 w-2 rounded-full ${connected ? 'bg-healthy' : 'bg-accent-2'}`}
              />
            </span>
            <span className="font-mono">{connected ? 'Live' : 'Reconnecting…'}</span>
          </div>

          <div className="flex items-center gap-3">
            {canSeeNotifications && <NotificationBell />}
            <div className="text-right">
              <div className="text-sm font-medium">{user.name}</div>
              <div className="text-xs capitalize text-text-muted">{user.role}</div>
            </div>
            <Button variant="outline" size="sm" onClick={() => void logout()}>
              <LogOut className="h-3.5 w-3.5" aria-hidden />
              Log out
            </Button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
