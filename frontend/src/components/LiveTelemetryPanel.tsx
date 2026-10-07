// "Live telemetry (MQTT)" section of the Ingestion page (M6,
// design/M6_LIVE_TELEMETRY.md §8–§9).
//
// Why polling and not the /api/ws stream: the WebSocket only carries alert and
// device online/offline events (src/realtime/manager.py), not counters. Ingest counters, device heartbeats and the
// windowed system KPIs are cheap aggregate reads that change every few
// seconds, so a 5 s poll of three GETs is simpler and matches how often the
// simulator/ESP32 heartbeats actually move these numbers.
//
// The three requests are settled independently: a backend without the
// telemetry routes (or a KPI query that fails) degrades only its own block
// instead of blanking the whole section.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type {
  CloudSyncKpi,
  EdgeBufferKpi,
  HealthTriState,
  KpiNotApplicable,
  KpiOr,
  SensorCollectionKpi,
  SystemKpis,
  TelemetryDevice,
  TelemetryStatus,
  TransmissionSuccessKpi,
} from '../api/types'
import { cn } from '../lib/cn'
import { formatPercent, formatRelative } from '../lib/telemetryFormat'
import { DevicesTable } from './DevicesTable'
import { Badge } from './ui/badge'
import { Card } from './ui/card'
import { MetricCard } from './ui/metric-card'

const TELEMETRY_POLL_MS = 5000

function formatSeconds(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value < 10 ? `${value.toFixed(2)} s` : `${Math.round(value)} s`
}

// System-KPI states → the shared signal palette (healthStyles tones). `down`
// is as loud as `critical`: no device online means no live data at all.
const STATE_TONE: Record<HealthTriState, 'healthy' | 'degrading' | 'critical' | 'unknown'> = {
  ok: 'healthy',
  degraded: 'degrading',
  critical: 'critical',
  down: 'critical',
  unknown: 'unknown',
}

function stateLabel(state: string): string {
  return state.charAt(0).toUpperCase() + state.slice(1)
}

function isNotApplicable<T>(kpi: KpiOr<T> | undefined): kpi is KpiNotApplicable | undefined {
  // Anything not explicitly 'available' (including a missing key) is shown as N/A.
  return !kpi || (kpi as { status: string }).status !== 'available'
}

// ---- KPI cards ----

interface KpiCardSpec {
  label: string
  value: string
  sub: string
  tone: 'healthy' | 'degrading' | 'critical' | 'unknown' | 'accent' | 'accent2'
}

function notApplicableCard(label: string, kpi: KpiNotApplicable | undefined): KpiCardSpec {
  return {
    label,
    value: 'N/A',
    sub: kpi?.reason ?? 'not reported by the backend',
    tone: 'unknown',
  }
}

// Rate KPIs have no explicit state, so tone them off the same thresholds an
// operator would eyeball: ≥ 95 % fine, ≥ 80 % worth a look, below that bad.
function rateTone(rate: number | null): KpiCardSpec['tone'] {
  if (rate == null) return 'unknown'
  if (rate >= 0.95) return 'healthy'
  if (rate >= 0.8) return 'degrading'
  return 'critical'
}

function systemKpiCards(system: SystemKpis | undefined): KpiCardSpec[] {
  const cards: KpiCardSpec[] = []

  const collection = system?.sensor_collection_rate
  if (isNotApplicable<SensorCollectionKpi>(collection)) {
    cards.push(notApplicableCard('Sensor collection rate', collection))
  } else {
    cards.push({
      label: 'Sensor collection rate',
      value: formatPercent(collection.rate),
      sub: `${collection.devices.length} device(s) · last ${collection.window_minutes} min`,
      tone: rateTone(collection.rate),
    })
  }

  const transmission = system?.transmission_success_rate
  if (isNotApplicable<TransmissionSuccessKpi>(transmission)) {
    cards.push(notApplicableCard('Transmission success', transmission))
  } else {
    cards.push({
      label: 'Transmission success',
      value: formatPercent(transmission.rate),
      sub:
        `${transmission.received}/${transmission.expected} received · ${transmission.lost} lost · ` +
        `device-reported failures ${formatPercent(transmission.device_reported_failure_rate)}`,
      tone: rateTone(transmission.rate),
    })
  }

  const buffer = system?.edge_buffer_health
  if (isNotApplicable<EdgeBufferKpi>(buffer)) {
    cards.push(notApplicableCard('Edge buffer health', buffer))
  } else {
    cards.push({
      label: 'Edge buffer health',
      value: stateLabel(buffer.state),
      sub: `buffered share ${formatPercent(buffer.buffered_share)} · ${buffer.dropped_total} dropped`,
      tone: STATE_TONE[buffer.state] ?? 'unknown',
    })
  }

  const sync = system?.cloud_sync_health
  if (isNotApplicable<CloudSyncKpi>(sync)) {
    cards.push(notApplicableCard('Cloud sync health', sync))
  } else {
    cards.push({
      label: 'Cloud sync health',
      value: stateLabel(sync.state),
      sub:
        `lag p50 ${formatSeconds(sync.lag_p50_s)} / p95 ${formatSeconds(sync.lag_p95_s)} · ` +
        `${sync.devices_online}/${sync.devices_total} online · last msg ${formatSeconds(sync.last_message_age_s)} ago · ` +
        `rejected ${formatPercent(sync.rejected_rate)}`,
      tone: STATE_TONE[sync.state] ?? 'unknown',
    })
  }

  return cards
}

