import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AlertsPanel } from './AlertsPanel'
import { openAlerts } from '../test/fixtures'

describe('AlertsPanel', () => {
  it('renders each alert message', () => {
    render(<AlertsPanel alerts={openAlerts} onSelect={() => {}} />)
    expect(screen.getByText('m1 critical')).toBeInTheDocument()
  })

  it('shows an empty state when there are no alerts', () => {
    render(<AlertsPanel alerts={[]} onSelect={() => {}} />)
    expect(screen.getByText(/no open alerts/i)).toBeInTheDocument()
  })

  it('calls onSelect with the machine id when an alert is clicked', async () => {
    const onSelect = vi.fn()
    render(<AlertsPanel alerts={openAlerts} onSelect={onSelect} />)
    await userEvent.click(screen.getByText('m1 critical'))
    expect(onSelect).toHaveBeenCalledWith('m1')
  })
})
