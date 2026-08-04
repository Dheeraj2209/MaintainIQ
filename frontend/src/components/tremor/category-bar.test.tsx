import { render, screen } from '@testing-library/react'
import { CategoryBar } from './category-bar'

describe('CategoryBar', () => {
  it('renders a segment and legend entry per non-zero category', () => {
    render(
      <CategoryBar
        segments={[
          { label: 'Healthy', value: 6, className: 'bg-healthy' },
          { label: 'Critical', value: 2, className: 'bg-critical' },
        ]}
      />,
    )
    // Legend shows label + value for each category
    expect(screen.getByText('Healthy')).toBeInTheDocument()
    expect(screen.getByText('6')).toBeInTheDocument()
    expect(screen.getByText('Critical')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    // The bar is announced with the full breakdown
    expect(screen.getByRole('img', { name: /Healthy: 6, Critical: 2/ })).toBeInTheDocument()
  })

  it('falls back to an empty track when the total is zero', () => {
    render(<CategoryBar segments={[{ label: 'Healthy', value: 0, className: 'bg-healthy' }]} showLabels={false} />)
    expect(screen.getByRole('img', { name: /Healthy: 0/ })).toBeInTheDocument()
  })
})
