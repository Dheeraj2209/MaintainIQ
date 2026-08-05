import { AnimatePresence, motion } from 'framer-motion'
import type { Alert } from '../api/types'
import { severityClasses } from './healthStyles'

interface Props {
  alerts: Alert[]
  onSelect: (alert: Alert) => void
  selectedAlertId?: number | null
}

export function AlertsPanel({ alerts, onSelect, selectedAlertId = null }: Props) {
  if (alerts.length === 0) {
    return <p className="py-4 text-sm text-text-muted">No open alerts.</p>
  }

  return (
    <ul className="space-y-2">
      <AnimatePresence initial={false}>
        {alerts.map((a) => (
          <motion.li key={a.id} layout initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
            <motion.button
              type="button"
              onClick={() => onSelect(a)}
              className={`glass w-full overflow-hidden rounded-xl border-l-2 p-3 text-left transition hover:border-white/20 hover:bg-white/[0.07] ${a.id === selectedAlertId ? 'ring-2 ring-accent/60' : ''}`}
              style={{ borderLeftColor: 'var(--color-' + severityBorder(a.severity) + ')' }}
              initial={{ backgroundColor: 'rgba(212,175,55,0.14)' }}
              animate={{ backgroundColor: 'rgba(19,19,19,1)' }}
              transition={{ duration: 1.1, ease: 'easeOut' }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-text">{a.message ?? `${a.machine_id}: ${a.health_state}`}</span>
                <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs ${severityClasses(a.severity)}`}>
                  {a.severity}
                </span>
              </div>
              <div className="mt-1 font-mono text-xs text-text-muted">
                {a.status} · opened {a.opened_at}
              </div>
            </motion.button>
          </motion.li>
        ))}
      </AnimatePresence>
    </ul>
  )
}

function severityBorder(severity: string): string {
  if (severity === 'high') return 'critical'
  if (severity === 'medium') return 'faulty'
  if (severity === 'low') return 'degrading'
  return 'unknown'
}
