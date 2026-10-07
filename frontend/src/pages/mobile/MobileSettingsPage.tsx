// /m/settings (design/2026-10-07-mobile-operator-pwa-design.md, "Pages"): who
// is signed in, the per-device push opt-in, the way back to the desktop
// console, and log out (which unsubscribes this device first).
import { LogOut, Monitor } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { PushToggle } from '../../push/PushToggle'

export function MobileSettingsPage() {
  const { user, logout } = useAuth()
  if (!user) return null

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold text-text">Settings</h1>

      <section aria-label="Signed in as" className="glass space-y-0.5 rounded-2xl p-4">
        <p className="text-base font-semibold text-text">{user.name}</p>
        <p className="text-sm text-text-muted">{user.email}</p>
        <p className="text-xs capitalize text-text-muted">{user.role}</p>
      </section>

      <section aria-label="Notifications" className="glass rounded-2xl p-4">
        <PushToggle />
      </section>

      <div className="glass divide-y divide-white/10 overflow-hidden rounded-2xl">
        <Link to="/dashboard" className="flex min-h-14 items-center gap-3 px-4 text-sm text-text hover:bg-white/5">
          <Monitor className="h-4 w-4 text-text-muted" aria-hidden />
          Desktop view
        </Link>
        <button
          type="button"
          onClick={() => void logout()}
          className="flex min-h-14 w-full items-center gap-3 px-4 text-left text-sm text-text hover:bg-white/5"
        >
          <LogOut className="h-4 w-4 text-text-muted" aria-hidden />
          Log out
        </button>
      </div>
    </div>
  )
}
