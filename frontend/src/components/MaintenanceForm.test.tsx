import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MaintenanceForm } from './MaintenanceForm'

describe('MaintenanceForm', () => {
  it('submits the entered values with the machine id', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<MaintenanceForm machineId="m1" onSubmit={onSubmit} />)

    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.type(screen.getByLabelText(/description/i), 'greased bearing')
    await userEvent.type(screen.getByLabelText(/technician/i), 'tech1')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))

    expect(onSubmit).toHaveBeenCalledWith({
      machine_id: 'm1',
      performed_at: '2026-07-20T10:00:00',
      description: 'greased bearing',
      technician: 'tech1',
    })
  })

  it('shows a success message after a successful submit', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<MaintenanceForm machineId="m1" onSubmit={onSubmit} />)
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))
    expect(await screen.findByText(/logged/i)).toBeInTheDocument()
  })

  it('surfaces the error message when submit fails', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('unknown machine_id'))
    render(<MaintenanceForm machineId="m1" onSubmit={onSubmit} />)
    await userEvent.type(screen.getByLabelText(/when/i), '2026-07-20T10:00')
    await userEvent.click(screen.getByRole('button', { name: /log maintenance/i }))
    expect(await screen.findByText(/unknown machine_id/)).toBeInTheDocument()
  })
})
