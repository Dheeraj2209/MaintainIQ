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
