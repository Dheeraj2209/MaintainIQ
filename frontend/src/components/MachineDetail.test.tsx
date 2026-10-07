import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../auth/AuthContext'
import { MachineDetail } from './MachineDetail'
import { server } from '../test/server'
import { api } from '../api/client'
import {
  commissioningMachine,
  heldMachine,
  machineDetail,
  machineDetailWithFullHistory,
  maintenanceHistoryPage2,
  operatorUser,
  resolvedAlertWithFeedback,
  telemetryDevices,
} from '../test/fixtures'
import type { Alert } from '../api/types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }))

function harness(
  machineId = 'm1',
  onCreateWorkOrder?: (alert: Alert | null) => void,
  onRecordOutcome?: (alert: Alert) => void,
) {
  return (
    <AuthProvider>
      <MachineDetail machineId={machineId} onCreateWorkOrder={onCreateWorkOrder} onRecordOutcome={onRecordOutcome} />
    </AuthProvider>
  )
}

describe('MachineDetail', () => {
  it('shows the sensor-node state and device id for a machine with a node', async () => {
    render(harness('sim-02'))

    const fact = await screen.findByText('Sensor node')
    await waitFor(() => expect(fact.nextElementSibling).toHaveTextContent('Offline · simdev-02'))
  })

  it('picks the worst state across several nodes on one machine', async () => {
    server.use(
      http.get('/api/telemetry/devices', () =>
        HttpResponse.json([
          { ...telemetryDevices[0], device_id: 'a', machine_id: 'm1', state: 'online' },
          { ...telemetryDevices[0], device_id: 'b', machine_id: 'm1', state: 'stale' },
          { ...telemetryDevices[0], device_id: 'c', machine_id: 'm1', state: 'never_reported' },
        ]),
      ),
    )
    render(harness())

    const fact = await screen.findByText('Sensor node')
    await waitFor(() => expect(fact.nextElementSibling).toHaveTextContent('Stale · b'))
  })

  it('shows a dash for a machine without a sensor node', async () => {
    let requested = ''
    server.use(
      http.get('/api/telemetry/devices', ({ request }) => {
        requested = request.url
        return HttpResponse.json([])
      }),
    )
    render(harness())

    const fact = await screen.findByText('Sensor node')
    await waitFor(() => expect(requested).toContain('machine_id=m1'))
    expect(fact.nextElementSibling).toHaveTextContent(/^—$/)
  })

  it('shows a dash when the device lookup fails', async () => {
    let calls = 0
    server.use(
      http.get('/api/telemetry/devices', () => {
        calls += 1
        return HttpResponse.json({ detail: 'boom' }, { status: 500 })
      }),
    )
    render(harness('sim-02'))

    const fact = await screen.findByText('Sensor node')
    await waitFor(() => expect(calls).toBe(1))
    await new Promise((r) => setTimeout(r, 20))
    expect(fact.nextElementSibling).toHaveTextContent(/^—$/)
  })

  it('loads and shows health facts for the machine', async () => {
    render(harness())

    expect(await screen.findByRole('heading', { name: /m1/ })).toBeInTheDocument()
    expect(screen.getAllByText(/critical/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/bearing_wear/)).toBeInTheDocument()
    // risk score surfaced somewhere
    expect(screen.getByText(/100/)).toBeInTheDocument()
  })

  it('renders a metric selector and the trend chart', async () => {
    render(harness())

    const select = await screen.findByLabelText(/metric/i)
    expect(select).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: /vibration_h_rms/i })).toBeInTheDocument()
  })

  it('refetches trends when the metric changes', async () => {
    const spy = vi.spyOn(api, 'getTrends')
    render(harness())

    const select = await screen.findByLabelText(/metric/i)
    await userEvent.selectOptions(select, 'vibration_h_kurtosis')

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('m1', 'vibration_h_kurtosis', expect.anything()),
    )
    spy.mockRestore()
  })

  it('surfaces the predicted RUL health fact', async () => {
    render(harness())

    expect(await screen.findByText(/predicted rul \(min\)/i, { selector: 'dt' })).toBeInTheDocument()
    expect(screen.getByText('42.5')).toBeInTheDocument()
  })

  it('logs maintenance and raises a success toast', async () => {
    render(harness())

    await screen.findByRole('heading', { name: /m1/ })
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.type(screen.getByLabelText(/description/i), 'greased bearing')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(await screen.findByText(/logged/i)).toBeInTheDocument()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Maintenance logged'))
  })

  it('surfaces a load error', async () => {
    server.use(
      http.get('/api/machines/:id', () =>
        HttpResponse.json({ detail: 'machine not found' }, { status: 404 }),
      ),
    )
    render(harness('mX'))
    expect(await screen.findByText(/not found/i)).toBeInTheDocument()
  })

  it('selecting an alert links it into the form and clears on submit', async () => {
    render(harness())

    await userEvent.click(await screen.findByText('m1 critical'))
    expect(await screen.findByText(/logging maintenance for alert #2/i)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    await screen.findByText(/logged/i)
    expect(screen.queryByText(/logging maintenance for alert #2/i)).not.toBeInTheDocument()
  })

  it('shows a Type column and a Load more button when a full page of history is returned', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json(machineDetailWithFullHistory)))
    render(harness())

    expect(await screen.findByRole('columnheader', { name: /type/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument()
  })

  it('appends more history rows and hides the button once a short page comes back', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json(machineDetailWithFullHistory)))
    server.use(
      http.get('/api/maintenance/:id', ({ request }) => {
        const url = new URL(request.url)
        expect(url.searchParams.get('offset')).toBe('10')
        return HttpResponse.json(maintenanceHistoryPage2)
      }),
    )
    render(harness())

    await screen.findByRole('button', { name: /load more/i })
    // 1 header row + 10 initial history rows
    expect(screen.getAllByRole('row')).toHaveLength(11)

    await userEvent.click(screen.getByRole('button', { name: /load more/i }))

    // 1 header row + 10 initial + 1 appended
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(12))
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  it('recovers gracefully when loading more history fails', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json(machineDetailWithFullHistory)))
    server.use(http.get('/api/maintenance/:id', () => HttpResponse.error()))
    render(harness())

    await screen.findByRole('button', { name: /load more/i })
    // 1 header row + 10 initial history rows
    expect(screen.getAllByRole('row')).toHaveLength(11)

    await userEvent.click(screen.getByRole('button', { name: /load more/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(11)
  })

  it('shows the open work order count and average completion time', async () => {
    render(harness())

    const open = await screen.findByText('Open work orders')
    expect(open.nextElementSibling).toHaveTextContent(/^1$/)
    expect(screen.getByText('Avg WO completion (h)').nextElementSibling).toHaveTextContent('2.50')
  })

  it('creates a work order for the selected alert', async () => {
    const onCreate = vi.fn()
    render(harness('m1', onCreate))

    await userEvent.click(await screen.findByText('m1 critical'))
    await userEvent.click(screen.getByRole('button', { name: /create work order/i }))

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }))
  })

  it('creates a free-standing work order for an admin with no alert selected', async () => {
    const onCreate = vi.fn()
    render(harness('m1', onCreate))
    await screen.findByText('m1 critical')

    const button = await screen.findByRole('button', { name: /create work order/i })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    expect(onCreate).toHaveBeenCalledWith(null)
  })

  it('disables the button for an operator until an alert is selected', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    const onCreate = vi.fn()
    render(harness('m1', onCreate))
    await screen.findByText('m1 critical')
    await new Promise((r) => setTimeout(r, 50))

    const button = screen.getByRole('button', { name: /create work order/i })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', 'Select an alert first')

    await userEvent.click(screen.getByText('m1 critical'))
    expect(screen.getByRole('button', { name: /create work order/i })).toBeEnabled()
  })

  it('points at the open work order when the selected alert already has one', async () => {
    server.use(
      http.get('/api/machines/:id', () =>
        HttpResponse.json({
          ...machineDetail,
          alerts: [{ ...machineDetail.alerts[0], active_work_order_id: 7 }],
        }),
      ),
    )
    render(harness('m1', vi.fn()))

    await userEvent.click(await screen.findByText('m1 critical'))
    expect(screen.getByRole('button', { name: /wo #7 open/i })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /create work order/i })).not.toBeInTheDocument()
  })

  it('has no work-order button without a handler', async () => {
    render(harness())
    await screen.findByText('m1 critical')
    expect(screen.queryByRole('button', { name: /create work order/i })).not.toBeInTheDocument()
  })

  describe('close / record outcome', () => {
    function serveAlerts(alerts: Alert[]) {
      server.use(http.get('/api/machines/:id', () => HttpResponse.json({ ...structuredClone(machineDetail), alerts })))
    }

    it('is disabled until an alert is selected', async () => {
      render(harness('m1', undefined, vi.fn()))
      await screen.findByText('m1 critical')

      const button = screen.getByRole('button', { name: /close \/ record outcome/i })
      expect(button).toBeDisabled()
      expect(button).toHaveAttribute('title', 'Select an alert first')
    })

    it('reads "Close alert" for an open alert and hands it to the handler', async () => {
      const onRecord = vi.fn()
      render(harness('m1', undefined, onRecord))

      await userEvent.click(await screen.findByText('m1 critical'))
      await userEvent.click(screen.getByRole('button', { name: /^close alert$/i }))
      expect(onRecord).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }))
    })

    it('reads "Record outcome" for a resolved alert without feedback', async () => {
      serveAlerts([{ ...structuredClone(resolvedAlertWithFeedback), feedback: null }])
      render(harness('m1', undefined, vi.fn()))

      await userEvent.click(await screen.findByText('m1 faulty'))
      expect(screen.getByRole('button', { name: /^record outcome$/i })).toBeEnabled()
    })

    it('reads "Edit outcome", disabled for an operator who did not record it', async () => {
      serveAlerts([structuredClone(resolvedAlertWithFeedback)])
      server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
      render(harness('m1', undefined, vi.fn()))
      await new Promise((r) => setTimeout(r, 50))

      await userEvent.click(await screen.findByText('m1 faulty'))
      expect(screen.getByRole('button', { name: /^edit outcome$/i })).toBeDisabled()
    })

    it('has no outcome button without a handler', async () => {
      render(harness())
      await userEvent.click(await screen.findByText('m1 critical'))
      expect(screen.queryByRole('button', { name: /close alert|record outcome/i })).not.toBeInTheDocument()
    })
  })
})

describe('MachineDetail health ratchet', () => {
  it('shows the held badge, replaces the RUL with the held state and lists the model warnings', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json({ ...machineDetail, health: heldMachine })))
    render(harness())

    expect(await screen.findByText(/held since/i)).toBeInTheDocument()
    const rul = screen.getByText('Predicted RUL (min)', { selector: 'dt' })
    expect(rul.nextElementSibling).toHaveTextContent('Held: Critical (current signal: Healthy)')
    expect(screen.getByText(/condition_receded: instant state healthy/)).toBeInTheDocument()
  })

  it('shows commissioning progress instead of a healthy badge', async () => {
    server.use(http.get('/api/machines/:id', () => HttpResponse.json({ ...machineDetail, health: commissioningMachine })))
    render(harness())

    expect(await screen.findByText('Commissioning 7/20')).toBeInTheDocument()
    expect(screen.getByText('Signal: Faulty')).toBeInTheDocument()
    expect(screen.queryByText(/held since/i)).not.toBeInTheDocument()
    expect(screen.getByText(/commissioning: 7\/20 snapshots/)).toBeInTheDocument()
  })
})
