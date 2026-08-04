import { Fragment, useCallback, useEffect, useState } from 'react'
import type { NotificationOut } from '../api/types'
import { api } from '../api/client'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { Select } from '../components/ui/input'
import { Badge } from '../components/ui/badge'

type StatusFilter = 'all' | 'sent' | 'failed'

export function NotificationsPage() {
  const [notifications, setNotifications] = useState<NotificationOut[]>([])
  const [status, setStatus] = useState<StatusFilter>('all')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { lastEvent } = useLiveEvents()

  const load = useCallback((s: StatusFilter) => {
    setError(null)
    api
      .listNotifications(s === 'all' ? undefined : s)
      .then(setNotifications)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load notifications'))
  }, [])

  useEffect(() => {
    load(status)
  }, [load, status])

  useEffect(() => {
    if (lastEvent) load(status)
  }, [lastEvent, load, status])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-text">Notifications</h1>
          <p className="text-xs text-text-muted">Email delivery log — {notifications.length} entries</p>
        </div>
        <label className="flex items-center gap-1 text-sm text-text-muted">
          Status
          <Select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
            <option value="all">All</option>
            <option value="sent">Sent</option>
            <option value="failed">Failed</option>
          </Select>
        </label>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      {notifications.length === 0 ? (
        <p className="py-6 text-sm text-text-muted">No notifications logged yet.</p>
      ) : (
        <div className="panel-notch glass overflow-x-auto rounded-2xl">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface">
              <tr className="text-xs uppercase text-text-muted">
                <th className="px-3 py-2">To</th>
                <th className="px-3 py-2">Subject</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Sent</th>
              </tr>
            </thead>
            <tbody>
              {notifications.map((n) => (
                <Fragment key={n.id}>
                  <tr
                    onClick={() => setExpandedId((id) => (id === n.id ? null : n.id))}
                    className="cursor-pointer border-t border-border bg-surface hover:bg-surface-hover"
                  >
                    <td className="px-3 py-2 text-text-muted">
                      {n.recipient_email}
                      {n.recipient_role && <span className="ml-1 text-xs capitalize">({n.recipient_role})</span>}
                    </td>
                    <td className="px-3 py-2 font-medium text-text">{n.subject}</td>
                    <td className="px-3 py-2">
                      <Badge variant={n.status === 'sent' ? 'healthy' : 'critical'}>{n.status}</Badge>
                    </td>
                    <td className="px-3 py-2 text-text-muted">{n.created_at}</td>
                  </tr>
                  {expandedId === n.id && (
                    <tr className="border-t border-border bg-bg">
                      <td colSpan={4} className="whitespace-pre-wrap px-3 py-3 text-text-muted">
                        {n.body}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
