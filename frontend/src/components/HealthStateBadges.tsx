// The machine's health badge with the ratchet made visible (plan Task 12):
// "Commissioning k/20" in a neutral colour (not green) while the baseline is
// relearned, with the current signal beside it if it is not healthy; "Reset,
// awaiting reading" (also neutral) after a reset with no reading since;
// otherwise the state, plus "Held since …" when that state is a held level.
import type { MachineSummary } from '../api/types'
import { commissioningSignal, commissioningText, isHeld, resetPendingText } from '../lib/healthHold'
import { healthLabel } from './healthStyles'
import { Badge } from './ui/badge'

type Health = Pick<
  MachineSummary,
  | 'health_state'
  | 'health_state_held'
  | 'instant_health_state'
  | 'commissioning'
  | 'held_since'
  | 'reset_pending_reading'
>

export function HealthStateBadges({ health }: { health: Health }) {
  const commissioning = commissioningText(health)
  if (commissioning) {
    const signal = commissioningSignal(health)
    return (
      <span className="inline-flex flex-wrap items-center gap-1">
        <Badge variant="neutral" title="Relearning the baseline after a health reset; not alerting yet">
          {commissioning}
        </Badge>
        {signal && (
          <Badge variant={health.instant_health_state as 'degrading' | 'faulty' | 'critical'}>{signal}</Badge>
        )}
      </span>
    )
  }
  const pending = resetPendingText(health)
  if (pending) {
    return (
      <Badge variant="neutral" title="Health tracking was reset; the baseline is relearned from the next readings">
        {pending}
      </Badge>
    )
  }
  const state = (health.health_state || 'unknown') as 'healthy' | 'degrading' | 'faulty' | 'critical' | 'unknown'
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Badge variant={state}>{healthLabel(health.health_state)}</Badge>
      {isHeld(health) && <HeldBadge since={health.held_since ?? null} />}
    </span>
  )
}

function HeldBadge({ since }: { since: string | null }) {
  const t = since ? new Date(since) : null
  const when = t && !Number.isNaN(t.getTime()) ? t.toLocaleString() : null
  return (
    <Badge variant="neutral" title="Held until a repair is recorded or the alert is closed as a false alarm">
      {when ? `Held since ${when}` : 'Held'}
    </Badge>
  )
}
