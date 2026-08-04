// Tremor-style CategoryBar — a segmented proportion bar for showing how a total
// splits across categories at a glance. Ported from Tremor Raw's CategoryBar
// pattern (github.com/tremorlabs/tremor — MIT) and themed to our black/red/gold
// palette. Pure divs, so it renders and tests cleanly under jsdom.
import { cn } from '@/lib/cn'

export interface CategorySegment {
  label: string
  value: number
  /** A theme color token, e.g. 'bg-healthy' / 'bg-critical'. */
  className: string
}

interface CategoryBarProps {
  segments: CategorySegment[]
  /** Optional value to drop a marker at (e.g. a threshold). */
  marker?: number
  showLabels?: boolean
  className?: string
}

export function CategoryBar({ segments, marker, showLabels = true, className }: CategoryBarProps) {
  const total = segments.reduce((sum, s) => sum + s.value, 0)
  const pct = (v: number) => (total > 0 ? (v / total) * 100 : 0)

  return (
    <div className={cn('w-full', className)}>
      <div
        role="img"
        aria-label={segments.map((s) => `${s.label}: ${s.value}`).join(', ')}
        className="relative flex h-2.5 items-center gap-[2px] overflow-hidden rounded-full"
      >
        {total === 0 ? (
          <div className="h-full w-full rounded-full bg-white/5" />
        ) : (
          segments
            .filter((s) => s.value > 0)
            .map((s) => (
              <div
                key={s.label}
                className={cn('h-full rounded-full transition-all', s.className)}
                style={{ width: `${pct(s.value)}%` }}
              />
            ))
        )}
        {marker != null && total > 0 && (
          <div
            aria-hidden
            className="absolute top-1/2 h-4 w-1 -translate-y-1/2 rounded-full bg-text shadow-[0_0_0_2px_var(--color-bg)]"
            style={{ left: `calc(${Math.min(100, Math.max(0, pct(marker)))}% - 2px)` }}
          />
        )}
      </div>
      {showLabels && (
        <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
          {segments.map((s) => (
            <li key={s.label} className="flex items-center gap-1.5 text-xs text-text-muted">
              <span aria-hidden className={cn('h-2 w-2 rounded-full', s.className)} />
              <span>{s.label}</span>
              <span className="font-mono tabular-nums text-text">{s.value}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
