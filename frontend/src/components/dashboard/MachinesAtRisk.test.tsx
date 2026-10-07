import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MachinesAtRisk } from './MachinesAtRisk'
import { commissioningMachine, heldMachine, resetPendingMachine } from '../../test/fixtures'

describe('MachinesAtRisk health ratchet', () => {
  it('shows held, commissioning and reset states like the machine grid', () => {
    render(
      <MachinesAtRisk
        machines={[heldMachine, { ...commissioningMachine, machine_id: 'm2' }, { ...resetPendingMachine, machine_id: 'm3' }]}
        onSelect={() => {}}
      />,
    )
    expect(screen.getByText(/held since/i)).toBeInTheDocument()
    expect(screen.getByText('Commissioning 7/20')).toBeInTheDocument()
    expect(screen.getByText('Reset, awaiting reading')).toBeInTheDocument()
    expect(screen.queryByText('Healthy')).not.toBeInTheDocument()
  })
})
