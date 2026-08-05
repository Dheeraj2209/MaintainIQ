import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthProvider } from '../auth/AuthContext'
import { MaintenanceForm } from './MaintenanceForm'
import { openAlerts } from '../test/fixtures'

function harness(props: Parameters<typeof MaintenanceForm>[0]) {
  return (
    <AuthProvider>
      <MaintenanceForm {...props} />
    </AuthProvider>
  )
}

describe('MaintenanceForm', () => {
  it('submits the entered values with the machine id, default type, and no linked alert', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit }))

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.type(screen.getByLabelText(/description/i), 'greased bearing')
    await userEvent.clear(screen.getByLabelText(/technician/i))
    await userEvent.type(screen.getByLabelText(/technician/i), 'tech1')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(onSubmit).toHaveBeenCalledWith({
      machine_id: 'm1',
      performed_at: '2026-07-20T10:00:00',
      description: 'greased bearing',
      technician: 'tech1',
      alert_id: null,
      type: 'preventive',
    })
  })

  it('pre-fills the technician field from the authenticated user', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit }))

    expect(await screen.findByDisplayValue('Ada Admin')).toBeInTheDocument()
  })

  it('shows a success message after a successful submit', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit }))
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))
    expect(await screen.findByText(/logged/i)).toBeInTheDocument()
  })

  it('surfaces the error message when submit fails', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('unknown machine_id'))
    render(harness({ machineId: 'm1', onSubmit }))
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))
    expect(await screen.findByText(/unknown machine_id/)).toBeInTheDocument()
  })

  it('shows a dismissible chip and defaults to corrective when a linked alert is set', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit, linkedAlert: openAlerts[0] }))

    expect(await screen.findByText(/logging maintenance for alert #2/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/type/i)).toHaveValue('corrective')

    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(screen.queryByText(/logging maintenance for alert #2/i)).not.toBeInTheDocument()
  })

  it('includes alert_id in the payload when a linked alert is set', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(harness({ machineId: 'm1', onSubmit, linkedAlert: openAlerts[0] }))

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ alert_id: 2, type: 'corrective' }))
  })
})
