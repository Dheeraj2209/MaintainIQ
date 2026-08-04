import type { KpiSummary } from '../api/types'
import { MetricCard } from './ui/metric-card'

interface CardSpec {
  label: string
  value: string | number
  sub?: string
  tone?: 'default' | 'healthy' | 'degrading' | 'critical' | 'accent'
}

export function KpiCards({ summary }: { summary: KpiSummary }) {
  const counts = summary.health_state_counts
  const cards: CardSpec[] = [
    { label: 'Machines', value: summary.machine_count, tone: 'accent' },
    { label: 'Healthy', value: counts.healthy ?? 0, sub: 'machines', tone: 'healthy' },
    { label: 'Degrading / Faulty', value: (counts.degrading ?? 0) + (counts.faulty ?? 0), sub: 'machines', tone: 'degrading' },
    { label: 'Critical', value: counts.critical ?? 0, sub: 'machines', tone: 'critical' },
    { label: 'Open alerts', value: summary.open_alert_count, sub: 'unresolved', tone: 'critical' },
    { label: 'Due for inspection', value: summary.machines_due_for_inspection, sub: 'machines', tone: 'degrading' },
  ]

  const pred = summary.prediction
  if (pred.status === 'available' && typeof pred.accuracy === 'number') {
    cards.push({
      label: 'Model accuracy',
      value: `${(pred.accuracy * 100).toFixed(1)}%`,
      sub: pred.winning_model,
      tone: 'accent',
    })
  }

  return (
    <section aria-label="Fleet KPI summary" className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-7">
      {cards.map((c) => (
        <MetricCard key={c.label} {...c} />
      ))}
    </section>
  )
}
