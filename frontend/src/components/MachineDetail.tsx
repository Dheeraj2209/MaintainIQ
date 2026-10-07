import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import type { Alert, MachineDetail as Detail, MaintenanceRecord, TelemetryDevice, TrendPoint } from '../api/types'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { FEEDBACK_MODE_TITLE, canEditFeedback, feedbackMode } from '../lib/feedback'
import { deviceStateLabel, deviceStateTone, healthClasses, healthLabel } from './healthStyles'
import { TrendChart } from './TrendChart'
import { AlertsPanel } from './AlertsPanel'
import { MaintenanceForm } from './MaintenanceForm'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Select } from './ui/input'

interface Props {
  machineId: string
  // Opens the page-owned CreateWorkOrderDialog: with the selected alert, or
  // null for a free-standing order. The dialog can't live here, because
  // MachineDetailPage remounts this component on every live event for the
  // machine. No handler, no button.
  onCreateWorkOrder?: (alert: Alert | null) => void
  // Opens the page-owned AlertCloseDialog for the selected alert (close it,
  // or record / edit its outcome). Page-owned for the same remount reason.
  // No handler, no button.
  onRecordOutcome?: (alert: Alert) => void
  // Opens the page-owned "Why this alert?" panel, from an alert row or for
  // the selected alert. Page-owned for the same remount reason. No handler,
  // no buttons.
  onExplain?: (alert: Alert) => void
}

const METRICS = [
  { value: 'vibration_h_rms', label: 'Vibration RMS' },
  { value: 'vibration_h_kurtosis', label: 'Kurtosis' },
  { value: 'vibration_h_high_band_energy_ratio', label: 'Band energy ratio' },
  { value: 'rul_minutes', label: 'Predicted RUL (min)' },
]

const HISTORY_PAGE_SIZE = 10

// Worst first: the chip shows the node that most needs attention.
const DEVICE_STATE_RANK: Record<string, number> = { offline: 3, stale: 2, never_reported: 1, online: 0 }

function worstNode(nodes: TelemetryDevice[]): TelemetryDevice | null {
  return nodes.reduce<TelemetryDevice | null>(
    (worst, n) => (!worst || (DEVICE_STATE_RANK[n.state] ?? 0) > (DEVICE_STATE_RANK[worst.state] ?? 0) ? n : worst),
    null,
  )
}

export function MachineDetail({ machineId, onCreateWorkOrder, onRecordOutcome, onExplain }: Props) {
  const { user } = useAuth()
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [metric, setMetric] = useState('vibration_h_rms')
  const [points, setPoints] = useState<TrendPoint[]>([])
  const [reloadKey, setReloadKey] = useState(0)
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null)
  const [history, setHistory] = useState<MaintenanceRecord[]>([])
  const [hasMoreHistory, setHasMoreHistory] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [nodes, setNodes] = useState<TelemetryDevice[]>([])

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

  // Sensor nodes on this machine. Independent of the detail fetch: a failure
  // here (or an older backend) just shows "—". MachineDetailPage remounts
  // this component on a device event for the machine, which re-runs it.
  useEffect(() => {
    let cancelled = false
    api
      .getTelemetryDevices({ machineId })
      .then((d) => !cancelled && setNodes(d))
      .catch(() => !cancelled && setNodes([]))
    return () => {
      cancelled = true
    }
  }, [machineId])

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
        <Fact
          label="Predicted RUL (min)"
          value={health.predicted_rul_minutes != null ? health.predicted_rul_minutes.toFixed(1) : '—'}
          mono
        />
        <Fact label="RUL estimate" value={health.rul_estimate_kind ?? '—'} />
        <Fact
          label="Out of distribution"
          value={health.out_of_distribution == null ? '—' : health.out_of_distribution ? 'Yes' : 'No'}
        />
        <Fact label="Last reading" value={health.last_reading_at ?? '—'} mono />
        <SensorNodeFact nodes={nodes} />
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
            <Fact label="Open work orders" value={String(maintenance.open_work_order_count ?? 0)} mono />
            <Fact
              label="Avg WO completion (h)"
              value={maintenance.avg_work_order_completion_hours != null ? maintenance.avg_work_order_completion_hours.toFixed(2) : '—'}
              mono
            />
          </dl>
          {maintenance.due_for_inspection && (
            <p className="mt-1 text-xs font-medium text-accent-2">Due for inspection</p>
          )}
          {onCreateWorkOrder && (
            <WorkOrderButton
              alert={selectedAlert}
              canCreateFreeStanding={user?.role === 'admin' || user?.role === 'supervisor'}
              onCreate={onCreateWorkOrder}
            />
          )}
          {onRecordOutcome && (
            <OutcomeButton alert={selectedAlert} canEdit={canEditFeedback(user, selectedAlert?.feedback)} onOpen={onRecordOutcome} />
          )}
          {onExplain && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="ml-2 mt-3"
              disabled={!selectedAlert}
              title={selectedAlert ? `Alert #${selectedAlert.id}` : 'Select an alert first'}
              onClick={() => selectedAlert && onExplain(selectedAlert)}
            >
              Why this alert?
            </Button>
          )}
          <MaintenanceForm machineId={health.machine_id} onSubmit={handleLog} linkedAlert={selectedAlert} />
        </div>

        <div>
          <h3 className="text-sm font-semibold text-text">Alerts</h3>
          <div className="mt-1">
            <AlertsPanel
              alerts={alerts}
              selectedAlertId={selectedAlert?.id ?? null}
              onSelect={setSelectedAlert}
              onExplain={onExplain}
            />
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

