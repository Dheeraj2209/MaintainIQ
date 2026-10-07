// Sensor-node table shared by the Ingestion page's live-telemetry section
// (compact: the M6 columns) and the Devices page (`showHealth`: adds the state
// chip, silent-for, heartbeat and incident columns from
// design/2026-10-06-device-health-design.md).
//
// The leading dot's accessible name is the device state in lower case
// ("online", "stale", "offline", "never reported"), so screen readers get the
// state even in the compact layout, which has no state column.
import { Link } from 'react-router-dom'
import type { DeviceIncident, TelemetryDevice } from '../api/types'
import { cn } from '../lib/cn'
import { formatDuration, formatRelative } from '../lib/telemetryFormat'
import { deviceStateClasses, deviceStateLabel, deviceStateTone } from './healthStyles'
import { Badge } from './ui/badge'
import { Card } from './ui/card'

function BufferBar({ device }: { device: TelemetryDevice }) {
  const depth = device.buffer_depth
  const capacity = device.buffer_capacity
  if (depth == null || capacity == null || capacity <= 0) {
    return <span className="text-text-muted">—</span>
  }
  const utilization = Math.min(1, Math.max(0, depth / capacity))
  // Same thresholds as the edge_buffer_health KPI (contract §9).
  const fill = utilization >= 0.9 ? 'bg-critical' : utilization >= 0.5 ? 'bg-degrading' : 'bg-healthy'
  return (
    <div className="flex items-center gap-2">
      <div
        role="progressbar"
        aria-label={`Edge buffer ${device.device_id}: ${depth} of ${capacity} snapshots`}
        aria-valuemin={0}
        aria-valuemax={capacity}
        aria-valuenow={depth}
        className="h-1.5 w-20 overflow-hidden rounded-full bg-white/10"
      >
        <div className={cn('h-full rounded-full', fill)} style={{ width: `${utilization * 100}%` }} />
      </div>
      <span className="font-mono text-xs text-text-muted">
        {depth}/{capacity}
      </span>
    </div>
  )
}

interface IncidentCellProps {
  device: TelemetryDevice
  incident: DeviceIncident | undefined
  now: number
  onAcknowledge?: (incidentId: number) => void
  acknowledging: boolean
}

function IncidentCell({ device, incident, now, onAcknowledge, acknowledging }: IncidentCellProps) {
  const id = device.open_incident_id
  if (id == null) return <span className="text-text-muted">—</span>
  // The incident row can lag the device row by one poll; the id alone is
  // enough to acknowledge, so only the "since" text waits for it.
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-critical" title={incident?.opened_at}>
        {incident ? `Open since ${formatRelative(incident.opened_at, now)}` : 'Open'}
      </span>
      {incident?.acknowledged_at ? (
        <span className="text-xs text-text-muted">Acknowledged</span>
      ) : (
        onAcknowledge && (
          <button
            type="button"
            disabled={acknowledging}
            aria-label={`Acknowledge incident ${id} (${device.device_id})`}
            onClick={() => onAcknowledge(id)}
            className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-text backdrop-blur transition hover:border-accent/50 hover:bg-white/10 disabled:opacity-50"
          >
            {acknowledging ? 'Acknowledging…' : 'Acknowledge'}
          </button>
        )
      )}
    </div>
  )
}

export interface DevicesTableProps {
  devices: TelemetryDevice[]
  now: number
  // Devices-page columns: state chip, silent for, heartbeat, incident.
  showHealth?: boolean
  // Render machine ids as links to /machines/:id (needs a router above).
  linkMachines?: boolean
  // Open incidents by id, for the incident cell's "since" / acknowledged state.
  incidents?: ReadonlyMap<number, DeviceIncident>
  onAcknowledge?: (incidentId: number) => void
  acknowledgingId?: number | null
}

