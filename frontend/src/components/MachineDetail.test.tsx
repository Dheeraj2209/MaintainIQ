import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../auth/AuthContext'
import { MachineDetail } from './MachineDetail'
import { server } from '../test/server'
import { api } from '../api/client'
import { machineDetailWithFullHistory, maintenanceHistoryPage2 } from '../test/fixtures'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }))

function harness(machineId = 'm1') {
  return (
    <AuthProvider>
      <MachineDetail machineId={machineId} />
    </AuthProvider>
  )
}

describe('MachineDetail', () => {
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
})
