import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MachineGrid } from './MachineGrid'
import { machineSummaries } from '../test/fixtures'

describe('MachineGrid', () => {
  it('renders one card per machine with its state and risk', () => {
    render(<MachineGrid machines={machineSummaries} selectedId={null} onSelect={() => {}} />)
    expect(screen.getByText('m1')).toBeInTheDocument()
    expect(screen.getByText('m2')).toBeInTheDocument()
    expect(screen.getByText('Critical')).toBeInTheDocument()
    expect(screen.getByText(/Risk 100/)).toBeInTheDocument()
  })

  it('calls onSelect with the machine id when a card is clicked', async () => {
    const onSelect = vi.fn()
    render(<MachineGrid machines={machineSummaries} selectedId={null} onSelect={onSelect} />)
    await userEvent.click(screen.getByText('m1'))
    expect(onSelect).toHaveBeenCalledWith('m1')
  })

  it('marks the selected card as pressed for accessibility', () => {
    render(<MachineGrid machines={machineSummaries} selectedId="m1" onSelect={() => {}} />)
    const selected = screen.getByRole('button', { pressed: true })
    expect(selected).toHaveTextContent('m1')
  })

  it('shows an empty state when there are no machines', () => {
    render(<MachineGrid machines={[]} selectedId={null} onSelect={() => {}} />)
    expect(screen.getByText(/no machines/i)).toBeInTheDocument()
  })
})
