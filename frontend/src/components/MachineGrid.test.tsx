import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MachineGrid } from './MachineGrid'
import { commissioningMachine, heldMachine, machineSummaries } from '../test/fixtures'

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

describe('MachineGrid health ratchet', () => {
  it('badges a held machine with when it was held', () => {
    render(<MachineGrid machines={[heldMachine]} selectedId={null} onSelect={() => {}} />)
    expect(screen.getByText('Critical')).toBeInTheDocument()
    expect(screen.getByText(/held since/i)).toBeInTheDocument()
  })

  it('shows commissioning in a neutral badge instead of healthy, with the current signal', () => {
    render(<MachineGrid machines={[commissioningMachine]} selectedId={null} onSelect={() => {}} />)
    expect(screen.getByText('Commissioning 7/20')).toBeInTheDocument()
    expect(screen.queryByText('Healthy')).not.toBeInTheDocument()
    expect(screen.getByText('Signal: Faulty')).toBeInTheDocument()
    expect(screen.queryByText(/held since/i)).not.toBeInTheDocument()
  })
})
