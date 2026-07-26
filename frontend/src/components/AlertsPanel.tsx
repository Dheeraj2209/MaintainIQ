import type { Alert } from '../api/types'
import { severityClasses } from './healthStyles'

interface Props {
  alerts: Alert[]
  onSelect: (machineId: string) => void
}

export function AlertsPanel({ alerts, onSelect }: Props) {
  if (alerts.length === 0) {
    return <p className="py-4 text-sm text-slate-400">No open alerts.</p>
  }

  return (
    <ul className="space-y-2">
      {alerts.map((a) => (
        <li key={a.id}>
          <button
            type="button"
            onClick={() => onSelect(a.machine_id)}
            className="w-full rounded-md border-l-4 border-slate-300 bg-slate-50 p-2 text-left hover:bg-slate-100"
            style={{ borderLeftColor: 'var(--color-' + severityBorder(a.severity) + ')' }}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm text-slate-800">{a.message ?? `${a.machine_id}: ${a.health_state}`}</span>
              <span className={`rounded-full px-2 py-0.5 text-xs ${severityClasses(a.severity)}`}>
                {a.severity}
              </span>
            </div>
            <div className="mt-1 text-xs text-slate-400">
              {a.status} · opened {a.opened_at}
            </div>
          </button>
        </li>
      ))}
    </ul>
  )
}

function severityBorder(severity: string): string {
  if (severity === 'high') return 'critical'
  if (severity === 'medium') return 'faulty'
  if (severity === 'low') return 'degrading'
  return 'unknown'
}
