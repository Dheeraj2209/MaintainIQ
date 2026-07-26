import type { MachineSummary } from '../api/types'
import { healthClasses, healthLabel } from './healthStyles'

interface Props {
  machines: MachineSummary[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function MachineGrid({ machines, selectedId, onSelect }: Props) {
  if (machines.length === 0) {
    return <p className="py-6 text-sm text-slate-400">No machines to display.</p>
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {machines.map((m) => {
        const cls = healthClasses(m.health_state)
        const selected = m.machine_id === selectedId
        return (
          <button
            key={m.machine_id}
            type="button"
            aria-pressed={selected}
            onClick={() => onSelect(m.machine_id)}
            className={`rounded-lg border border-l-4 bg-white p-3 text-left transition hover:shadow-md ${cls.border} ${
              selected ? 'ring-2 ring-slate-800' : 'ring-1 ring-slate-200'
            }`}
          >
            <div className="font-semibold text-slate-900">{m.machine_id}</div>
            <span className={`mt-1 inline-block rounded-full px-2 py-0.5 text-xs ${cls.badge}`}>
              {healthLabel(m.health_state)}
            </span>
            <div className="mt-2 text-xs text-slate-500">
              Risk {m.risk_score} · {m.open_alert_count} open alert(s)
            </div>
          </button>
        )
      })}
    </div>
  )
}
