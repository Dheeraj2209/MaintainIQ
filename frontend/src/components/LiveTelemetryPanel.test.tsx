import { render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { LiveTelemetryPanel } from './LiveTelemetryPanel'
import { formatDuration, formatPercent, formatRelative } from '../lib/telemetryFormat'
import { server } from '../test/server'
import {
  kpiDetailWith,
  systemKpisAvailable,
  telemetryStatusDisabled,
} from '../test/fixtures'

// Card label → its MetricCard root, so assertions stay scoped to one KPI.
function kpiCard(label: string) {
  return screen.getByText(label).parentElement as HTMLElement
}

describe('LiveTelemetryPanel', () => {
  it('shows the connected ingest status, broker and counters', async () => {
    render(<LiveTelemetryPanel />)

    expect(await screen.findByLabelText(/mqtt ingest connected to broker/i)).toBeInTheDocument()
    expect(screen.getByText('mosquitto:1883')).toBeInTheDocument()
    expect(screen.getByText('maintainiq/v1')).toBeInTheDocument()
    expect(screen.getByText('Received').nextElementSibling).toHaveTextContent('412')
    expect(screen.getByText('Duplicates').nextElementSibling).toHaveTextContent('4')
    expect(screen.getByText('Reconnects').nextElementSibling).toHaveTextContent('2')
  })

  it('explains how to start ingest when it is disabled', async () => {
    server.use(http.get('/api/telemetry/status', () => HttpResponse.json(telemetryStatusDisabled)))
    render(<LiveTelemetryPanel />)

    expect(await screen.findByLabelText(/mqtt ingest disabled/i)).toBeInTheDocument()
    expect(screen.getByText(/mqtt ingest is not running/i)).toBeInTheDocument()
    expect(screen.getByText('just broker')).toBeInTheDocument()
    expect(screen.getByText('MQTT_BROKER_HOST=localhost')).toBeInTheDocument()
    expect(screen.getByText('just simulate')).toBeInTheDocument()
    // Counters are hidden while disabled — they would only ever read zero.
    expect(screen.queryByText('Received')).not.toBeInTheDocument()
  })

  it('flags an enabled-but-disconnected service', async () => {
    server.use(
      http.get('/api/telemetry/status', () =>
        HttpResponse.json({ ...telemetryStatusDisabled, enabled: true, broker: 'localhost:1883' }),
      ),
    )
    render(<LiveTelemetryPanel />)
    expect(await screen.findByLabelText(/enabled but disconnected/i)).toHaveTextContent(/disconnected/i)
  })

  it('renders one device row per device with online dot, buffer bar, dropped, RSSI and firmware', async () => {
    render(<LiveTelemetryPanel />)

    const table = await screen.findByRole('table', { name: /telemetry devices/i })
    const row1 = (await within(table).findByText('simdev-01')).closest('tr')!
    const row2 = within(table).getByText('simdev-02').closest('tr')!

    expect(within(row1).getByRole('img', { name: 'online' })).toBeInTheDocument()
    expect(within(row1).getByText('sim-01')).toBeInTheDocument()
    expect(within(row1).getByText('-61 dBm')).toBeInTheDocument()
    expect(within(row1).getByText('maintainiq-sim/0.1.0')).toBeInTheDocument()

    expect(within(row2).getByRole('img', { name: 'offline' })).toBeInTheDocument()
    const bar = within(row2).getByRole('progressbar', { name: /edge buffer simdev-02: 47 of 50/i })
    expect(bar).toHaveAttribute('aria-valuenow', '47')
    expect(bar).toHaveAttribute('aria-valuemax', '50')
    expect(within(row2).getByText('6')).toBeInTheDocument()
    expect(within(row2).getByText('-82 dBm')).toBeInTheDocument()
  })

  it('shows an empty state when no device has reported', async () => {
    server.use(http.get('/api/telemetry/devices', () => HttpResponse.json([])))
    render(<LiveTelemetryPanel />)
    expect(await screen.findByText(/no devices have reported yet/i)).toBeInTheDocument()
  })

  it('renders the not_applicable reason on every system KPI before any telemetry arrives', async () => {
    render(<LiveTelemetryPanel />)

    await waitFor(() => expect(screen.getAllByText('N/A')).toHaveLength(4))
    for (const label of ['Sensor collection rate', 'Transmission success', 'Edge buffer health', 'Cloud sync health']) {
      expect(within(kpiCard(label)).getByText(/no live telemetry received yet/i)).toBeInTheDocument()
    }
  })

  it('renders the available system KPI payloads', async () => {
    server.use(http.get('/api/kpis/detail', () => HttpResponse.json(kpiDetailWith(systemKpisAvailable))))
    render(<LiveTelemetryPanel />)

    await waitFor(() => expect(within(kpiCard('Sensor collection rate')).getByText('97.3%')).toBeInTheDocument())
    expect(within(kpiCard('Sensor collection rate')).getByText(/2 device\(s\) · last 60 min/)).toBeInTheDocument()

    const tx = within(kpiCard('Transmission success'))
    expect(tx.getByText('90.0%')).toBeInTheDocument()
    expect(tx.getByText(/900\/1000 received · 100 lost · device-reported failures 1\.8%/)).toBeInTheDocument()

    const buf = within(kpiCard('Edge buffer health'))
    expect(buf.getByText('Critical')).toBeInTheDocument()
    expect(buf.getByText(/buffered share 12\.5% · 6 dropped/)).toBeInTheDocument()

    const sync = within(kpiCard('Cloud sync health'))
    expect(sync.getByText('Degraded')).toBeInTheDocument()
    expect(sync.getByText(/lag p50 0\.42 s \/ p95 1\.80 s · 1\/2 online/)).toBeInTheDocument()
    expect(sync.getByText(/rejected 0\.7%/)).toBeInTheDocument()

    expect(screen.queryByText('N/A')).not.toBeInTheDocument()
  })

  it('keeps the other blocks when one endpoint fails and surfaces the error', async () => {
    server.use(
      http.get('/api/telemetry/devices', () => HttpResponse.json({ detail: 'devices unavailable' }, { status: 500 })),
    )
    render(<LiveTelemetryPanel />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/devices unavailable/i)
    expect(screen.getByLabelText(/mqtt ingest connected to broker/i)).toBeInTheDocument()
  })

  it('polls the telemetry endpoints on an interval', async () => {
    let calls = 0
    server.use(
      http.get('/api/telemetry/status', () => {
        calls += 1
        return HttpResponse.json(telemetryStatusDisabled)
      }),
    )
    render(<LiveTelemetryPanel pollMs={40} />)
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(3))
  })
})

describe('telemetry formatters', () => {
  const now = Date.parse('2026-10-06T12:00:00Z')

  it('formats relative times', () => {
    expect(formatRelative(null, now)).toBe('—')
    expect(formatRelative('2026-10-06T11:59:58Z', now)).toBe('just now')
    expect(formatRelative('2026-10-06T11:59:30Z', now)).toBe('30 s ago')
    expect(formatRelative('2026-10-06T11:50:00Z', now)).toBe('10 min ago')
    expect(formatRelative('2026-10-06T09:00:00Z', now)).toBe('3 h ago')
    expect(formatRelative('2026-10-04T12:00:00Z', now)).toBe('2 d ago')
    // Device clock ahead of ours must not render a negative age.
    expect(formatRelative('2026-10-06T12:05:00Z', now)).toBe('just now')
  })

  it('formats durations', () => {
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(4.2)).toBe('4 s')
    expect(formatDuration(59.6)).toBe('1 min')
    expect(formatDuration(600)).toBe('10 min')
    expect(formatDuration(3 * 3600 + 120)).toBe('3 h 2 min')
    expect(formatDuration(2 * 86400)).toBe('2 d')
  })

  it('formats percentages', () => {
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(0.5)).toBe('50.0%')
  })
})