// ---- sub-components ----

function IngestStatusBadge({ status }: { status: TelemetryStatus | null }) {
  if (!status) return <Badge variant="unknown">Unknown</Badge>
  if (!status.enabled) {
    return (
      <Badge variant="unknown" aria-label="MQTT ingest disabled">
        Disabled
      </Badge>
    )
  }
  return status.connected ? (
    <Badge variant="healthy" aria-label="MQTT ingest connected to broker">
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-healthy" />
      Connected
    </Badge>
  ) : (
    <Badge variant="critical" aria-label="MQTT ingest enabled but disconnected from broker">
      Disconnected
    </Badge>
  )
}

const STAT_LABELS: [keyof TelemetryStatus['stats'], string][] = [
  ['received', 'Received'],
  ['accepted', 'Accepted'],
  ['rejected', 'Rejected'],
  ['duplicates', 'Duplicates'],
  ['errors', 'Errors'],
  ['queue_overflows', 'Queue overflows'],
  ['queue_depth', 'Queue depth'],
  ['reconnects', 'Reconnects'],
]

// ---- section ----

export function LiveTelemetryPanel({ pollMs = TELEMETRY_POLL_MS }: { pollMs?: number }) {
  const [status, setStatus] = useState<TelemetryStatus | null>(null)
  const [devices, setDevices] = useState<TelemetryDevice[]>([])
  const [system, setSystem] = useState<SystemKpis | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const load = useCallback(async () => {
    const [s, d, k] = await Promise.allSettled([
      api.getTelemetryStatus(),
      api.getTelemetryDevices(),
      api.getKpiDetail(),
    ])
    if (s.status === 'fulfilled') setStatus(s.value)
    if (d.status === 'fulfilled') setDevices(d.value)
    if (k.status === 'fulfilled') setSystem(k.value.system)
    const failed = [s, d, k].find((r): r is PromiseRejectedResult => r.status === 'rejected')
    setError(failed ? (failed.reason instanceof Error ? failed.reason.message : 'Failed to load telemetry') : null)
    setNow(Date.now())
  }, [])

  useEffect(() => {
    // `active` guards against a poll resolving after unmount (route change).
    let active = true
    const tick = () => {
      if (active) void load()
    }
    tick()
    const id = setInterval(tick, pollMs)
    return () => {
      active = false
      clearInterval(id)
    }
  }, [load, pollMs])

  const cards = systemKpiCards(system)

  return (
    <section aria-labelledby="live-telemetry-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="live-telemetry-heading" className="text-lg font-bold text-text">
            Live telemetry (MQTT)
          </h2>
          <p className="text-xs text-text-muted">
            Sensor snapshots from ESP32 nodes or the simulator, through the same predict → alert path as replay ·
            refreshes every {Math.round(pollMs / 1000)} s
          </p>
        </div>
        <IngestStatusBadge status={status} />
      </div>

      {error && (
        <div role="alert" className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">
          {error}
        </div>
      )}

      <Card className="panel-notch p-4">
        {status && !status.enabled ? (
          <div className="space-y-2 text-sm">
            <p className="font-semibold text-text">MQTT ingest is not running.</p>
            <p className="text-text-muted">
              The API only subscribes to the broker when <code className="font-mono text-accent">MQTT_BROKER_HOST</code> is
              set. To get a live feed without hardware:
            </p>
            <ol className="list-decimal space-y-1 pl-5 text-text-muted">
              <li>
                Start the Mosquitto broker: <code className="font-mono text-text">just broker</code>
              </li>
              <li>
                Restart the API with <code className="font-mono text-text">MQTT_BROKER_HOST=localhost</code>
              </li>
              <li>
                Publish simulated sensor data: <code className="font-mono text-text">just simulate</code>
              </li>
            </ol>
          </div>
        ) : (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs uppercase tracking-wide text-text-muted">Broker</dt>
              <dd className="font-mono text-text">{status?.broker ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-text-muted">Topic prefix</dt>
              <dd className="font-mono text-text">{status?.topic_prefix ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-text-muted">Last message</dt>
              <dd className="text-text" title={status?.last_message_at ?? undefined}>
                {formatRelative(status?.last_message_at, now)}
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-text-muted">Started</dt>
              <dd className="text-text" title={status?.started_at ?? undefined}>
                {formatRelative(status?.started_at, now)}
              </dd>
            </div>
            {STAT_LABELS.map(([key, label]) => (
              <div key={key}>
                <dt className="text-xs uppercase tracking-wide text-text-muted">{label}</dt>
                <dd
                  className={cn(
                    'font-mono tabular-nums',
                    (key === 'rejected' || key === 'errors' || key === 'queue_overflows') && (status?.stats[key] ?? 0) > 0
                      ? 'text-critical'
                      : 'text-text',
                  )}
                >
                  {status?.stats[key] ?? '—'}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </Card>

      <div>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">System KPIs</h3>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {cards.map((c) => (
            <MetricCard key={c.label} label={c.label} value={c.value} sub={c.sub} tone={c.tone} />
          ))}
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Devices</h3>
        <DevicesTable devices={devices} now={now} />
      </div>
    </section>
  )
}
