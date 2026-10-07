// The health ratchet as operators see it
// (docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md, Task 12).
// health_state can be a held level above the current reading's own state, and
// after a reset the model relearns its baseline (commissioning) before it may
// alert again. Kept out of component files so those only export components.
import type { MachineSummary } from '../api/types'

type HealthView = Pick<
  MachineSummary,
  'health_state' | 'health_state_held' | 'instant_health_state' | 'commissioning' | 'reset_pending_reading'
>

function label(state: string | null | undefined): string {
  if (!state) return 'Unknown'
  return state.charAt(0).toUpperCase() + state.slice(1)
}

// Held, and not commissioning: a commissioning reading's state is forced
// healthy, which is not a hold.
export function isHeld(health: HealthView): boolean {
  return !!health.health_state_held && !health.commissioning
}

// "Held: Critical (current signal: Healthy)", shown in place of the RUL
// estimate while held, or null when not held.
export function heldText(health: HealthView): string | null {
  if (!isHeld(health)) return null
  return `Held: ${label(health.health_state)} (current signal: ${label(health.instant_health_state)})`
}

// "Commissioning 7/20", or null when not commissioning.
export function commissioningText(health: HealthView): string | null {
  const c = health.commissioning
  return c ? `Commissioning ${c.seen}/${c.of}` : null
}

// "Reset, awaiting reading" after a reset with no reading since (the held
// level is healthy but nothing has been measured yet), or null.
export function resetPendingText(health: HealthView): string | null {
  return health.reset_pending_reading && !health.commissioning ? 'Reset, awaiting reading' : null
}

// The state to colour a machine by: neutral ('unknown') while it relearns its
// baseline after a reset, not the forced-healthy green.
export function toneState(health: HealthView): string {
  return health.commissioning || health.reset_pending_reading ? 'unknown' : health.health_state
}

// The current reading's state while commissioning, when it is not healthy.
export function commissioningSignal(health: HealthView): string | null {
  const instant = health.instant_health_state
  if (!health.commissioning || !instant || instant === 'healthy') return null
  return `Signal: ${label(instant)}`
}
