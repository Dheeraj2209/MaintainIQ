import { useMemo } from 'react'
import type { MachineSummary } from '../../api/types'
import { URGENT_RISK, formatRul } from './risk'

/**
 * "Where is this fleet headed?" — a forward projection of aggregate risk over
 * the next two weeks.
 *
 * IMPORTANT, and worth stating plainly on the face of the widget: this is NOT
 * a history chart. The API exposes no fleet-level risk time series (see
 * api/client.ts — `getTrends` is per-machine and per-metric), so there is
 * nothing truthful to draw to the left of today. Everything here is projected
 * forward from two values the backend really does return per machine:
 * `risk_score` and `predicted_rul_minutes`.
 *
 * The model is deliberately simple enough to explain in one line: a machine's
 * risk closes the remaining distance to 100 over the course of its predicted
 * remaining life. The fleet index is the mean of those curves. The shaded band
 * is not a statistical confidence interval — it is the same projection run with
 * remaining life stretched and compressed by the models' own mean confidence,
 * so a fleet the models are unsure about visibly fans out.
 */

const HORIZON_DAYS = 14
const URGENT = URGENT_RISK
const MINUTES_PER_DAY = 1440

/* Plot geometry, in viewBox units. */
const W = 720
const H = 210
const PAD_L = 34
const PAD_R = 14
const PAD_T = 12
const PAD_B = 24
const PLOT_W = W - PAD_L - PAD_R
const PLOT_H = H - PAD_T - PAD_B

function clamp(n: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, n))
}

/**
 * Projected risk for one machine `days` from now, with its remaining life
 * scaled by `lifeScale` (1 = the model's own estimate).
 *
 * Machines with no RUL estimate hold their current risk rather than being
 * guessed at — a flat line is the honest representation of "we don't know".
 */
function projectMachine(m: MachineSummary, days: number, lifeScale: number): number {
  const risk = clamp(m.risk_score, 0, 100)
  const rulMinutes = m.predicted_rul_minutes
  if (rulMinutes === null) return risk
  if (rulMinutes <= 0) return 100

  const rulDays = (rulMinutes / MINUTES_PER_DAY) * lifeScale
  if (rulDays <= 0) return 100

  const consumed = Math.min(1, days / rulDays)
  return Math.min(100, risk + (100 - risk) * consumed)
}

function x(day: number) {
  return PAD_L + (day / HORIZON_DAYS) * PLOT_W
}

function y(risk: number) {
  return PAD_T + (1 - clamp(risk, 0, 100) / 100) * PLOT_H
}

