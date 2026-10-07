// Devices page (design/2026-10-06-device-health-design.md): the state of every
// sensor node, silence incidents and their acknowledgement. Open to every
// role, unlike the Ingestion page's live-telemetry section.
//
// Polls every 5 s like LiveTelemetryPanel (states age with the clock even when
// nothing is published), and also re-fetches as soon as a device_offline /
// device_online live event arrives. Acknowledging is not broadcast, so other
// viewers pick it up on their next poll.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '../api/client'
import { isDeviceEvent } from '../api/types'
import type { DeviceIncident, DeviceIncidentStatus, DeviceState, TelemetryDevice, TelemetryStatus } from '../api/types'
import { DevicesTable } from '../components/DevicesTable'
import { deviceStateLabel, deviceStateTone } from '../components/healthStyles'
import { Badge } from '../components/ui/badge'
import { Card } from '../components/ui/card'
import { formatRelative } from '../lib/telemetryFormat'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

const DEVICES_POLL_MS = 5000

type HistoryTab = DeviceIncidentStatus | 'all'

const HISTORY_TABS: { value: HistoryTab; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'all', label: 'All' },
]

const STATE_ORDER: DeviceState[] = ['online', 'stale', 'offline', 'never_reported']

const KIND_LABEL: Record<string, string> = { silent: 'Went silent', lwt: 'Disconnected' }

// Why silence alerting may not be protecting these nodes right now, most
// fundamental first; null when ingest is up and the watchdog is armed.
function watchdogWarning(status: TelemetryStatus | null): string | null {
  if (!status) return null
  if (!status.enabled) return 'Live ingest is off — no sensor nodes can report.'
  if (!status.connected) return 'Broker disconnected — silence alerting paused.'
  const wd = status.device_watchdog
  if (!wd) return null
  if (!wd.enabled) return 'Silence alerting is off: set MAINTAINIQ_SWEEP_INTERVAL_S to enable it.'
  if (!wd.armed) return `Silence alerting arms ${Math.round(wd.grace_s)} s after the broker connects.`
  return null
}

