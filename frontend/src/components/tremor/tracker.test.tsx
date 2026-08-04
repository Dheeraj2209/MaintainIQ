import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Tracker } from './tracker'

describe('Tracker', () => {
  it('renders one labelled cell per entity', () => {
    render(
      <Tracker
        cells={[
          { key: 'm1', className: 'bg-healthy', tooltip: 'm1 — Healthy' },
          { key: 'm2', className: 'bg-critical', tooltip: 'm2 — Critical' },
        ]}
      />,
    )
    expect(screen.getByLabelText('m1 — Healthy')).toBeInTheDocument()
    expect(screen.getByLabelText('m2 — Critical')).toBeInTheDocument()
  })

  it('invokes onClick when a cell is activated', async () => {
    const onClick = vi.fn()
    render(<Tracker cells={[{ key: 'm1', className: 'bg-healthy', tooltip: 'm1 — Healthy', onClick }]} />)
    await userEvent.click(screen.getByRole('button', { name: 'm1 — Healthy' }))
    expect(onClick).toHaveBeenCalledOnce()
  })
})
