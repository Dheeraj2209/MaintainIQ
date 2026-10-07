import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AlertsPanel } from './AlertsPanel'
import { openAlerts, resolvedAlertWithFeedback } from '../test/fixtures'

describe('AlertsPanel', () => {
  it('renders each alert message', () => {
    render(<AlertsPanel alerts={openAlerts} onSelect={() => {}} />)
    expect(screen.getByText('m1 critical')).toBeInTheDocument()
  })

  it('shows an empty state when there are no alerts', () => {
    render(<AlertsPanel alerts={[]} onSelect={() => {}} />)
    expect(screen.getByText(/no open alerts/i)).toBeInTheDocument()
  })

  it('calls onSelect with the full alert object when an alert is clicked', async () => {
    const onSelect = vi.fn()
    render(<AlertsPanel alerts={openAlerts} onSelect={onSelect} />)
    await userEvent.click(screen.getByText('m1 critical'))
    expect(onSelect).toHaveBeenCalledWith(openAlerts[0])
  })

  it('visually marks the selected alert', () => {
    render(<AlertsPanel alerts={openAlerts} onSelect={() => {}} selectedAlertId={openAlerts[0].id} />)
    expect(screen.getByRole('button', { name: /m1 critical/i })).toHaveClass('ring-2')
  })

  it('badges the recorded outcome of an alert that has feedback', () => {
    render(<AlertsPanel alerts={[...openAlerts, resolvedAlertWithFeedback]} onSelect={() => {}} />)
    const row = screen.getByText('m1 faulty').closest('button')
    if (!row) throw new Error('row not found')
    expect(row).toHaveTextContent(/prevented by maintenance/i)
    const open = screen.getByText('m1 critical').closest('button')
    expect(open).not.toHaveTextContent(/prevented by maintenance/i)
  })

  it('offers "Why?" only when onExplain is given, without selecting the alert', async () => {
    const onSelect = vi.fn()
    const onExplain = vi.fn()
    const { rerender } = render(<AlertsPanel alerts={openAlerts} onSelect={onSelect} />)
    expect(screen.queryByRole('button', { name: /why this alert/i })).not.toBeInTheDocument()

    rerender(<AlertsPanel alerts={openAlerts} onSelect={onSelect} onExplain={onExplain} />)
    await userEvent.click(screen.getByRole('button', { name: 'Why this alert? Alert #2' }))
    expect(onExplain).toHaveBeenCalledWith(openAlerts[0])
    expect(onSelect).not.toHaveBeenCalled()
  })
})
