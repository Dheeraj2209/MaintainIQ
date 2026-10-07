import { Fragment, useCallback, useEffect, useState } from 'react'
import type { NotificationChannel, NotificationOut } from '../api/types'
import { api } from '../api/client'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { Select } from '../components/ui/input'
import { Badge } from '../components/ui/badge'

type StatusFilter = 'all' | 'sent' | 'failed'
type ChannelFilter = 'all' | NotificationChannel

const CHANNEL_LABEL: Record<NotificationChannel, string> = { email: 'Email', push: 'Push' }

export function NotificationsPage() {
  const [notifications, setNotifications] = useState<NotificationOut[]>([])
  const [status, setStatus] = useState<StatusFilter>('all')
  const [channel, setChannel] = useState<ChannelFilter>('all')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { lastEvent } = useLiveEvents()

  const load = useCallback((s: StatusFilter, c: ChannelFilter) => {
    setError(null)
    api
      .listNotifications(s === 'all' ? undefined : s, c === 'all' ? undefined : c)
      .then(setNotifications)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load notifications'))
  }, [])

  useEffect(() => {
    load(status, channel)
  }, [load, status, channel])

  useEffect(() => {
    if (lastEvent) load(status, channel)
  }, [lastEvent, load, status, channel])

  return (
    <div className="rise-children space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-text">Notifications</h1>
          <p className="text-xs text-text-muted">Delivery log — {notifications.length} entries</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1 text-sm text-text-muted">
            Channel
            <Select value={channel} onChange={(e) => setChannel(e.target.value as ChannelFilter)}>
              <option value="all">All</option>
              <option value="email">Email</option>
              <option value="push">Push</option>
            </Select>
          </label>
          <label className="flex items-center gap-1 text-sm text-text-muted">
            Status
            <Select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
              <option value="all">All</option>
              <option value="sent">Sent</option>
              <option value="failed">Failed</option>
            </Select>
          </label>
        </div>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      {notifications.length === 0 ? (
        <p className="py-6 text-sm text-text-muted">No notifications logged yet.</p>
      ) : (
        <div className="panel-notch glass overflow-x-auto rounded-2xl">
          <table className="w-full text-left text-sm">
            <thead className="bg-white/[0.04]">
              <tr className="text-xs uppercase text-text-muted">
                <th className="px-3 py-2">Channel</th>
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
                    className="cursor-pointer border-t border-white/10 transition hover:bg-white/[0.05]"
                  >
                    <td className="px-3 py-2">
                      <Badge variant={n.channel === 'push' ? 'accent2' : 'neutral'}>{CHANNEL_LABEL[n.channel ?? 'email']}</Badge>
                    </td>
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
                    <tr className="border-t border-white/10 bg-black/20">
                      <td colSpan={5} className="whitespace-pre-wrap px-3 py-3 text-text-muted">
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
