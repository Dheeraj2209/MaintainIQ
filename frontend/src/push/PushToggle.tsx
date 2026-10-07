// "Push notifications on this device" switch (design/2026-10-07-mobile-operator-pwa-design.md,
// decision 18). Used on /m/settings and in the desktop header's "This device"
// menu. Every state that blocks push says why in plain words, and the switch
// stays disabled rather than failing on tap.
import { useId } from 'react'
import { toast } from 'sonner'
import { cn } from '../lib/cn'
import type { PushState } from './usePush'
import { usePush } from './usePush'

const STATUS: Record<PushState, string> = {
  loading: 'Checking this device…',
  unsupported: "This browser can't receive push notifications.",
  insecure: "Push needs HTTPS — see README 'Mobile & push'.",
  unavailable: 'Push needs the installed app (a production build with its service worker).',
  'server-disabled': "Push isn't configured on this server.",
  denied: "Notifications are blocked in this browser's site settings.",
  off: 'Off. Turn on to be paged on this device.',
  on: 'On. Pages for your role reach this device.',
  busy: 'Updating…',
}

function devices(n: number): string {
  return `${n} device${n === 1 ? '' : 's'}`
}

export function PushToggle({ compact = false }: { compact?: boolean }) {
  const { state, error, enable, disable, sendTest } = usePush()
  const id = useId()
  const checked = state === 'on'
  const usable = state === 'on' || state === 'off'

  async function handleTest() {
    const result = await sendTest()
    if (!result) return
    if (result.sent > 0) toast.success(`Test notification sent to ${devices(result.sent)}`)
    else toast.error(`Test notification failed on ${devices(result.failed + result.expired)}`)
  }

  return (
    <div className={cn('space-y-2', compact ? 'text-xs' : 'text-sm')}>
      <div className="flex items-center justify-between gap-3">
        <span id={`${id}-label`} className="font-medium text-text">
          Push notifications on this device
        </span>
        {/* A 44 px hit area around the visual 28 px track. */}
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          aria-labelledby={`${id}-label`}
          aria-describedby={`${id}-status`}
          disabled={!usable}
          onClick={() => void (checked ? disable() : enable())}
          className="inline-flex h-11 min-w-14 shrink-0 items-center justify-center disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span
            aria-hidden
            className={cn(
              'relative inline-flex h-7 w-12 items-center rounded-full border transition-colors',
              checked ? 'border-accent/60 bg-accent/40' : 'border-white/15 bg-white/5',
            )}
          >
            <span
              className={cn(
                'inline-block h-5 w-5 rounded-full bg-text shadow transition-transform',
                checked ? 'translate-x-6' : 'translate-x-1',
              )}
            />
          </span>
        </button>
      </div>
      <p id={`${id}-status`} className="text-text-muted">
        {STATUS[state]}
      </p>
      {error && (
        <p role="alert" className="text-critical">
          {error}
        </p>
      )}
      {checked && (
        <button
          type="button"
          onClick={() => void handleTest()}
          className="min-h-11 rounded-xl border border-white/10 bg-white/5 px-4 text-sm text-text transition hover:bg-white/10"
        >
          Send test
        </button>
      )}
    </div>
  )
}
