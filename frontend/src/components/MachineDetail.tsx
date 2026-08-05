import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import type { Alert, MachineDetail as Detail, MaintenanceRecord, TrendPoint } from '../api/types'
import { api } from '../api/client'
import { healthClasses, healthLabel } from './healthStyles'
import { TrendChart } from './TrendChart'
import { AlertsPanel } from './AlertsPanel'
import { MaintenanceForm } from './MaintenanceForm'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Select } from './ui/input'

interface Props {
  machineId: string
}

const METRICS = [
  { value: 'vibration_h_rms', label: 'Vibration RMS' },
  { value: 'vibration_h_kurtosis', label: 'Kurtosis' },
  { value: 'vibration_h_high_band_energy_ratio', label: 'Band energy ratio' },
  { value: 'temperature_c', label: 'Temperature (°C)' },
]

const HISTORY_PAGE_SIZE = 10

export function MachineDetail({ machineId }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [metric, setMetric] = useState('vibration_h_rms')
  const [points, setPoints] = useState<TrendPoint[]>([])
  const [reloadKey, setReloadKey] = useState(0)
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null)
  const [history, setHistory] = useState<MaintenanceRecord[]>([])
  const [hasMoreHistory, setHasMoreHistory] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)

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

  useEffect(() => {
    if (detail) {
      setHistory(detail.maintenance_history)
      setHasMoreHistory(detail.maintenance_history.length >= HISTORY_PAGE_SIZE)
    }
  }, [detail])

  if (error) {
    return (
      <section className="rounded-2xl border border-critical/40 bg-critical/10 p-4 text-critical backdrop-blur">
        {error}
      </section>
    )
  }

  if (!detail) {
    return <section className="glass rounded-2xl p-4 text-text-muted">Loading…</section>
  }

  const { health, maintenance, alerts } = detail
  const hc = healthClasses(health.health_state)

  async function handleLog(payload: Parameters<typeof api.logMaintenance>[0]) {
    const result = await api.logMaintenance(payload)
    setReloadKey((k) => k + 1)
    setSelectedAlert(null)
    toast.success('Maintenance logged')
    return result
  }

  async function handleLoadMore() {
    setLoadingMore(true)
    try {
      const nextPage = await api.getMaintenanceHistory(machineId, { limit: HISTORY_PAGE_SIZE, offset: history.length })
      setHistory((prev) => [...prev, ...nextPage])
      setHasMoreHistory(nextPage.length >= HISTORY_PAGE_SIZE)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load more history')
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <section className={`glass relative overflow-hidden rounded-2xl border-l-2 ${hc.border} p-5`}>
      <div aria-hidden className={`pointer-events-none absolute -right-10 -top-12 h-32 w-32 rounded-full opacity-20 blur-3xl ${hc.dot}`} />
      <header className="relative flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-mono text-lg font-semibold text-text">{health.machine_id}</h2>
        <Badge variant={health.health_state}>{healthLabel(health.health_state)}</Badge>
      </header>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
        <Fact label="Probable cause" value={health.probable_cause ?? '—'} />
        <Fact label="Risk score" value={String(health.risk_score)} mono />
        <Fact label="Confidence" value={health.confidence != null ? `${Math.round(health.confidence * 100)}%` : '—'} mono />
        <Fact label="Vibration" value={health.vibration_severity} />
        <Fact label="Temperature" value={health.temperature_severity} />
        <Fact label="Last reading" value={health.last_reading_at ?? '—'} mono />
      </dl>

      <div className="mt-4">
        <label className="flex items-center gap-2 text-sm text-text-muted">
          Metric
          <Select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {METRICS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </Select>
        </label>
        <div className="mt-2">
          <TrendChart metric={metric} points={points} />
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold text-text">Maintenance</h3>
          <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <Fact label="Last serviced" value={maintenance.last_maintenance_at ?? 'never'} mono />
            <Fact
              label="Days since"
              value={maintenance.days_since_last_maintenance != null ? String(maintenance.days_since_last_maintenance) : '—'}
              mono
            />
            <Fact label="Records" value={String(maintenance.completed_maintenance_count)} mono />
            <Fact
              label="Avg resolution (h)"
              value={maintenance.avg_alert_resolution_hours != null ? maintenance.avg_alert_resolution_hours.toFixed(2) : '—'}
              mono
            />
            <Fact
              label="Avg acknowledgement (h)"
              value={maintenance.avg_alert_acknowledgement_hours != null ? maintenance.avg_alert_acknowledgement_hours.toFixed(2) : '—'}
              mono
            />
          </dl>
          {maintenance.due_for_inspection && (
            <p className="mt-1 text-xs font-medium text-accent-2">Due for inspection</p>
          )}
          <MaintenanceForm machineId={health.machine_id} onSubmit={handleLog} linkedAlert={selectedAlert} />
        </div>

        <div>
          <h3 className="text-sm font-semibold text-text">Alerts</h3>
          <div className="mt-1">
            <AlertsPanel alerts={alerts} selectedAlertId={selectedAlert?.id ?? null} onSelect={setSelectedAlert} />
          </div>
        </div>
      </div>

      {history.length > 0 && (
        <div className="mt-4">
          <h3 className="text-sm font-semibold text-text">Maintenance history</h3>
          <table className="mt-1 w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase text-text-muted">
                <th className="py-1 pr-3">When</th>
                <th className="py-1 pr-3">Type</th>
                <th className="py-1 pr-3">Description</th>
                <th className="py-1">Technician</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r) => (
                <tr key={r.id} className="border-t border-white/10">
                  <td className="py-1 pr-3 font-mono">{r.performed_at}</td>
                  <td className="py-1 pr-3">{r.type ?? '—'}</td>
                  <td className="py-1 pr-3">{r.description ?? '—'}</td>
                  <td className="py-1">{r.technician ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {hasMoreHistory && (
            <Button type="button" variant="outline" size="sm" className="mt-2" disabled={loadingMore} onClick={handleLoadMore}>
              {loadingMore ? 'Loading…' : 'Load more'}
            </Button>
          )}
        </div>
      )}
    </section>
  )
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-text-muted">{label}</dt>
      <dd className={mono ? 'font-mono text-text' : 'text-text'}>{value}</dd>
    </div>
  )
}
