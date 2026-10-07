import type { CSSProperties } from 'react'
import type { HealthState } from '../../api/types'
import { healthLabel, healthTone } from '../healthStyles'

/**
 * Fleet composition as concentric arcs — one ring per health state, each arc
 * sweeping its share of the fleet.
 *
 * Why rings rather than a donut: a donut forces the five states to share one
 * circle, so the smallest slice (usually `critical` — the one that matters
 * most) is the hardest to see. Giving every state its own full-circle track
 * means a single critical machine still draws a visible arc, and every state is
 * read against the same 0–100% scale rather than against each other.
 *
 * Severity rises inward, so the eye lands on the hottest state at the core.
 * `unknown` sits outermost: it isn't a severity, it's an absence of one.
 */

const RING_ORDER: HealthState[] = ['unknown', 'healthy', 'degrading', 'faulty', 'critical']

/** Outermost first, matching RING_ORDER. viewBox units. */
const RADII = [92, 76, 60, 44, 28]
const SIZE = 200
const CENTER = SIZE / 2
const STROKE = 11

interface Props {
  counts: Partial<Record<HealthState, number>>
  total: number
}

export function FleetRings({ counts, total }: Props) {
  const rings = RING_ORDER.map((state, i) => {
    const count = counts[state] ?? 0
    const radius = RADII[i]
    const circumference = 2 * Math.PI * radius
    const fraction = total > 0 ? count / total : 0
    return { state, count, radius, circumference, fraction }
  })

  // Rows are listed hottest-first: the legend is a triage list, and triage
  // starts at the top.
  const legend = [...rings].reverse()

  return (
    <section
      aria-label="Fleet composition"
      style={{ '--spot-tone': 'var(--color-accent-2)' } as CSSProperties}
      className="panel-notch glass hover-glow animate-settle rounded-3xl p-6"
    >
      <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-text-muted">Fleet composition</p>

      <div className="mt-4 flex flex-col items-center gap-6 sm:flex-row sm:items-center">
        <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-40 shrink-0 sm:w-44" role="img" aria-labelledby="fleet-rings-desc">
          <desc id="fleet-rings-desc">
            {total} machines:{' '}
            {legend
              .filter((r) => r.count > 0)
              .map((r) => `${r.count} ${healthLabel(r.state).toLowerCase()}`)
              .join(', ') || 'none classified yet'}
            .
          </desc>

          {/* Rotated so every arc starts at twelve o'clock and sweeps clockwise. */}
          <g transform={`rotate(-90 ${CENTER} ${CENTER})`}>
            {rings.map((r) => (
              <g key={r.state}>
                <circle
                  cx={CENTER}
                  cy={CENTER}
                  r={r.radius}
                  fill="none"
                  className="stroke-white/6"
                  strokeWidth={STROKE}
                />
                {r.count > 0 && (
                  <circle
                    cx={CENTER}
                    cy={CENTER}
                    r={r.radius}
                    fill="none"
                    strokeWidth={STROKE}
                    strokeLinecap="round"
                    strokeDasharray={`${(r.fraction * r.circumference).toFixed(2)} ${r.circumference.toFixed(2)}`}
                    // `stroke` is a presentation attribute and will not resolve
                    // `var()`, but a real CSS declaration will — so the token
                    // goes through `style`, not through the attribute.
                    style={{
                      stroke: healthTone(r.state),
                      transition: 'stroke-dasharray 700ms cubic-bezier(0.2, 0.8, 0.2, 1)',
                    }}
                  />
                )}
              </g>
            ))}
          </g>

          <text
            x={CENTER}
            y={CENTER - 2}
            textAnchor="middle"
            className="fill-text font-mono text-[22px] font-semibold"
          >
            {total}
          </text>
          <text x={CENTER} y={CENTER + 14} textAnchor="middle" className="fill-text-muted text-[9px] uppercase tracking-[0.18em]">
            machines
          </text>
        </svg>

        <dl className="w-full min-w-0 space-y-2">
          {legend.map((r) => (
            <div key={r.state} className="flex items-baseline gap-3">
              <span
                aria-hidden
                className="h-2 w-2 shrink-0 translate-y-[-1px] rounded-full"
                style={{ background: healthTone(r.state) }}
              />
              <dt className="min-w-0 flex-1 truncate text-sm text-text-muted">{healthLabel(r.state)}</dt>
              <dd className="shrink-0 font-mono text-sm tabular-nums text-text">
                {r.count}
                <span className="ml-1.5 text-xs text-text-muted">
                  {total > 0 ? `${Math.round(r.fraction * 100)}%` : '—'}
                </span>
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  )
}
