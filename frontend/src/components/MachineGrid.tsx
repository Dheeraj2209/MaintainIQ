import { motion } from 'framer-motion'
import type { MachineSummary } from '../api/types'
import { healthClasses, healthLabel } from './healthStyles'

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
        const cls = healthClasses(m.health_state)
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
            className={`glass hover-glow group relative overflow-hidden rounded-2xl p-4 text-left ${
              selected ? 'border-accent/60 ring-1 ring-accent/40' : ''
            }`}
          >
            {/* Health-tinted glow bloom in the corner keys the tile to its state. */}
            <div
              aria-hidden
              className={`pointer-events-none absolute -right-8 -top-10 h-24 w-24 rounded-full opacity-25 blur-2xl ${cls.dot}`}
            />
            <div className="relative flex items-center justify-between gap-2">
              <span className="font-mono font-semibold text-text">{m.machine_id}</span>
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${cls.dot} ${cls.glow}`} aria-hidden />
            </div>
            <span className={`relative mt-2 inline-flex rounded-full px-2.5 py-0.5 text-xs ${cls.badge}`}>
              {healthLabel(m.health_state)}
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
