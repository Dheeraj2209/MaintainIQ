// The operator view's shell (design/2026-10-07-mobile-operator-pwa-design.md,
// "MobileShell"): one column, a compact header with the live indicator, and a
// bottom tab bar within thumb reach. No sidebar, no page-enter animation and
// no spotlight effects: motion costs battery and latency on cheap phones.
import { Gauge, Radar, ScanLine, Settings, ShieldAlert } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { cn } from '../lib/cn'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

const TABS = [
  { to: '/m/alerts', label: 'Alerts', icon: ShieldAlert },
  { to: '/m/scan', label: 'Scan', icon: ScanLine },
  { to: '/m/machines', label: 'Machines', icon: Gauge },
  { to: '/m/settings', label: 'Settings', icon: Settings },
]

export function MobileShell() {
  const { connected } = useLiveEvents()

  return (
    <div className="flex min-h-dvh flex-col bg-aurora text-text">
      <header className="glass-panel sticky top-0 z-20 flex items-center justify-between border-b px-4 pb-2.5 pt-[calc(0.625rem+env(safe-area-inset-top))]">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-accent-2 via-accent to-accent">
            <Radar className="h-4 w-4 text-text" aria-hidden />
          </span>
          <span className="text-base font-bold tracking-tight">
            Maintain<span className="text-accent">IQ</span>
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-text-muted">
          {/* Same distinction as the desktop header, without the pulse:
              colour plus the word, never colour alone. */}
          <span aria-hidden className={cn('h-2 w-2 rounded-full', connected ? 'bg-accent-2' : 'bg-unknown')} />
          <span className="font-mono">{connected ? 'Live' : 'Reconnecting…'}</span>
        </div>
      </header>

      <main className="flex-1 px-3 pb-[calc(4.5rem+env(safe-area-inset-bottom))] pt-3">
        <Outlet />
      </main>

      <nav
        aria-label="Mobile"
        className="glass-panel safe-bottom fixed inset-x-0 bottom-0 z-30 grid grid-cols-4 border-t"
      >
        {TABS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                'flex min-h-14 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors',
                isActive ? 'text-accent' : 'text-text-muted hover:text-text',
              )
            }
          >
            <Icon className="h-5 w-5" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
