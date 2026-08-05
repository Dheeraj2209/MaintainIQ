import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { MachineDetail } from './MachineDetail'
import { server } from '../test/server'
import { api } from '../api/client'

function harness(component: React.ReactNode) {
  return <AuthProvider>{component}</AuthProvider>
}

describe('MachineDetail', () => {
  it('loads and shows health facts for the machine', async () => {
    render(harness(<MachineDetail machineId="m1" onLogged={() => {}} />))

    expect(await screen.findByRole('heading', { name: /m1/ })).toBeInTheDocument()
    expect(screen.getAllByText(/critical/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/bearing_wear/)).toBeInTheDocument()
    // risk score surfaced somewhere
    expect(screen.getByText(/100/)).toBeInTheDocument()
  })

  it('renders a metric selector and the trend chart', async () => {
    render(harness(<MachineDetail machineId="m1" onLogged={() => {}} />))

    const select = await screen.findByLabelText(/metric/i)
    expect(select).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: /vibration_h_rms/i })).toBeInTheDocument()
  })

  it('refetches trends when the metric changes', async () => {
    const spy = vi.spyOn(api, 'getTrends')
    render(harness(<MachineDetail machineId="m1" onLogged={() => {}} />))

    const select = await screen.findByLabelText(/metric/i)
    await userEvent.selectOptions(select, 'temperature_c')

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('m1', 'temperature_c', expect.anything()),
    )
    spy.mockRestore()
  })

  it('logs maintenance and notifies the parent', async () => {
    const onLogged = vi.fn()
    render(harness(<MachineDetail machineId="m1" onLogged={onLogged} />))

    await screen.findByRole('heading', { name: /m1/ })
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.type(screen.getByLabelText(/description/i), 'greased bearing')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(await screen.findByText(/logged/i)).toBeInTheDocument()
    await waitFor(() => expect(onLogged).toHaveBeenCalled())
  })

  it('surfaces a load error', async () => {
    server.use(
      http.get('/api/machines/:id', () =>
        HttpResponse.json({ detail: 'machine not found' }, { status: 404 }),
      ),
    )
    render(harness(<MachineDetail machineId="mX" onLogged={() => {}} />))
    expect(await screen.findByText(/not found/i)).toBeInTheDocument()
  })
})