export function DevicesTable({
  devices,
  now,
  showHealth = false,
  linkMachines = false,
  incidents,
  onAcknowledge,
  acknowledgingId = null,
}: DevicesTableProps) {
  const columns = showHealth ? 11 : 7
  return (
    <Card className="panel-notch overflow-x-auto p-0">
      <table aria-label="Telemetry devices" className="w-full text-left text-sm">
        <thead className="bg-white/[0.04]">
          <tr className="text-xs uppercase text-text-muted">
            {showHealth && <th className="px-3 py-2">State</th>}
            <th className="px-3 py-2">Device</th>
            <th className="px-3 py-2">Machine</th>
            <th className="px-3 py-2">Last seen</th>
            {showHealth && <th className="px-3 py-2">Silent for</th>}
            {showHealth && <th className="px-3 py-2">Heartbeat</th>}
            <th className="px-3 py-2">Edge buffer</th>
            <th className="px-3 py-2">Dropped</th>
            <th className="px-3 py-2">RSSI</th>
            <th className="px-3 py-2">Firmware</th>
            {showHealth && <th className="px-3 py-2">Incident</th>}
          </tr>
        </thead>
        <tbody>
          {devices.length === 0 ? (
            <tr>
              <td colSpan={columns} className="px-3 py-4 text-text-muted">
                No devices have reported yet.
              </td>
            </tr>
          ) : (
            devices.map((d) => {
              const sc = deviceStateClasses(d.state)
              const label = deviceStateLabel(d.state)
              return (
                <tr key={d.device_id} className="border-t border-white/10">
                  {showHealth && (
                    <td className="px-3 py-2">
                      <Badge variant={deviceStateTone(d.state)}>{label}</Badge>
                    </td>
                  )}
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-2 font-mono text-text">
                      <span
                        role="img"
                        aria-label={label.toLowerCase()}
                        title={label}
                        className={cn(
                          'inline-block h-2 w-2 shrink-0 rounded-full',
                          sc.dot,
                          d.state === 'online' && 'shadow-[0_0_8px_var(--color-healthy)]',
                        )}
                      />
                      {d.device_id}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-text-muted">
                    {d.machine_id && linkMachines ? (
                      <Link
                        to={`/machines/${encodeURIComponent(d.machine_id)}`}
                        className="text-accent hover:text-accent-hover"
                      >
                        {d.machine_id}
                      </Link>
                    ) : (
                      (d.machine_id ?? '—')
                    )}
                  </td>
                  <td className="px-3 py-2 text-text-muted" title={d.last_seen_at}>
                    {formatRelative(d.last_seen_at, now)}
                  </td>
                  {showHealth && (
                    <td className={cn('px-3 py-2 font-mono', d.state === 'online' ? 'text-text-muted' : sc.text)}>
                      {formatDuration(d.silent_for_s)}
                    </td>
                  )}
                  {showHealth && (
                    <td className="px-3 py-2 font-mono text-text-muted">{formatDuration(d.expected_heartbeat_s)}</td>
                  )}
                  <td className="px-3 py-2">
                    <BufferBar device={d} />
                  </td>
                  <td className={cn('px-3 py-2 font-mono', d.buffer_dropped_total ? 'text-degrading' : 'text-text-muted')}>
                    {d.buffer_dropped_total ?? '—'}
                  </td>
                  <td className="px-3 py-2 font-mono text-text-muted">
                    {d.wifi_rssi_dbm != null ? `${Math.round(d.wifi_rssi_dbm)} dBm` : '—'}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-text-muted">{d.firmware ?? '—'}</td>
                  {showHealth && (
                    <td className="px-3 py-2">
                      <IncidentCell
                        device={d}
                        incident={d.open_incident_id != null ? incidents?.get(d.open_incident_id) : undefined}
                        now={now}
                        onAcknowledge={onAcknowledge}
                        acknowledging={acknowledgingId != null && acknowledgingId === d.open_incident_id}
                      />
                    </td>
                  )}
                </tr>
              )
            })
          )}
        </tbody>
      </table>
    </Card>
  )
}
