import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { DevicesTable } from './DevicesTable'
import type { DeviceIncident, DeviceState, TelemetryDevice } from '../api/types'
import { deviceIncidents, telemetryDevices } from '../test/fixtures'

const NOW = Date.parse('2026-10-06T12:00:00Z')

function device(id: string, state: DeviceState, extra: Partial<TelemetryDevice> = {}): TelemetryDevice {
  return { ...structuredClone(telemetryDevices[0]), device_id: id, state, ...extra }
}

function incidentsById(list: DeviceIncident[]): Map<number, DeviceIncident> {
  return new Map(list.map((i) => [i.id, i]))
}

describe('DevicesTable', () => {
  it('labels the state dot with the lower-case state for every state', () => {
    const devices = [
      device('n-online', 'online'),
      device('n-stale', 'stale'),
      device('n-offline', 'offline'),
      device('n-never', 'never_reported'),
    ]
    render(<DevicesTable devices={devices} now={NOW} />)

    const table = screen.getByRole('table', { name: /telemetry devices/i })
    const rowOf = (id: string) => within(table).getByText(id).closest('tr')!
    expect(within(rowOf('n-online')).getByRole('img', { name: 'online' })).toBeInTheDocument()
    expect(within(rowOf('n-stale')).getByRole('img', { name: 'stale' })).toBeInTheDocument()
    expect(within(rowOf('n-offline')).getByRole('img', { name: 'offline' })).toBeInTheDocument()
    expect(within(rowOf('n-never')).getByRole('img', { name: 'never reported' })).toBeInTheDocument()
  })

  it('keeps the compact column set unless health columns are requested', () => {
    render(<DevicesTable devices={telemetryDevices} now={NOW} />)
    expect(screen.queryByRole('columnheader', { name: /incident/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /silent for/i })).not.toBeInTheDocument()
  })

  it('shows the state chip, silent-for and an Acknowledge button for an open, unacknowledged incident', async () => {
    const onAcknowledge = vi.fn()
    render(
      <MemoryRouter>
        <DevicesTable
          devices={telemetryDevices}
          now={NOW}
          showHealth
          linkMachines
          incidents={incidentsById(deviceIncidents)}
          onAcknowledge={onAcknowledge}
        />
      </MemoryRouter>,
    )

    const row = screen.getByText('simdev-02').closest('tr')!
    expect(within(row).getByText('Offline')).toBeInTheDocument()
    expect(within(row).getByText('10 min')).toBeInTheDocument()
    expect(within(row).getByText(/open since 9 min ago/i)).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'sim-02' })).toHaveAttribute('href', '/machines/sim-02')

    await userEvent.click(within(row).getByRole('button', { name: /acknowledge incident 1/i }))
    expect(onAcknowledge).toHaveBeenCalledWith(1)

    // simdev-01 has no open incident.
    const healthy = screen.getByText('simdev-01').closest('tr')!
    expect(within(healthy).queryByRole('button', { name: /acknowledge/i })).not.toBeInTheDocument()
  })

  it('shows "Acknowledged" instead of the button once the open incident is acknowledged', () => {
    const acked = incidentsById([{ ...deviceIncidents[0], acknowledged_at: '2026-10-06T11:55:00Z', acknowledged_by: 1 }])
    render(<DevicesTable devices={telemetryDevices} now={NOW} showHealth incidents={acked} onAcknowledge={vi.fn()} />)

    const row = screen.getByText('simdev-02').closest('tr')!
    expect(within(row).getByText('Acknowledged')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /acknowledge/i })).not.toBeInTheDocument()
  })

  it('disables the button while that incident is being acknowledged', () => {
    render(
      <DevicesTable
        devices={telemetryDevices}
        now={NOW}
        showHealth
        incidents={incidentsById(deviceIncidents)}
        onAcknowledge={vi.fn()}
        acknowledgingId={1}
      />,
    )
    expect(screen.getByRole('button', { name: /acknowledge incident 1/i })).toBeDisabled()
  })

  it('shows an empty state', () => {
    render(<DevicesTable devices={[]} now={NOW} showHealth />)
    expect(screen.getByText(/no devices have reported yet/i)).toBeInTheDocument()
  })
})