export function DevicesPage({ pollMs = DEVICES_POLL_MS }: { pollMs?: number }) {
  const [status, setStatus] = useState<TelemetryStatus | null>(null)
  const [devices, setDevices] = useState<TelemetryDevice[]>([])
  const [openIncidents, setOpenIncidents] = useState<DeviceIncident[]>([])
  const [history, setHistory] = useState<DeviceIncident[]>([])
  const [tab, setTab] = useState<HistoryTab>('open')
  const [acknowledgingId, setAcknowledgingId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const { lastEvent } = useLiveEvents()
  // Loads overlap (poll, live event, acknowledge, tab switch) and may settle
  // out of order. Only a load newer than the last applied one may set state,
  // and its history only if its tab is still the selected one — otherwise a
  // slow "open" response could fill the Resolved tab until the next poll.
  const tabRef = useRef<HistoryTab>(tab)
  const startedSeq = useRef(0)
  const appliedSeq = useRef(0)

  // Settled independently, like LiveTelemetryPanel: one failing request only
  // degrades its own block. Open incidents are fetched separately from the
  // history tab because the devices table needs them whatever tab is shown.
  const load = useCallback(async (historyTab: HistoryTab) => {
    const seq = ++startedSeq.current
    const [s, d, o, h] = await Promise.allSettled([
      api.getTelemetryStatus(),
      api.getTelemetryDevices(),
      api.getDeviceIncidents({ status: 'open' }),
      api.getDeviceIncidents(historyTab === 'all' ? {} : { status: historyTab }),
    ])
    if (seq < appliedSeq.current) return
    appliedSeq.current = seq
    if (s.status === 'fulfilled') setStatus(s.value)
    if (d.status === 'fulfilled') setDevices(d.value)
    if (o.status === 'fulfilled') setOpenIncidents(o.value)
    if (h.status === 'fulfilled' && historyTab === tabRef.current) setHistory(h.value)
    const failed = [s, d, o, h].find((r): r is PromiseRejectedResult => r.status === 'rejected')
    setError(failed ? (failed.reason instanceof Error ? failed.reason.message : 'Failed to load devices') : null)
    setNow(Date.now())
  }, [])

  useEffect(() => {
    // `active` guards against a poll resolving after unmount (route change).
    let active = true
    const tick = () => {
      if (active) void load(tab)
    }
    tick()
    const id = setInterval(tick, pollMs)
    return () => {
      active = false
      clearInterval(id)
    }
  }, [load, pollMs, tab])

  useEffect(() => {
    // Alert events don't change node state; only device events re-fetch.
    if (lastEvent && isDeviceEvent(lastEvent)) void load(tab)
  }, [lastEvent, load, tab])

  async function handleAcknowledge(incidentId: number) {
    setAcknowledgingId(incidentId)
    try {
      const incident = await api.acknowledgeDeviceIncident(incidentId)
      toast.success(`Incident on ${incident.device_id} acknowledged`)
      await load(tabRef.current)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to acknowledge incident')
    } finally {
      setAcknowledgingId(null)
    }
  }

  function selectTab(next: HistoryTab) {
    tabRef.current = next
    setTab(next)
  }

  const incidentsById = useMemo(() => new Map(openIncidents.map((i) => [i.id, i])), [openIncidents])
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const d of devices) c[d.state] = (c[d.state] ?? 0) + 1
    return c
  }, [devices])
  const warning = watchdogWarning(status)

  return (
    <div className="rise-children space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-text">Sensor nodes</h1>
          <p className="text-xs text-text-muted">
            Heartbeat state of every ESP32 / simulator node · refreshes every {Math.round(pollMs / 1000)} s
          </p>
        </div>
        <ul aria-label="Node states" className="flex flex-wrap gap-2">
          {STATE_ORDER.map((state) => {
            const label = deviceStateLabel(state)
            const n = counts[state] ?? 0
            return (
              <li key={state} aria-label={`${n} ${label.toLowerCase()}`}>
                <Badge variant={n > 0 ? deviceStateTone(state) : 'neutral'}>
                  {label} <span className="font-mono tabular-nums">{n}</span>
                </Badge>
              </li>
            )
          })}
        </ul>
      </div>

      {warning && (
        <div
          role="status"
          className="rounded-2xl border border-degrading/40 bg-degrading/10 p-3 text-sm text-degrading backdrop-blur"
        >
          {warning}
        </div>
      )}

      {error && (
        <div role="alert" className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">
          {error}
        </div>
      )}

      <DevicesTable
        devices={devices}
        now={now}
        showHealth
        linkMachines
        incidents={incidentsById}
        onAcknowledge={handleAcknowledge}
        acknowledgingId={acknowledgingId}
      />

      <section aria-labelledby="incident-history-heading" className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="incident-history-heading" className="text-sm font-semibold uppercase tracking-wide text-text-muted">
            Incident history
          </h2>
          <div role="tablist" aria-label="Incident history" className="flex gap-1 rounded-full bg-white/5 p-1">
            {HISTORY_TABS.map((t) => (
              <button
                key={t.value}
                type="button"
                role="tab"
                aria-selected={tab === t.value}
                aria-controls="incident-history-panel"
                onClick={() => selectTab(t.value)}
                className={
                  tab === t.value
                    ? 'rounded-full bg-accent/15 px-3 py-1 text-xs font-medium text-accent ring-1 ring-inset ring-accent/30'
                    : 'rounded-full px-3 py-1 text-xs text-text-muted transition hover:text-text'
                }
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <Card id="incident-history-panel" role="tabpanel" className="panel-notch overflow-x-auto p-0">
          <table aria-label="Device incidents" className="w-full text-left text-sm">
            <thead className="bg-white/[0.04]">
              <tr className="text-xs uppercase text-text-muted">
                <th className="px-3 py-2">Device</th>
                <th className="px-3 py-2">Machine</th>
                <th className="px-3 py-2">Kind</th>
                <th className="px-3 py-2">Opened</th>
                <th className="px-3 py-2">Last heard</th>
                <th className="px-3 py-2">Resolved</th>
                <th className="px-3 py-2">Acknowledged</th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-3 py-4 text-text-muted">
                    No incidents match this filter.
                  </td>
                </tr>
              ) : (
                history.map((i) => (
                  <tr key={i.id} className="border-t border-white/10">
                    <td className="px-3 py-2 font-mono text-text">{i.device_id}</td>
                    <td className="px-3 py-2 font-mono text-text-muted">{i.machine_id ?? '—'}</td>
                    <td className={i.status === 'open' ? 'px-3 py-2 text-critical' : 'px-3 py-2 text-text-muted'}>
                      {KIND_LABEL[i.kind] ?? i.kind}
                    </td>
                    <td className="px-3 py-2 text-text-muted" title={i.opened_at}>
                      {formatRelative(i.opened_at, now)}
                    </td>
                    <td className="px-3 py-2 text-text-muted" title={i.last_seen_at ?? undefined}>
                      {formatRelative(i.last_seen_at, now)}
                    </td>
                    <td className="px-3 py-2 text-text-muted" title={i.resolved_at ?? undefined}>
                      {i.resolved_at ? formatRelative(i.resolved_at, now) : 'Still open'}
                    </td>
                    <td className="px-3 py-2">
                      {i.acknowledged_at ? (
                        <span className="text-xs text-text-muted" title={i.acknowledged_at}>
                          {formatRelative(i.acknowledged_at, now)}
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={acknowledgingId === i.id}
                          aria-label={`Acknowledge incident ${i.id} (${i.device_id})`}
                          onClick={() => void handleAcknowledge(i.id)}
                          className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-text backdrop-blur transition hover:border-accent/50 hover:bg-white/10 disabled:opacity-50"
                        >
                          {acknowledgingId === i.id ? 'Acknowledging…' : 'Acknowledge'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </Card>
      </section>
    </div>
  )
}
