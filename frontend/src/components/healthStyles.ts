// Central mapping from a health/severity state to Tailwind utility classes and
// a display label. Pure functions so they're trivially unit-testable and the
// color coding stays consistent across every component.

interface StateClasses {
  badge: string // soft "glass pill" status chip
  border: string // left-border accent for cards
  text: string
  dot: string // solid signal dot for compact indicators
  glow: string // box-shadow ring tuned to the signal color (for hover/selected)
}

const MAP: Record<string, StateClasses> = {
  healthy: {
    badge: 'bg-healthy/15 text-healthy ring-1 ring-inset ring-healthy/30',
    border: 'border-l-healthy',
    text: 'text-healthy',
    dot: 'bg-healthy',
    glow: 'shadow-[0_0_20px_-4px_var(--color-healthy)]',
  },
  degrading: {
    badge: 'bg-degrading/15 text-degrading ring-1 ring-inset ring-degrading/30',
    border: 'border-l-degrading',
    text: 'text-degrading',
    dot: 'bg-degrading',
    glow: 'shadow-[0_0_20px_-4px_var(--color-degrading)]',
  },
  faulty: {
    badge: 'bg-faulty/15 text-faulty ring-1 ring-inset ring-faulty/30',
    border: 'border-l-faulty',
    text: 'text-faulty',
    dot: 'bg-faulty',
    glow: 'shadow-[0_0_20px_-4px_var(--color-faulty)]',
  },
  critical: {
    badge: 'bg-critical/15 text-critical ring-1 ring-inset ring-critical/30',
    border: 'border-l-critical',
    text: 'text-critical',
    dot: 'bg-critical',
    glow: 'shadow-[0_0_20px_-4px_var(--color-critical)]',
  },
  unknown: {
    badge: 'bg-unknown/15 text-unknown ring-1 ring-inset ring-unknown/30',
    border: 'border-l-unknown',
    text: 'text-unknown',
    dot: 'bg-unknown',
    glow: 'shadow-[0_0_20px_-4px_var(--color-unknown)]',
  },
}

export function healthClasses(state: string): StateClasses {
  return MAP[state] ?? MAP.unknown
}

// The raw CSS custom property for a state's signal color, for places that need
// a *value* rather than a class — chiefly the `--spot-tone` the hover spotlight
// reads, which has to reach `color-mix()` and so can't be a Tailwind utility.
const TONE: Record<string, string> = {
  healthy: 'var(--color-healthy)',
  degrading: 'var(--color-degrading)',
  faulty: 'var(--color-faulty)',
  critical: 'var(--color-critical)',
  unknown: 'var(--color-unknown)',
}

export function healthTone(state: string): string {
  return TONE[state] ?? TONE.unknown
}

export function healthLabel(state: string): string {
  if (!state) return 'Unknown'
  return state.charAt(0).toUpperCase() + state.slice(1)
}

// Severity (low/medium/high) reuses the same warm→hot ramp as soft pills.
export function severityClasses(severity: string): string {
  switch (severity) {
    case 'high':
      return 'bg-critical/15 text-critical ring-1 ring-inset ring-critical/30'
    case 'medium':
      return 'bg-faulty/15 text-faulty ring-1 ring-inset ring-faulty/30'
    case 'low':
      return 'bg-degrading/15 text-degrading ring-1 ring-inset ring-degrading/30'
    default:
      return 'bg-unknown/15 text-unknown ring-1 ring-inset ring-unknown/30'
  }
}

// Sensor-node states (design/2026-10-06-device-health-design.md) borrow the
// health intensity ramp rather than adding colours: a stale node is a warning,
// an offline one is as loud as a critical machine, never-reported is neutral.
type DeviceTone = 'healthy' | 'degrading' | 'critical' | 'unknown'

const DEVICE_STATE_TONE: Record<string, DeviceTone> = {
  online: 'healthy',
  stale: 'degrading',
  offline: 'critical',
  never_reported: 'unknown',
}

const DEVICE_STATE_LABEL: Record<string, string> = {
  online: 'Online',
  stale: 'Stale',
  offline: 'Offline',
  never_reported: 'Never reported',
}

export function deviceStateClasses(state: string): StateClasses {
  return healthClasses(DEVICE_STATE_TONE[state] ?? 'unknown')
}

// The Badge variant for a device state (same mapping as the classes above).
export function deviceStateTone(state: string): DeviceTone {
  return DEVICE_STATE_TONE[state] ?? 'unknown'
}

export function deviceStateLabel(state: string): string {
  return DEVICE_STATE_LABEL[state] ?? 'Unknown'
}

