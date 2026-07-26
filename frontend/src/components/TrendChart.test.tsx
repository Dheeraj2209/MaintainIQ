import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TrendChart } from './TrendChart'
import { trendPoints } from '../test/fixtures'

describe('TrendChart', () => {
  it('shows an empty state when there is no data', () => {
    render(<TrendChart metric="vibration_h_rms" points={[]} />)
    expect(screen.getByText(/no data/i)).toBeInTheDocument()
  })

  it('renders a labeled chart region when data is present', () => {
    render(<TrendChart metric="temperature_c" points={trendPoints} />)
    expect(screen.getByRole('img', { name: /temperature_c/ })).toBeInTheDocument()
  })
})
