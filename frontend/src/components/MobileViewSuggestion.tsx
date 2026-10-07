// "On a phone?" banner for the desktop console (design/2026-10-07-mobile-operator-pwa-design.md,
// decision 15). Shown on narrow viewports outside /m until dismissed, and
// never a redirect: someone who wants the full console on a phone keeps it.
import { Smartphone } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

export const MOBILE_SUGGEST_DISMISSED_KEY = 'miq:mobile-suggest-dismissed'
const NARROW_QUERY = '(max-width: 640px)'

function readDismissed(): boolean {
  try {
    return localStorage.getItem(MOBILE_SUGGEST_DISMISSED_KEY) === '1'
  } catch {
    return false // storage blocked (private mode): just show it
  }
}

function useNarrowViewport(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia?.(NARROW_QUERY).matches ?? false)
  useEffect(() => {
    const query = window.matchMedia?.(NARROW_QUERY)
    if (!query) return
    const onChange = () => setNarrow(query.matches)
    onChange()
    query.addEventListener?.('change', onChange)
    return () => query.removeEventListener?.('change', onChange)
  }, [])
  return narrow
}

export function MobileViewSuggestion() {
  const { pathname } = useLocation()
  const narrow = useNarrowViewport()
  const [dismissed, setDismissed] = useState(readDismissed)

  if (!narrow || dismissed || pathname === '/m' || pathname.startsWith('/m/')) return null

  function dismiss() {
    setDismissed(true)
    try {
      localStorage.setItem(MOBILE_SUGGEST_DISMISSED_KEY, '1')
    } catch {
      // Not persisted; it stays hidden for this visit.
    }
  }

  return (
    <div
      role="region"
      aria-label="Mobile view suggestion"
      className="mb-4 flex flex-wrap items-center gap-3 rounded-2xl border border-accent/30 bg-accent/10 p-3 text-sm text-text"
    >
      <Smartphone className="h-4 w-4 shrink-0 text-accent" aria-hidden />
      <p className="min-w-0 flex-1">On a phone? The operator view is built for it.</p>
      <div className="flex gap-2">
        <Link
          to="/m"
          className="inline-flex min-h-11 items-center rounded-xl border border-accent/50 bg-accent/20 px-3 font-medium"
        >
          Open mobile view
        </Link>
        <button
          type="button"
          onClick={dismiss}
          className="inline-flex min-h-11 items-center rounded-xl border border-white/10 px-3 text-text-muted hover:text-text"
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