// Work orders (design/2026-10-07-work-orders-escalation-design.md) reuse the
// Badge variants: an untouched order is neutral, an owned one accent, work in
// progress a warning, done healthy, and a cancelled order fades to unknown.
type WorkOrderTone = 'neutral' | 'accent' | 'degrading' | 'healthy' | 'unknown'

const WORK_ORDER_STATUS_TONE: Record<string, WorkOrderTone> = {
  open: 'neutral',
  assigned: 'accent',
  in_progress: 'degrading',
  done: 'healthy',
  cancelled: 'unknown',
}

const WORK_ORDER_STATUS_LABEL: Record<string, string> = {
  open: 'Open',
  assigned: 'Assigned',
  in_progress: 'In progress',
  done: 'Done',
  cancelled: 'Cancelled',
}

export function workOrderStatusTone(status: string): WorkOrderTone {
  return WORK_ORDER_STATUS_TONE[status] ?? 'unknown'
}

export function workOrderStatusLabel(status: string): string {
  return WORK_ORDER_STATUS_LABEL[status] ?? 'Unknown'
}

// Priority shares the alert-severity ramp (severityClasses) it defaults from.
const PRIORITY_TONE: Record<string, 'degrading' | 'faulty' | 'critical'> = {
  low: 'degrading',
  medium: 'faulty',
  high: 'critical',
}

export function workOrderPriorityTone(priority: string): 'degrading' | 'faulty' | 'critical' | 'unknown' {
  return PRIORITY_TONE[priority] ?? 'unknown'
}

// Paging level of an unacknowledged alert: supervisors re-paged (1) is a
// warning, admins paged (2, the top of the ladder) is as loud as critical.
export function pageLevelTone(level: number): 'neutral' | 'degrading' | 'critical' {
  if (level >= 2) return 'critical'
  if (level === 1) return 'degrading'
  return 'neutral'
}

// Was the alert actually re-paged by the ladder? Alerts that predate it
// (migration 4) and batch seed alerts are stored at the top level with
// last_paged_at null so the ladder never pages them; they get no badge.
export function wasLadderPaged(alert: { page_level: number; last_paged_at: string | null }): boolean {
  return alert.page_level > 0 && alert.last_paged_at != null
}

// Prediction feedback (design/2026-10-07-prediction-feedback-design.md) reuses
// the intensity tokens: a real failure is as loud as critical, a failure the
// maintenance prevented is the good outcome, a false alarm a warning.
type FeedbackTone = 'critical' | 'healthy' | 'degrading' | 'unknown'

const FEEDBACK_OUTCOME_TONE: Record<string, FeedbackTone> = {
  confirmed_failure: 'critical',
  maintenance_prevented: 'healthy',
  false_alarm: 'degrading',
  unknown: 'unknown',
}

const FEEDBACK_OUTCOME_LABEL: Record<string, string> = {
  confirmed_failure: 'Confirmed failure',
  maintenance_prevented: 'Prevented by maintenance',
  false_alarm: 'False alarm',
  unknown: 'Unknown',
}

export function feedbackOutcomeTone(outcome: string): FeedbackTone {
  return FEEDBACK_OUTCOME_TONE[outcome] ?? 'unknown'
}

export function feedbackOutcomeLabel(outcome: string): string {
  return FEEDBACK_OUTCOME_LABEL[outcome] ?? 'Unknown'
}

// Root-cause labels (src/root_cause/rule_based.py plus `other`). A cause this
// build doesn't know is shown as-is rather than hidden.
const CAUSE_LABEL: Record<string, string> = {
  bearing_wear: 'Bearing wear',
  imbalance: 'Imbalance',
  sensor_or_data_quality_issue: 'Sensor / data quality issue',
  unknown: 'Unknown',
  other: 'Other',
}

export function causeLabel(cause: string | null | undefined): string {
  if (!cause) return 'Not specified'
  return CAUSE_LABEL[cause] ?? cause
}

// "Why this alert?" key-factor bars (design/2026-10-07-alert-explanation-design.md)
// use the same warm→hot ramp as severity, by each factor's share of the top
// score: the factor that moved most reads critical. A steady channel (at its
// baseline) is neutral whatever its score. Never red/green.
export function factorIntensity(share: number, direction: string): 'critical' | 'faulty' | 'degrading' | 'unknown' {
  if (direction === 'steady') return 'unknown'
  if (share >= 0.66) return 'critical'
  if (share >= 0.33) return 'faulty'
  return 'degrading'
}
