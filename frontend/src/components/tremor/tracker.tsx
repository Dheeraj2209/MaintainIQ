// Tremor-style Tracker — a dense row of colored cells, one per entity, for
// showing status across a fleet at a glance. Ported from Tremor Raw's Tracker
// pattern (github.com/tremorlabs/tremor — MIT) and themed to our health tokens.
// Pure divs + native title tooltips, so it renders and tests cleanly under jsdom.
import { cn } from '@/lib/cn'

export interface TrackerCell {
  key: string
  /** A theme color token, e.g. 'bg-healthy' / 'bg-critical'. */
  className: string
  /** Hover/screen-reader description, e.g. 'm1 — Critical'. */
  tooltip: string
  onClick?: () => void
}

interface TrackerProps {
  cells: TrackerCell[]
  className?: string
}

export function Tracker({ cells, className }: TrackerProps) {
  return (
    <div className={cn('flex h-8 w-full items-center gap-[3px]', className)}>
      {cells.map((cell) => {
        const Cell = cell.onClick ? 'button' : 'div'
        return (
          <Cell
            key={cell.key}
            type={cell.onClick ? 'button' : undefined}
            onClick={cell.onClick}
            title={cell.tooltip}
            aria-label={cell.tooltip}
            className={cn(
              'h-full min-w-0 flex-1 rounded-[3px] opacity-80 transition hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60',
              cell.className,
              cell.onClick && 'cursor-pointer',
            )}
          />
        )
      })}
    </div>
  )
}
