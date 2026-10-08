import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { HealthState, KpiSummary, MachineSummary } from '../api/types'
import { api } from '../api/client'
import { healthClasses, healthLabel } from '../components/healthStyles'
import { HealthStateBadges } from '../components/HealthStateBadges'
import { toneState } from '../lib/healthHold'
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card'
import { MetricCard } from '../components/ui/metric-card'
import { useChartColors } from '../lib/chartColors'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { CategoryBar, type CategorySegment } from '../components/tremor/category-bar'

const HEALTH_ORDER: { state: HealthState; className: string }[] = [
  { state: 'healthy', className: 'bg-healthy' },
  { state: 'degrading', className: 'bg-degrading' },
  { state: 'faulty', className: 'bg-faulty' },
  { state: 'critical', className: 'bg-critical' },
  { state: 'unknown', className: 'bg-unknown' },
]

const pct = (v: number | null | undefined) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—')
const fixed = (v: number | null | undefined, digits: number) => (typeof v === 'number' ? v.toFixed(digits) : '—')

export function AnalyticsPage() {
  const [kpis, setKpis] = useState<KpiSummary | null>(null)
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const { lastEvent } = useLiveEvents()
  const colors = useChartColors()
  const healthColors: Record<HealthState, string> = {
    healthy: colors.healthy,
    degrading: colors.degrading,
    faulty: colors.faulty,
    critical: colors.critical,
    unknown: colors.unknown,
  }

  const load = useCallback(() => {
    setError(null)
    Promise.all([api.getKpiSummary(), api.getMachines()])
      .then(([k, m]) => {
        setKpis(k)
        setMachines(m)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load analytics'))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (lastEvent) load()
  }, [lastEvent, load])

  if (error) {
    return (
      <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
    )
  }

  if (!kpis) {
    return <p className="text-sm text-text-muted">Loading analytics…</p>
  }

  const pieData = (Object.keys(healthColors) as HealthState[])
    .map((state) => ({ name: healthLabel(state), state, value: kpis.health_state_counts[state] ?? 0 }))
    .filter((d) => d.value > 0)

  const topRisk = [...machines].sort((a, b) => b.risk_score - a.risk_score).slice(0, 10)
  const pred = kpis.prediction
  const detection = pred.failure_detection

  const healthSegments: CategorySegment[] = HEALTH_ORDER.map(({ state, className }) => ({
    label: healthLabel(state),
    value: kpis.health_state_counts[state] ?? 0,
    className,
  })).filter((s) => s.value > 0)

  return (
    <div className="rise-children space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Analytics</h1>
        <p className="text-xs text-text-muted">
          Fleet-wide health distribution, prediction accuracy, and risk ranking
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="panel-notch p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">
            Fleet health distribution
          </h2>
          {pieData.length === 0 ? (
            <p className="py-6 text-sm text-text-muted">No machines to summarize.</p>
          ) : (
            <>
              <CategoryBar segments={healthSegments} className="mb-4" />
              <div className="h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={2}>
                    {pieData.map((d) => (
                      <Cell key={d.state} fill={healthColors[d.state]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      backgroundColor: colors.surface,
                      border: `1px solid ${colors.border}`,
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                    labelStyle={{ color: colors.textMuted }}
                    itemStyle={{ color: colors.text }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12, color: colors.textMuted }} />
                </PieChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </Card>

        <Card className="panel-notch p-4">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-text-muted">Prediction model</h2>
          {pred.status === 'available' ? (
            <div className="grid grid-cols-2 gap-3">
              <MetricCard
                label="Active model"
                value={pred.model_version ?? pred.algorithm ?? '—'}
                sub={pred.model_version ? (pred.algorithm ?? undefined) : undefined}
                className="col-span-2"
              />
              <MetricCard label="Failure-detection F1" value={pct(detection?.f1)} tone="healthy" />
              <MetricCard label="ROC AUC" value={fixed(detection?.roc_auc, 2)} tone="accent" />
              <MetricCard label="Precision" value={pct(detection?.precision)} />
              <MetricCard label="Recall" value={pct(detection?.recall)} />
              <MetricCard label="False alarms" value={pred.false_alarm_count ?? '—'} sub="snapshots" tone="degrading" />
              <MetricCard
                label="Missed failure windows"
                value={pred.missed_failure_window_count ?? '—'}
                sub="snapshots"
                tone="critical"
              />
              <MetricCard
                label="RUL error (MAE)"
                className="col-span-2"
                value={typeof pred.rul_mae_minutes === 'number' ? `${pred.rul_mae_minutes.toFixed(1)} min` : '—'}
                sub={
                  typeof pred.prognostic_horizon_minutes === 'number'
                    ? `within ${pred.prognostic_horizon_minutes} min horizon`
                    : undefined
                }
              />
            </div>
          ) : (
            <p className="text-sm text-text-muted">No trained model metrics available yet.</p>
          )}
        </Card>
      </div>

      <Card className="panel-notch p-4">
        <CardHeader className="px-0 pt-0">
          <CardTitle className="text-sm uppercase tracking-wide text-text-muted">Top at-risk machines</CardTitle>
          <span className="text-xs text-text-muted">{kpis.machines_due_for_inspection} due for inspection</span>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {topRisk.length === 0 ? (
            <p className="py-4 text-sm text-text-muted">No machines to rank.</p>
          ) : (
            <ol className="space-y-2">
              {topRisk.map((m, i) => {
                const cls = healthClasses(toneState(m))
                return (
                  <li key={m.machine_id}>
                    <Link
                      to={`/machines/${encodeURIComponent(m.machine_id)}`}
                      className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5 text-sm transition hover:border-accent/40 hover:bg-white/[0.07]"
                    >
                      <span className="flex items-center gap-2">
                        <span className="font-mono text-text-muted">#{i + 1}</span>
                        <span className="font-medium text-text">{m.machine_id}</span>
                        <HealthStateBadges health={m} />
                      </span>
                      <span className={`font-mono ${cls.text}`}>Risk {m.risk_score}</span>
                    </Link>
                  </li>
                )
              })}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
