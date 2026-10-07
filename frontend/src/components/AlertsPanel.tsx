import { AnimatePresence, motion } from 'framer-motion'
import type { Alert } from '../api/types'
import { feedbackOutcomeLabel, feedbackOutcomeTone, severityClasses } from './healthStyles'
import { Badge } from './ui/badge'

interface Props {
  alerts: Alert[]
  onSelect: (alert: Alert) => void
  selectedAlertId?: number | null
  // Opens "Why this alert?" (design/2026-10-07-alert-explanation-design.md).
  // Rendered as a sibling of the row's select button, since a button can't
  // nest in another. No handler, no button.
  onExplain?: (alert: Alert) => void
}

export function AlertsPanel({ alerts, onSelect, selectedAlertId = null, onExplain }: Props) {
  if (alerts.length === 0) {
    return <p className="py-4 text-sm text-text-muted">No open alerts.</p>
  }

  return (
    <ul className="space-y-2">
      <AnimatePresence initial={false}>
        {alerts.map((a) => (
          <motion.li
            key={a.id}
            layout
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0 }}
            className={onExplain ? 'flex items-stretch gap-2' : undefined}
          >
            <motion.button
              type="button"
              onClick={() => onSelect(a)}
              className={`w-full min-w-0 overflow-hidden rounded-xl border border-white/10 border-l-2 bg-white/[0.03] p-3 text-left transition hover:border-white/20 hover:bg-white/[0.07] ${a.id === selectedAlertId ? 'ring-2 ring-accent/60' : ''}`}
              style={{ borderLeftColor: 'var(--color-' + severityBorder(a.severity) + ')' }}
              initial={{ backgroundColor: 'rgba(124,108,255,0.18)' }}
              animate={{ backgroundColor: 'rgba(255,255,255,0.03)' }}
              transition={{ duration: 1.1, ease: 'easeOut' }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-text">{a.message ?? `${a.machine_id}: ${a.health_state}`}</span>
                <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs ${severityClasses(a.severity)}`}>
                  {a.severity}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-text-muted">
                  {a.status} · opened {a.opened_at}
                </span>
                {a.feedback && (
                  <Badge variant={feedbackOutcomeTone(a.feedback.outcome)}>{feedbackOutcomeLabel(a.feedback.outcome)}</Badge>
                )}
              </div>
            </motion.button>
            {onExplain && (
              <button
                type="button"
                onClick={() => onExplain(a)}
                aria-label={`Why this alert? Alert #${a.id}`}
                title="Why this alert?"
                className="shrink-0 self-center rounded-full border border-accent/30 bg-accent/10 px-2.5 py-0.5 text-xs text-accent backdrop-blur transition hover:border-accent/60 hover:bg-accent/15"
              >
                Why?
              </button>
            )}
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
