import type { CSSProperties } from 'react'
import type { MachineSummary } from '../../api/types'
import { healthTone } from '../healthStyles'
import { HealthStateBadges } from '../HealthStateBadges'
import { toneState } from '../../lib/healthHold'
import { formatRul, URGENT_RISK } from './risk'

/**
 * The worst machines in the fleet, ranked, with the three numbers that decide
 * what to do about them: how bad it is, how much the model trusts itself, and
 * how long you have.
 *
 * Every bar carries its own number beside it. The bar is for scanning the
 * ranking at a glance; the number is the actual reading, and it is never the
 * colour that tells you the severity — the fill length and the printed value
 * do that, and the health word is spelled out in the badge.
 */

const ROW_LIMIT = 6

interface Props {
  machines: MachineSummary[]
  onSelect: (machineId: string) => void
}

export function MachinesAtRisk({ machines, onSelect }: Props) {
  const ranked = [...machines].sort((a, b) => b.risk_score - a.risk_score).slice(0, ROW_LIMIT)
  const urgentCount = machines.filter((m) => m.risk_score >= URGENT_RISK).length

  return (
    <section aria-label="Machines at risk" className="panel-notch glass animate-settle rounded-3xl p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-text-muted">Machines at risk</p>
        <p className="text-xs text-text-muted">
          {urgentCount > 0 ? (
            <>
              <span className="font-mono tabular-nums text-text">{urgentCount}</span> at or above the urgent threshold of{' '}
              <span className="font-mono tabular-nums">{URGENT_RISK}</span>
            </>
          ) : (
            <>None above the urgent threshold of <span className="font-mono tabular-nums">{URGENT_RISK}</span></>
          )}
        </p>
      </div>

      {ranked.length === 0 ? (
        <p className="mt-4 text-sm text-text-muted">No machines to rank yet.</p>
      ) : (
        <ul className="mt-4 space-y-1.5">
          {/* Column headings, carried by the list rather than a <table> so the
              rows can stay full-width buttons. */}
          <li
            aria-hidden
            className="hidden items-center gap-4 px-3 pb-1 text-[10px] uppercase tracking-[0.18em] text-text-muted/70 sm:flex"
          >
            <span className="min-w-0 flex-[1.4]">Machine</span>
            <span className="flex-1">Risk</span>
            <span className="flex-1">Model confidence</span>
            <span className="w-20 text-right">Life left</span>
          </li>

          {ranked.map((m) => {
            // Neutral while the machine relearns its baseline after a reset.
            const tone = healthTone(toneState(m))
            const confidence = m.confidence === null ? null : Math.round(m.confidence * 100)
            return (
              <li key={m.machine_id}>
                <button
                  type="button"
                  onClick={() => onSelect(m.machine_id)}
                  style={{ '--spot-tone': tone } as CSSProperties}
                  className="hover-glow flex w-full flex-col gap-2 rounded-xl border border-border/70 bg-white/2 px-3 py-2.5 text-left sm:flex-row sm:items-center sm:gap-4"
                >
                  <span className="flex min-w-0 flex-[1.4] items-center gap-2">
                    <span className="truncate font-mono text-sm text-text">{m.machine_id}</span>
                    <span className="shrink-0 text-[10px]">
                      <HealthStateBadges health={m} />
                    </span>
                  </span>

                  <Meter value={m.risk_score} label={`${Math.round(m.risk_score)}`} fill={tone} />

                  <Meter
                    value={confidence ?? 0}
                    label={confidence === null ? 'unscored' : `${confidence}%`}
                    fill="var(--color-accent-2)"
                    muted={confidence === null}
                  />

                  <span className="shrink-0 font-mono text-xs tabular-nums text-text-muted sm:w-20 sm:text-right">
                    {formatRul(m.predicted_rul_minutes)}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

interface MeterProps {
  /** 0–100. */
  value: number
  /** Always rendered — the bar is the glance, this is the reading. */
  label: string
  fill: string
  muted?: boolean
}

function Meter({ value, label, fill, muted }: MeterProps) {
  const pct = Math.max(0, Math.min(100, value))
  return (
    <span className="flex flex-1 items-center gap-2">
      <span className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white/8">
        {!muted && (
          <span
            className="block h-full rounded-full"
            style={{ width: `${pct}%`, background: fill, transition: 'width 600ms cubic-bezier(0.2, 0.8, 0.2, 1)' }}
          />
        )}
      </span>
      <span className={`w-14 shrink-0 font-mono text-xs tabular-nums ${muted ? 'text-text-muted/60' : 'text-text-muted'}`}>
        {label}
      </span>
    </span>
  )
}
