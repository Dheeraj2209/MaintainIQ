// Central mapping from a health/severity state to Tailwind utility classes and
// a display label. Pure functions so they're trivially unit-testable and the
// color coding stays consistent across every component.

interface StateClasses {
  badge: string // background badge
  border: string // left-border accent for cards
  text: string
}

const MAP: Record<string, StateClasses> = {
  healthy: { badge: 'bg-healthy text-white', border: 'border-l-healthy', text: 'text-healthy' },
  degrading: { badge: 'bg-degrading text-black', border: 'border-l-degrading', text: 'text-degrading' },
  faulty: { badge: 'bg-faulty text-white', border: 'border-l-faulty', text: 'text-faulty' },
  critical: { badge: 'bg-critical text-white', border: 'border-l-critical', text: 'text-critical' },
  unknown: { badge: 'bg-unknown text-white', border: 'border-l-unknown', text: 'text-unknown' },
}

export function healthClasses(state: string): StateClasses {
  return MAP[state] ?? MAP.unknown
}

export function healthLabel(state: string): string {
  if (!state) return 'Unknown'
  return state.charAt(0).toUpperCase() + state.slice(1)
}

// Severity (low/medium/high) reuses the same warm→hot ramp.
export function severityClasses(severity: string): string {
  switch (severity) {
    case 'high':
      return 'bg-critical text-white'
    case 'medium':
      return 'bg-faulty text-white'
    case 'low':
      return 'bg-degrading text-black'
    default:
      return 'bg-unknown text-white'
  }
}
