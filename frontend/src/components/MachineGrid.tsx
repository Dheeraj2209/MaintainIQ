import { motion } from 'framer-motion'
import type { CSSProperties } from 'react'
import type { MachineSummary } from '../api/types'
import { HealthStateBadges } from './HealthStateBadges'
import { healthClasses, healthTone } from './healthStyles'
import { toneState } from '../lib/healthHold'

interface Props {
  machines: MachineSummary[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function MachineGrid({ machines, selectedId, onSelect }: Props) {
  if (machines.length === 0) {
    return <p className="py-6 text-sm text-text-muted">No machines to display.</p>
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {machines.map((m, i) => {
        // Commissioning (or a reset awaiting its first reading) is neutral,
        // not the forced-healthy green.
        const tone = toneState(m)
        const cls = healthClasses(tone)
        const selected = m.machine_id === selectedId
        return (
          <motion.button
            key={m.machine_id}
            type="button"
            aria-pressed={selected}
            onClick={() => onSelect(m.machine_id)}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, delay: i * 0.04, ease: 'easeOut' }}
            whileHover={{ y: -3 }}
            // The proximity light picks up the tile's own health color, so a
            // critical machine catches magenta and a healthy one stays slate.
            style={{ '--spot-tone': healthTone(tone) } as CSSProperties}
            // No `overflow-hidden` — the edge-catch ring sits at `inset: -1px`
            // and clipping would erase it. The corner glow blob that used to
            // need that clipping is gone: it lit the tile at all times, which
            // the cursor lamp now does only when you're actually near it.
            className={`glass hover-glow group relative rounded-2xl p-4 text-left ${
              selected ? 'border-accent/60 ring-1 ring-accent/40' : ''
            }`}
          >
            <div className="relative flex items-center justify-between gap-2">
              <span className="font-mono font-semibold text-text">{m.machine_id}</span>
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${cls.dot} ${cls.glow}`} aria-hidden />
            </div>
            <span className="relative mt-2 flex">
              <HealthStateBadges health={m} />
            </span>
            <div className="relative mt-3 flex items-center gap-1.5 font-mono text-xs text-text-muted">
              <span className={cls.text}>Risk {m.risk_score}</span>
              <span className="text-text-muted/50">·</span>
              <span>{m.open_alert_count} open</span>
            </div>
          </motion.button>
        )
      })}
    </div>
  )
}
