import {
  Activity,
  Bell,
  BellRing,
  ClipboardList,
  FileText,
  Gauge,
  LayoutGrid,
  LogOut,
  Menu,
  PlayCircle,
  Radar,
  RadioTower,
  ShieldAlert,
  Smartphone,
  Users,
  Waves,
  Wrench,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import type { LiveEventType, Role } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { Button } from '../components/ui/button'
import { MobileViewSuggestion } from '../components/MobileViewSuggestion'
import { useDialogFocus } from '../lib/useDialogFocus'
import { PushToggle } from '../push/PushToggle'

interface NavItem {
  to: string
  label: string
  icon: typeof Gauge
  roles?: Role[]
}

interface NavSection {
  title: string
  items: NavItem[]
}

/* Eleven flat links read as an undifferentiated list. Grouped by what you came
   to do — watch the fleet, interrogate it, act on it, run the place — the
   sidebar becomes scannable, and role-gated sections (Operations, Admin)
   disappear wholesale for users who can't reach any of their links. */
const NAV_SECTIONS: NavSection[] = [
  {
    title: 'Monitor',
    items: [
      { to: '/dashboard', label: 'Dashboard', icon: LayoutGrid },
      { to: '/machines', label: 'Machines', icon: Gauge },
      { to: '/alerts', label: 'Alerts', icon: ShieldAlert },
      { to: '/devices', label: 'Devices', icon: RadioTower },
    ],
  },
  {
    title: 'Analyze',
    items: [
      { to: '/analytics', label: 'Analytics', icon: Radar },
      { to: '/model', label: 'Model', icon: Activity },
      { to: '/reports', label: 'Reports', icon: FileText },
    ],
  },
  {
    title: 'Operations',
    items: [
      { to: '/work-orders', label: 'Work orders', icon: ClipboardList },
      { to: '/maintenance', label: 'Maintenance', icon: Wrench },
      { to: '/notifications', label: 'Notifications', icon: Bell, roles: ['admin', 'supervisor'] },
      { to: '/ingestion', label: 'Ingestion', icon: Waves, roles: ['admin', 'supervisor'] },
    ],
  },
  {
    title: 'Admin',
    items: [
      { to: '/admin/users', label: 'Users', icon: Users, roles: ['admin'] },
      { to: '/demo', label: 'Demo control', icon: PlayCircle, roles: ['admin'] },
    ],
  },
]

// Events that page someone (an email, and a push to subscribed devices, goes
// out): new and severity-escalated
// alerts, ladder re-pages and a sensor node going silent. Recoveries,
// acknowledgements, closures/recorded outcomes and work-order activity don't.
const PAGING_EVENT_TYPES: ReadonlySet<LiveEventType> = new Set<LiveEventType>([
  'alert_created',
  'alert_escalated',
  'alert_paged',
  'device_offline',
])

function NotificationBell() {
  const { lastEvent } = useLiveEvents()
  const location = useLocation()
  const [hasUnseen, setHasUnseen] = useState(false)

  // Only events that page someone light the dot (PAGING_EVENT_TYPES).
  useEffect(() => {
    if (lastEvent && PAGING_EVENT_TYPES.has(lastEvent.type)) setHasUnseen(true)
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

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-accent-2 via-accent to-accent shadow-[0_6px_20px_-6px_rgba(124,108,255,0.7)]">
        <Radar className="h-5 w-5 text-text" aria-hidden />
      </span>
      <div>
        <div className="text-lg font-bold leading-none tracking-tight text-text">
          Maintain<span className="text-accent text-glow">IQ</span>
        </div>
        <p className="mt-1 text-[11px] text-text-muted">Predictive maintenance</p>
      </div>
    </div>
  )
}

// One nav for the sidebar and the small-screen drawer, so the two cannot
// drift. `indicatorId` keeps their active-pill animations apart.
function NavList({ sections, label, indicatorId }: { sections: NavSection[]; label: string; indicatorId: string }) {
  const location = useLocation()
  return (
    <nav aria-label={label} className="flex flex-col gap-5 px-3 pb-4">
      {sections.map((section) => (
        <div key={section.title} className="flex flex-col gap-1">
          {/* Section headers are deliberately quiet — wide tracking, small
              caps, half-opacity. They should organize the list without
              competing with the links for attention. */}
          <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-text-muted/70">
            {section.title}
          </p>
          {section.items.map((item) => {
            const isActive = location.pathname.startsWith(item.to)
            const Icon = item.icon
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className="relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition"
              >
                {isActive && (
                  <motion.span
                    layoutId={indicatorId}
                    className="absolute inset-0 rounded-xl border border-accent/30 bg-gradient-to-r from-accent/15 to-transparent shadow-[inset_0_0_20px_-8px_rgba(124,108,255,0.6)]"
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
        </div>
      ))}
    </nav>
  )
}

// Below `lg` the sidebar is hidden and the same nav opens as an off-canvas
// drawer (design/2026-10-07-mobile-operator-pwa-design.md, decision 15). It
// closes on Escape (useDialogFocus), a backdrop tap or any navigation.
function NavDrawer({ sections, onClose }: { sections: NavSection[]; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(true, onClose, panelRef)
  return (
    <div className="fixed inset-0 z-40 bg-bg/60 backdrop-blur-sm lg:hidden" onClick={onClose}>
      <div
        ref={panelRef}
        id="app-nav-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Navigation"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="glass-panel m-3 flex h-[calc(100dvh-1.5rem)] w-64 flex-col overflow-y-auto rounded-2xl border focus:outline-none"
      >
        <div className="flex items-center justify-between py-6 pl-5 pr-3">
          <Brand />
          <button
            type="button"
            data-autofocus
            onClick={onClose}
            aria-label="Close navigation"
            className="flex h-11 w-11 items-center justify-center rounded-xl text-lg text-text-muted hover:text-text"
          >
            ×
          </button>
        </div>
        <NavList sections={sections} label="Main menu" indicatorId="drawer-active-indicator" />
      </div>
    </div>
  )
}

// "This device": the push opt-in for whichever browser this is. The desktop
// /notifications page is admin/supervisor-only, so the switch lives here for
// everyone. The toggle mounts only when opened, so the console makes no push
// calls until someone asks.
function DeviceMenu() {
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(open, () => setOpen(false), panelRef)

  // A tap anywhere else closes it, like any popover.
  useEffect(() => {
    if (!open) return
    function onPointerDown(e: PointerEvent) {
      if (!wrapperRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  return (
    <div ref={wrapperRef} className="relative">
      <button
        type="button"
        aria-label="This device"
        aria-expanded={open}
        aria-controls="device-menu"
        onClick={() => setOpen((o) => !o)}
        className="relative rounded-md p-2 text-text-muted transition hover:bg-white/10 hover:text-text"
      >
        <BellRing className="h-5 w-5" aria-hidden />
      </button>
      {open && (
        <div
          ref={panelRef}
          id="device-menu"
          tabIndex={-1}
          className="glass-panel absolute right-0 top-full z-30 mt-2 w-72 rounded-2xl border p-4 focus:outline-none"
        >
          <PushToggle compact />
        </div>
      )}
    </div>
  )
}

// The key the page transition remounts on. /work-orders/:id only opens a
// drawer over the list, so it shares the list's key: remounting there would
// replay the entrance and drop the page's filters on every row click.
function transitionKey(pathname: string): string {
  return pathname.startsWith('/work-orders') ? '/work-orders' : pathname
}

export function AppShell() {
  const { user, logout } = useAuth()
  const { connected } = useLiveEvents()
  const location = useLocation()
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Any navigation (a drawer link included) closes the drawer.
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  // RequireAuth always mounts this shell after `user` resolves; this null
  // check exists only to satisfy the type checker below.
  if (!user) return null

  // Filter inside each section, then drop sections that ended up empty — an
  // "Admin" header with nothing under it just advertises what you can't do.
  const sections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => !item.roles || item.roles.includes(user.role)),
  })).filter((section) => section.items.length > 0)

  const canSeeNotifications = sections.some((s) => s.items.some((item) => item.to === '/notifications'))

  return (
    <div className="flex min-h-screen bg-aurora text-text">
      <aside className="glass-panel m-3 mr-0 hidden w-60 shrink-0 flex-col rounded-2xl border lg:flex">
        <div className="px-5 py-6">
          <Brand />
        </div>
        <NavList sections={sections} label="Main" indicatorId="nav-active-indicator" />
      </aside>
      {drawerOpen && <NavDrawer sections={sections} onClose={() => setDrawerOpen(false)} />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="glass-panel sticky top-0 z-20 mx-3 mt-3 flex items-center justify-between gap-2 rounded-2xl border px-3 py-3 sm:px-5">
          {/* min-w-0 + shrink-0 and sr-only labels below `sm`: at ~375 px the
              'Reconnecting…' label and the 'Log out' text used to push the row
              past the viewport (sideways scroll, clipped Log out button). The
              dot still shows the state; the text stays for screen readers. */}
          <div className="flex min-w-0 items-center gap-2 text-xs text-text-muted">
            <button
              type="button"
              aria-label="Open navigation"
              aria-expanded={drawerOpen}
              aria-controls="app-nav-drawer"
              onClick={() => setDrawerOpen(true)}
              className="flex h-11 w-11 items-center justify-center rounded-xl text-text-muted transition hover:bg-white/10 hover:text-text lg:hidden"
            >
              <Menu className="h-5 w-5" aria-hidden />
            </button>
            <span className="relative flex h-2 w-2">
              {connected && (
                <motion.span
                  className="absolute inline-flex h-full w-full rounded-full bg-accent-2"
                  animate={{ scale: [1, 2.2], opacity: [0.7, 0] }}
                  transition={{ duration: 1.6, repeat: Infinity, ease: 'easeOut' }}
                />
              )}
              {/* Live is azure and pulsing; reconnecting is a dim, still dot.
                  The presence or absence of motion carries the distinction, so
                  it survives greyscale — the adjacent text states it outright. */}
              <span
                aria-hidden
                className={`relative inline-flex h-2 w-2 rounded-full ${connected ? 'bg-accent-2' : 'bg-unknown'}`}
              />
            </span>
            <span data-testid="connection-label" className="sr-only whitespace-nowrap font-mono sm:not-sr-only">
              {connected ? 'Live' : 'Reconnecting…'}
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-1.5 sm:gap-3">
            {/* The operator view, for every role (decision 15). */}
            <Link
              to="/m"
              aria-label="Mobile view"
              className="flex items-center gap-1.5 rounded-md p-2 text-xs text-text-muted transition hover:bg-white/10 hover:text-text"
            >
              <Smartphone className="h-5 w-5" aria-hidden />
              <span className="hidden md:inline">Mobile view</span>
            </Link>
            <DeviceMenu />
            {canSeeNotifications && <NotificationBell />}
            <div className="hidden text-right sm:block">
              <div className="text-sm font-medium">{user.name}</div>
              <div className="text-xs capitalize text-text-muted">{user.role}</div>
            </div>
            <Button variant="outline" size="sm" onClick={() => void logout()}>
              <LogOut className="h-3.5 w-3.5" aria-hidden />
              <span data-testid="logout-label" className="sr-only whitespace-nowrap sm:not-sr-only">Log out</span>
            </Button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto px-3 py-6 sm:px-6">
          <MobileViewSuggestion />
          {/* Keyed on the pathname (transitionKey) so React remounts on
              navigation and the CSS entrance replays. Doing it here means every
              route gets the same transition without each page opting in
              individually. */}
          <div key={transitionKey(location.pathname)} className="page-enter">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
