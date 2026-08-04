import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Alert } from '../api/types'
import { api } from '../api/client'
import { healthLabel, severityClasses } from '../components/healthStyles'
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
  const navigate = useNavigate()
  const { lastEvent } = useLiveEvents()

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

  return (
    <div className="space-y-4">
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
            <thead className="bg-surface">
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
                  className="cursor-pointer border-t border-border bg-surface hover:bg-surface-hover"
                >
                  <td className="px-3 py-2 font-medium text-text">{a.machine_id}</td>
                  <td className="px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${severityClasses(a.severity)}`}>
                      {a.severity}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-text-muted">{healthLabel(a.health_state)}</td>
                  <td className="px-3 py-2 text-text-muted">{a.message ?? '—'}</td>
                  <td className="px-3 py-2 text-text-muted">{a.status}</td>
                  <td className="px-3 py-2 text-text-muted">{a.opened_at}</td>
                  <td className="px-3 py-2 text-text-muted">{a.resolved_at ?? '—'}</td>
                  <td className="px-3 py-2">
                    {a.acknowledged_at ? (
                      <span className="text-xs text-text-muted">Acknowledged</span>
                    ) : (
                      <button
                        type="button"
                        disabled={acknowledgingId === a.id}
                        onClick={(e) => handleAcknowledge(e, a.id)}
                        className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-text backdrop-blur hover:bg-surface-hover disabled:opacity-50"
                      >
                        {acknowledgingId === a.id ? 'Acknowledging…' : 'Acknowledge'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