function toPath(points: { d: number; r: number }[]) {
  return points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.d).toFixed(1)} ${y(p.r).toFixed(1)}`).join(' ')
}

interface Props {
  machines: MachineSummary[]
  onSelect: (machineId: string) => void
}

export function RiskHorizon({ machines, onSelect }: Props) {
  const model = useMemo(() => {
    const scored = machines.filter((m) => Number.isFinite(m.risk_score))
    if (scored.length === 0) return null

    // Mean confidence across machines the models actually scored. Unscored
    // machines don't get to drag the band wider — they just aren't evidence.
    const confidences = scored.map((m) => m.confidence).filter((c): c is number => c !== null)
    const meanConfidence = confidences.length > 0 ? confidences.reduce((a, b) => a + b, 0) / confidences.length : 0.5

    // Low confidence = wide fan. Capped so a cold-start fleet doesn't render a
    // band that spans the whole plot and says nothing.
    const spread = clamp(1 - meanConfidence, 0, 0.6)

    const mean = (days: number, lifeScale: number) =>
      scored.reduce((sum, m) => sum + projectMachine(m, days, lifeScale), 0) / scored.length

    // Half-day sampling: fine enough that the curve reads as smooth and that
    // the URGENT crossing lands within twelve hours of the true value.
    const steps: number[] = []
    for (let d = 0; d <= HORIZON_DAYS; d += 0.5) steps.push(d)

    const central = steps.map((d) => ({ d, r: mean(d, 1) }))
    // Longer life than predicted = slower escalation = the optimistic edge.
    const optimistic = steps.map((d) => ({ d, r: mean(d, 1 + spread) }))
    const pessimistic = steps.map((d) => ({ d, r: mean(d, 1 - spread) }))

    const crossing = central.find((p) => p.r >= URGENT) ?? null

    const dueSoon = scored
      .filter((m) => m.predicted_rul_minutes !== null)
      .sort((a, b) => (a.predicted_rul_minutes ?? 0) - (b.predicted_rul_minutes ?? 0))
      .slice(0, 3)

    return {
      central,
      optimistic,
      pessimistic,
      crossing,
      dueSoon,
      today: central[0].r,
      atHorizon: central[central.length - 1].r,
      meanConfidence,
      scoredCount: scored.length,
    }
  }, [machines])

  if (!model) {
    return (
      <section aria-label="Risk horizon" className="panel-notch glass animate-settle rounded-3xl p-6">
        <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-text-muted">Risk horizon</p>
        <p className="mt-3 text-sm text-text-muted">No scored machines yet — the projection needs at least one.</p>
      </section>
    )
  }

  const { central, optimistic, pessimistic, crossing, dueSoon } = model

  // Closed area between the two edges: out along the optimistic curve, back
  // along the pessimistic one.
  const bandPath = `${toPath(optimistic)} ${toPath([...pessimistic].reverse()).replace('M', 'L')} Z`

  const headline = crossing
    ? crossing.d === 0
      ? 'now'
      : `${crossing.d < 1 ? '<1' : Math.round(crossing.d)} d`
    : `>${HORIZON_DAYS} d`

  const headlineNote = crossing
    ? crossing.d === 0
      ? 'Fleet risk is already at the intervention threshold'
      : 'until projected fleet risk reaches the intervention threshold'
    : `fleet risk stays below the intervention threshold through ${HORIZON_DAYS} days`

  return (
    <section aria-label="Risk horizon" className="panel-notch glass animate-settle rounded-3xl p-6">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-text-muted">Risk horizon</p>
          <div className="mt-2 flex items-end gap-3">
            <span className="font-mono text-5xl font-semibold leading-none tabular-nums text-text">{headline}</span>
            <span className="max-w-[22rem] pb-1 text-sm leading-snug text-text-muted">{headlineNote}</span>
          </div>
          <p className="mt-2.5 text-xs text-text-muted">
            Projected forward from live remaining-life estimates for {model.scoredCount}{' '}
            {model.scoredCount === 1 ? 'machine' : 'machines'} · mean model confidence{' '}
            <span className="font-mono tabular-nums">{Math.round(model.meanConfidence * 100)}%</span>
          </p>
        </div>

        {/* The countdown rail. The chart answers "when does the fleet get bad";
            this answers "and who takes it there", which is the actual next
            action. */}
        {dueSoon.length > 0 && (
          <div className="shrink-0 lg:w-64">
            <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-text-muted">Shortest remaining life</p>
            <ul className="mt-2.5 space-y-1.5">
              {dueSoon.map((m) => (
                <li key={m.machine_id}>
                  <button
                    type="button"
                    onClick={() => onSelect(m.machine_id)}
                    className="flex w-full items-baseline justify-between gap-3 rounded-lg px-2 py-1.5 text-left transition hover:bg-white/6"
                  >
                    <span className="truncate font-mono text-xs text-text">{m.machine_id}</span>
                    <span className="shrink-0 font-mono text-xs tabular-nums text-text-muted">
                      {formatRul(m.predicted_rul_minutes)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <figure className="mt-5">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-labelledby="risk-horizon-desc">
          <desc id="risk-horizon-desc">
            Projected mean fleet risk over the next {HORIZON_DAYS} days, rising from{' '}
            {Math.round(model.today)} to {Math.round(model.atHorizon)} out of 100.{' '}
            {crossing
              ? `The projection reaches the intervention threshold of ${URGENT} after about ${Math.round(crossing.d)} days.`
              : `The projection stays below the intervention threshold of ${URGENT}.`}
          </desc>

          <defs>
            <linearGradient id="risk-horizon-band" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" style={{ stopColor: 'var(--color-accent)', stopOpacity: 0.34 }} />
              <stop offset="100%" style={{ stopColor: 'var(--color-accent)', stopOpacity: 0.04 }} />
            </linearGradient>
          </defs>

          {/* Horizontal reference lines every 25 risk points. */}
          {[0, 25, 50, 75, 100].map((r) => (
            <g key={r}>
              <line
                x1={PAD_L}
                x2={W - PAD_R}
                y1={y(r)}
                y2={y(r)}
                className="stroke-white/6"
                strokeWidth={1}
                shapeRendering="crispEdges"
              />
              <text x={PAD_L - 8} y={y(r) + 3.5} textAnchor="end" className="fill-text-muted/60 font-mono text-[9px]">
                {r}
              </text>
            </g>
          ))}

          {/* The intervention threshold, drawn as a rule rather than as a
              colour change — severity here is position, not hue. */}
          <line
            x1={PAD_L}
            x2={W - PAD_R}
            y1={y(URGENT)}
            y2={y(URGENT)}
            className="stroke-critical/55"
            strokeWidth={1}
            strokeDasharray="5 5"
          />
          <text x={W - PAD_R} y={y(URGENT) - 6} textAnchor="end" className="fill-critical/80 font-mono text-[9px]">
            Urgent · {URGENT}
          </text>

          <path d={bandPath} fill="url(#risk-horizon-band)" />
          <path
            d={toPath(central)}
            fill="none"
            className="stroke-accent-2"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {crossing && (
            <g>
              <circle cx={x(crossing.d)} cy={y(crossing.r)} r={4.5} className="fill-critical" />
              <circle cx={x(crossing.d)} cy={y(crossing.r)} r={9} className="fill-critical/25" />
            </g>
          )}

          {/* Day axis. Today is spelled out; the rest are day offsets. */}
          {[0, 3, 7, 10, 14].map((d) => (
            <text key={d} x={x(d)} y={H - 6} textAnchor="middle" className="fill-text-muted/60 font-mono text-[9px]">
              {d === 0 ? 'Today' : `+${d}d`}
            </text>
          ))}
        </svg>
        <figcaption className="mt-2 text-[11px] leading-relaxed text-text-muted/80">
          Forward projection only — no history is shown, because fleet-level risk is not recorded over time. The band
          widens as model confidence falls.
        </figcaption>
      </figure>
    </section>
  )
}
