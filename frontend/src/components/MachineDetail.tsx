import { useEffect, useState } from 'react'
import type { MachineDetail as Detail, TrendPoint } from '../api/types'
import { api } from '../api/client'
import { healthClasses, healthLabel } from './healthStyles'
import { TrendChart } from './TrendChart'
import { AlertsPanel } from './AlertsPanel'
import { MaintenanceForm } from './MaintenanceForm'

interface Props {
  machineId: string
  onLogged: () => void
}

const METRICS = [
  { value: 'vibration_rms', label: 'Vibration RMS' },
  { value: 'kurtosis', label: 'Kurtosis' },
  { value: 'band_energy_ratio', label: 'Band energy ratio' },
  { value: 'temperature_c', label: 'Temperature (°C)' },
]

export function MachineDetail({ machineId, onLogged }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [metric, setMetric] = useState('vibration_rms')
  const [points, setPoints] = useState<TrendPoint[]>([])
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setError(null)
    api
      .getMachine(machineId)
      .then((d) => !cancelled && setDetail(d))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : 'Failed to load machine'))
    return () => {
      cancelled = true
    }
  }, [machineId, reloadKey])

  useEffect(() => {
    let cancelled = false
    api
      .getTrends(machineId, metric, 200)
      .then((p) => !cancelled && setPoints(p))
      .catch(() => !cancelled && setPoints([]))
    return () => {
      cancelled = true
    }
  }, [machineId, metric])

  if (error) {
    return (
      <section className="rounded-lg border border-critical/40 bg-critical/5 p-4 text-critical">
        {error}
      </section>
    )
  }

  if (!detail) {
    return <section className="rounded-lg border border-slate-200 bg-white p-4 text-slate-500">Loading…</section>
  }

  const { health, maintenance, alerts, maintenance_history } = detail
  const hc = healthClasses(health.health_state)

  async function handleLog(payload: Parameters<typeof api.logMaintenance>[0]) {
    const result = await api.logMaintenance(payload)
    setReloadKey((k) => k + 1)
    onLogged()
    return result
  }

  return (
    <section className={`rounded-lg border border-l-4 ${hc.border} border-slate-200 bg-white p-4 shadow-sm`}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-slate-800">{health.machine_id}</h2>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${hc.badge}`}>
          {healthLabel(health.health_state)}
        </span>
      </header>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
        <Fact label="Probable cause" value={health.probable_cause ?? '—'} />
        <Fact label="Risk score" value={String(health.risk_score)} />
        <Fact label="Confidence" value={health.confidence != null ? `${Math.round(health.confidence * 100)}%` : '—'} />
        <Fact label="Vibration" value={health.vibration_severity} />
        <Fact label="Temperature" value={health.temperature_severity} />
        <Fact label="Last reading" value={health.last_reading_at ?? '—'} />
      </dl>

      <div className="mt-4">
        <label className="flex items-center gap-2 text-sm text-slate-600">
          Metric
          <select
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            {METRICS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <div className="mt-2">
          <TrendChart metric={metric} points={points} />
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-700">Maintenance</h3>
          <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <Fact label="Last serviced" value={maintenance.last_maintenance_at ?? 'never'} />
            <Fact
              label="Days since"
              value={maintenance.days_since_last_maintenance != null ? String(maintenance.days_since_last_maintenance) : '—'}
            />
            <Fact label="Records" value={String(maintenance.completed_maintenance_count)} />
            <Fact
              label="Avg resolution (h)"
              value={maintenance.avg_alert_resolution_hours != null ? maintenance.avg_alert_resolution_hours.toFixed(2) : '—'}
            />
          </dl>
          {maintenance.due_for_inspection && (
            <p className="mt-1 text-xs font-medium text-faulty">Due for inspection</p>
          )}
          <MaintenanceForm machineId={health.machine_id} onSubmit={handleLog} />
        </div>

        <div>
          <h3 className="text-sm font-semibold text-slate-700">Alerts</h3>
          <div className="mt-1">
            <AlertsPanel alerts={alerts} onSelect={() => {}} />
          </div>
        </div>
      </div>

      {maintenance_history.length > 0 && (
        <div className="mt-4">
          <h3 className="text-sm font-semibold text-slate-700">Maintenance history</h3>
          <table className="mt-1 w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase text-slate-400">
                <th className="py-1 pr-3">When</th>
                <th className="py-1 pr-3">Description</th>
                <th className="py-1">Technician</th>
              </tr>
            </thead>
            <tbody>
              {maintenance_history.map((r) => (
                <tr key={r.id} className="border-t border-slate-100">
                  <td className="py-1 pr-3">{r.performed_at}</td>
                  <td className="py-1 pr-3">{r.description ?? '—'}</td>
                  <td className="py-1">{r.technician ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="text-slate-700">{value}</dd>
    </div>
  )
}
