import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import type { Alert, WorkOrder } from '../api/types'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { AlertCloseDialog } from '../components/AlertCloseDialog'
import { AlertExplanationPanel } from '../components/AlertExplanationPanel'
import type { AlertCloseResult } from '../components/AlertCloseForm'
import { CreateWorkOrderDialog } from '../components/CreateWorkOrderDialog'
import {
  causeLabel,
  feedbackOutcomeLabel,
  feedbackOutcomeTone,
  healthLabel,
  pageLevelTone,
  wasLadderPaged,
  severityClasses,
} from '../components/healthStyles'
import { Badge } from '../components/ui/badge'
import { canEditFeedback } from '../lib/feedback'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

type StatusFilter = 'all' | 'open' | 'resolved'
type SortKey = 'opened_at' | 'severity'

const SEVERITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1, unknown: 0 }

export function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [status, setStatus] = useState<StatusFilter>('open')
  const [sortKey, setSortKey] = useState<SortKey>('opened_at')
  const [error, setError] = useState<string | null>(null)
  const [acknowledgingId, setAcknowledgingId] = useState<number | null>(null)
  // The alert the "Create work order" dialog is open for. Held here, outside
  // the rows, so a live-event re-render of the table can't drop it.
  const [workOrderAlert, setWorkOrderAlert] = useState<Alert | null>(null)
  // Likewise the alert the close / record-outcome dialog is open for.
  const [closeAlert, setCloseAlert] = useState<Alert | null>(null)
  const { user } = useAuth()
  const navigate = useNavigate()
  const { lastEvent } = useLiveEvents()
  // /alerts/:id opens the "Why this alert?" drawer over the list
  // (design/2026-10-07-alert-explanation-design.md); anything that isn't a
  // positive integer just shows the list.
  const { id: idParam } = useParams<{ id: string }>()
  const explainId = idParam && /^\d+$/.test(idParam) ? Number(idParam) : null
  const closeExplanation = useCallback(() => navigate('/alerts'), [navigate])

  const load = useCallback((s: StatusFilter) => {
    setError(null)
    api
      .getAlerts(s === 'all' ? undefined : s)
      .then(setAlerts)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load alerts'))
  }, [])

  useEffect(() => {
    load(status)
  }, [load, status])

  useEffect(() => {
    if (lastEvent) load(status)
  }, [lastEvent, load, status])

  const sorted = useMemo(() => {
    const copy = [...alerts]
    if (sortKey === 'severity') {
      copy.sort((a, b) => (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0))
    } else {
      copy.sort((a, b) => (a.opened_at < b.opened_at ? 1 : -1))
    }
    return copy
  }, [alerts, sortKey])

  async function handleAcknowledge(e: React.MouseEvent, alertId: number) {
    e.stopPropagation()
    setAcknowledgingId(alertId)
    try {
      await api.acknowledgeAlert(alertId)
      load(status)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to acknowledge alert')
    } finally {
      setAcknowledgingId(null)
    }
  }

  function handleCreateWorkOrder(e: React.MouseEvent, alert: Alert) {
    e.stopPropagation()
    setWorkOrderAlert(alert)
  }

  const closeWorkOrderDialog = useCallback(() => setWorkOrderAlert(null), [])

  function handleWorkOrderCreated(order: WorkOrder) {
    setWorkOrderAlert(null)
    toast.success(`Work order #${order.id} created`)
    load(status)
  }

  function handleOpenClose(e: React.MouseEvent, alert: Alert) {
    e.stopPropagation()
    setCloseAlert(alert)
  }

  const closeCloseDialog = useCallback(() => setCloseAlert(null), [])

  function handleOutcomeSaved(result: AlertCloseResult) {
    setCloseAlert(null)
    toast.success(result.closed ? `Alert #${result.alert.id} closed` : 'Outcome recorded')
    load(status)
  }

  return (
    <div className="rise-children space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-text">Alerts</h1>
          <p className="text-xs text-text-muted">{alerts.length} alerts matching this filter</p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1 text-text-muted">
            Status
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as StatusFilter)}
              className="rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
            >
              <option value="open">Open</option>
              <option value="resolved">Resolved</option>
              <option value="all">All</option>
            </select>
          </label>
          <label className="flex items-center gap-1 text-text-muted">
            Sort by
            <select
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              className="rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
            >
              <option value="opened_at">Newest</option>
              <option value="severity">Severity</option>
            </select>
          </label>
        </div>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      {sorted.length === 0 ? (
        <p className="py-6 text-sm text-text-muted">No alerts match this filter.</p>
      ) : (
        <div className="glass overflow-x-auto rounded-2xl">
          <table className="w-full text-left text-sm">
            <thead className="bg-white/[0.04]">
              <tr className="text-xs uppercase text-text-muted">
                <th className="px-3 py-2">Machine</th>
                <th className="px-3 py-2">Severity</th>
                <th className="px-3 py-2">Health state</th>
                <th className="px-3 py-2">Message</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Opened</th>
                <th className="px-3 py-2">Resolved</th>
                <th className="px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((a) => (
                <tr
                  key={a.id}
                  onClick={() => navigate(`/machines/${encodeURIComponent(a.machine_id)}`)}
                  className="cursor-pointer border-t border-white/10 transition hover:bg-white/[0.05]"
                >
                  <td className="px-3 py-2 font-medium text-text">{a.machine_id}</td>
                  <td className="px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${severityClasses(a.severity)}`}>
                      {a.severity}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-text-muted">{healthLabel(a.health_state)}</td>
                  <td className="px-3 py-2 text-text-muted">{a.message ?? '—'}</td>
                  <td className="px-3 py-2 text-text-muted">
                    <span className="flex flex-wrap items-center gap-1.5">
                      {a.status}
                      {wasLadderPaged(a) && (
                        <Badge
                          variant={pageLevelTone(a.page_level)}
                          title={a.last_paged_at ? `Last paged ${a.last_paged_at}` : undefined}
                        >
                          Paged L{a.page_level}
                        </Badge>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-text-muted">{a.opened_at}</td>
                  <td className="px-3 py-2 text-text-muted">{a.resolved_at ?? '—'}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        aria-label={`Why this alert? Alert #${a.id}`}
                        title="Why this alert?"
                        onClick={(e) => {
                          e.stopPropagation()
                          navigate(`/alerts/${a.id}`)
                        }}
                        className="rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-xs text-accent backdrop-blur transition hover:border-accent/60 hover:bg-accent/15"
                      >
                        Why?
                      </button>
                      {a.acknowledged_at ? (
                        <span className="text-xs text-text-muted">Acknowledged</span>
                      ) : (
                        <button
                          type="button"
                          disabled={acknowledgingId === a.id}
                          onClick={(e) => handleAcknowledge(e, a.id)}
                          className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-text backdrop-blur transition hover:border-accent/50 hover:bg-white/10 disabled:opacity-50"
                        >
                          {acknowledgingId === a.id ? 'Acknowledging…' : 'Acknowledge'}
                        </button>
                      )}
                      {a.active_work_order_id != null ? (
                        <Link
                          to={`/work-orders/${a.active_work_order_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="text-xs font-medium text-accent hover:text-accent-hover"
                        >
                          WO #{a.active_work_order_id}
                        </Link>
                      ) : (
                        <button
                          type="button"
                          onClick={(e) => handleCreateWorkOrder(e, a)}
                          className="rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-xs text-accent backdrop-blur transition hover:border-accent/60 hover:bg-accent/15"
                        >
                          Create work order
                        </button>
                      )}
                      <OutcomeControl alert={a} canEdit={canEditFeedback(user, a.feedback)} onOpen={handleOpenClose} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CreateWorkOrderDialog
        open={workOrderAlert != null}
        alert={workOrderAlert}
        onClose={closeWorkOrderDialog}
        onCreated={handleWorkOrderCreated}
      />
      <AlertCloseDialog
        open={closeAlert != null}
        alert={closeAlert}
        onClose={closeCloseDialog}
        onSaved={handleOutcomeSaved}
      />
      {explainId != null && <AlertExplanationPanel key={explainId} alertId={explainId} onClose={closeExplanation} />}
    </div>
  )
}

// The prediction-feedback action of a row: close an open alert, record the
// outcome of a resolved one, or show the recorded outcome (a button into the
// edit dialog when this user may change it). Every click stops propagation,
// since the row itself navigates.
function OutcomeControl({
  alert,
  canEdit,
  onOpen,
}: {
  alert: Alert
  canEdit: boolean
  onOpen: (e: React.MouseEvent, alert: Alert) => void
}) {
  const feedback = alert.feedback
  if (alert.status === 'open') {
    return (
      <button
        type="button"
        onClick={(e) => onOpen(e, alert)}
        className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-text backdrop-blur transition hover:border-accent/50 hover:bg-white/10"
      >
        Close…
      </button>
    )
  }
  if (!feedback) {
    return (
      <button
        type="button"
        onClick={(e) => onOpen(e, alert)}
        className="rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-xs text-accent backdrop-blur transition hover:border-accent/60 hover:bg-accent/15"
      >
        Record outcome
      </button>
    )
  }
  const title = `${causeLabel(feedback.actual_cause)} · recorded by ${feedback.recorded_by_name ?? `user #${feedback.recorded_by}`}`
  const badge = <Badge variant={feedbackOutcomeTone(feedback.outcome)}>{feedbackOutcomeLabel(feedback.outcome)}</Badge>
  if (!canEdit) {
    return (
      <span title={title} onClick={(e) => e.stopPropagation()}>
        {badge}
      </span>
    )
  }
  return (
    <button type="button" title={title} onClick={(e) => onOpen(e, alert)} className="rounded-full transition hover:brightness-125">
      {badge}
    </button>
  )
}
