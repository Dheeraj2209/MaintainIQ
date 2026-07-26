import type { KpiSummary } from '../api/types'

interface Card {
  label: string
  value: string | number
  sub?: string
}

function Stat({ label, value, sub }: Card) {
  return (
    <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
    </div>
  )
}

export function KpiCards({ summary }: { summary: KpiSummary }) {
  const counts = summary.health_state_counts
  const cards: Card[] = [
    { label: 'Machines', value: summary.machine_count },
    { label: 'Healthy', value: counts.healthy ?? 0, sub: 'machines' },
    { label: 'Degrading / Faulty', value: (counts.degrading ?? 0) + (counts.faulty ?? 0), sub: 'machines' },
    { label: 'Critical', value: counts.critical ?? 0, sub: 'machines' },
    { label: 'Open alerts', value: summary.open_alert_count, sub: 'unresolved' },
    { label: 'Due for inspection', value: summary.machines_due_for_inspection, sub: 'machines' },
  ]

  const pred = summary.prediction
  if (pred.status === 'available' && typeof pred.accuracy === 'number') {
    cards.push({
      label: 'Model accuracy',
      value: `${(pred.accuracy * 100).toFixed(1)}%`,
      sub: pred.winning_model,
    })
  }

  return (
    <section
      aria-label="Fleet KPI summary"
      className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-7"
    >
      {cards.map((c) => (
        <Stat key={c.label} {...c} />
      ))}
    </section>
  )
}