// Raise a work order from the selected alert; with none selected only admins
// and supervisors may raise a free-standing one (the server's rule too).
function WorkOrderButton({
  alert,
  canCreateFreeStanding,
  onCreate,
}: {
  alert: Alert | null
  canCreateFreeStanding: boolean
  onCreate: (alert: Alert | null) => void
}) {
  if (alert?.active_work_order_id != null) {
    return (
      <Button type="button" variant="outline" size="sm" className="mt-3" disabled>
        WO #{alert.active_work_order_id} open
      </Button>
    )
  }
  const blocked = !alert && !canCreateFreeStanding
  return (
    <Button
      type="button"
      variant="accent2"
      size="sm"
      className="mt-3"
      disabled={blocked}
      title={blocked ? 'Select an alert first' : alert ? `From alert #${alert.id}` : 'Not tied to an alert'}
      onClick={() => onCreate(alert)}
    >
      Create work order
    </Button>
  )
}

// Close the selected alert, or record / edit its outcome. An outcome someone
// else recorded is only editable by them, an admin or a supervisor.
function OutcomeButton({
  alert,
  canEdit,
  onOpen,
}: {
  alert: Alert | null
  canEdit: boolean
  onOpen: (alert: Alert) => void
}) {
  if (!alert) {
    return (
      <Button type="button" variant="outline" size="sm" className="ml-2 mt-3" disabled title="Select an alert first">
        Close / record outcome
      </Button>
    )
  }
  const mode = feedbackMode(alert)
  const blocked = mode === 'edit' && !canEdit
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="ml-2 mt-3"
      disabled={blocked}
      title={blocked ? 'Only the recorder, an admin or a supervisor can change this outcome' : `Alert #${alert.id}`}
      onClick={() => onOpen(alert)}
    >
      {FEEDBACK_MODE_TITLE[mode]}
    </Button>
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

function SensorNodeFact({ nodes }: { nodes: TelemetryDevice[] }) {
  const worst = worstNode(nodes)
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-text-muted">Sensor node</dt>
      <dd className="text-text">
        {worst ? (
          <Badge variant={deviceStateTone(worst.state)} title={`${nodes.length} node(s) on this machine`}>
            {deviceStateLabel(worst.state)} · <span className="font-mono">{worst.device_id}</span>
            {nodes.length > 1 && <span className="text-text-muted"> +{nodes.length - 1}</span>}
          </Badge>
        ) : (
          '—'
        )}
      </dd>
    </div>
  )
}
