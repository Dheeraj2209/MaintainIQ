// /m/machines/:id (design/2026-10-07-mobile-operator-pwa-design.md, "Pages"):
// where a scanned QR label lands. Health at a glance, then the machine's open
// alerts with the quick actions. Re-fetches on any live event for the
// machine, like the desktop MachineDetailPage.
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ApiError, api } from '../../api/client'
import type { Alert, MachineSummary } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import { AlertCloseDialog } from '../../components/AlertCloseDialog'
import type { AlertCloseResult } from '../../components/AlertCloseForm'
import { formatRul } from '../../components/dashboard/risk'
import { HealthStateBadges } from '../../components/HealthStateBadges'
import { heldText } from '../../lib/healthHold'
import { formatPercent, formatRelative } from '../../lib/telemetryFormat'
import { MobileAlertCard } from '../../mobile/MobileAlertCard'
import { useQuickAlertActions } from '../../mobile/useQuickAlertActions'
import { useLiveEvents } from '../../realtime/LiveEventsProvider'

export function MobileMachineDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  return <MachineView key={id} machineId={id} />
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="font-mono text-base text-text">{value}</dd>
    </div>
  )
}

function MachineView({ machineId }: { machineId: string }) {
  const { user } = useAuth()
  const { lastEvent } = useLiveEvents()
  const navigate = useNavigate()
  const [health, setHealth] = useState<MachineSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [closing, setClosing] = useState<Alert | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const reload = useCallback(() => setReloadTick((n) => n + 1), [])
  const { alerts, setServerAlerts, acknowledge, createWorkOrder } = useQuickAlertActions({ userId: user?.id, reload })

  const load = useCallback(async () => {
    try {
      const detail = await api.getMachine(machineId)
      setHealth(detail.health)
      setServerAlerts(detail.alerts.filter((a) => a.status === 'open'))
      setError(null)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNotFound(true)
      else setError(e instanceof Error ? e.message : 'Failed to load the machine')
    }
  }, [machineId, setServerAlerts])

  useEffect(() => {
    void load()
  }, [load, reloadTick])

  // Both alert and device events carry machine_id.
  useEffect(() => {
    if (lastEvent && lastEvent.machine_id === machineId) void load()
  }, [lastEvent, load, machineId])

  function handleClosed(result: AlertCloseResult) {
    setClosing(null)
    toast.success(result.closed ? `Alert #${result.alert.id} closed` : 'Outcome recorded')
    reload()
  }

  if (notFound) {
    return (
      <div className="space-y-4 py-6 text-center">
        <p role="alert" className="text-sm text-critical">
          Unknown machine '{machineId}'
        </p>
        <button
          type="button"
          onClick={() => navigate('/m/scan')}
          className="min-h-11 rounded-xl border border-accent/50 bg-accent/20 px-5 text-sm font-medium text-text"
        >
          Scan again
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {error && (
        <p role="alert" className="rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
          {error}
        </p>
      )}

      {!health ? (
        !error && <p className="py-6 text-center text-sm text-text-muted">Loading machine…</p>
      ) : (
        <>
          <section aria-label="Machine health" className="glass space-y-3 rounded-2xl p-4">
            <div className="flex items-center justify-between gap-2">
              <h1 className="truncate text-xl font-bold text-text">{health.machine_id}</h1>
              <HealthStateBadges health={health} />
            </div>
            <dl className="grid grid-cols-2 gap-3">
              <Fact label="Remaining life" value={heldText(health) ?? formatRul(health.predicted_rul_minutes)} />
              <Fact label="Risk" value={String(Math.round(health.risk_score))} />
              <Fact label="Confidence" value={formatPercent(health.confidence, 0)} />
              <Fact label="Last reading" value={formatRelative(health.last_reading_at)} />
            </dl>
            {health.out_of_distribution && (
              <p role="note" className="rounded-xl border border-degrading/40 bg-degrading/10 p-2 text-xs text-degrading">
                Recent readings are outside what the model was trained on — treat the estimate with care.
              </p>
            )}
          </section>

          <section aria-labelledby="machine-alerts-heading" className="space-y-3">
            <h2 id="machine-alerts-heading" className="text-lg font-semibold text-text">
              Open alerts
            </h2>
            {alerts.length === 0 ? (
              <p className="text-sm text-text-muted">No open alerts on this machine.</p>
            ) : (
              <ul className="space-y-3">
                {alerts.map((a) => (
                  <li key={a.id}>
                    <MobileAlertCard
                      alert={a}
                      user={user}
                      onAcknowledge={(x) => void acknowledge(x)}
                      onWorkOrder={(x) => void createWorkOrder(x)}
                      onClose={setClosing}
                      onWhy={(x) => navigate(`/m/alerts/${x.id}`)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      <div className="flex flex-wrap gap-4 text-sm">
        <Link to={`/machines/${encodeURIComponent(machineId)}`} className="inline-flex min-h-11 items-center text-accent">
          Full machine page
        </Link>
        <Link to="/m/scan" className="inline-flex min-h-11 items-center text-accent">
          Scan another
        </Link>
      </div>

      <AlertCloseDialog open={closing != null} alert={closing} onClose={() => setClosing(null)} onSaved={handleClosed} />
    </div>
  )
}
