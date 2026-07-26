import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { KpiCards } from './KpiCards'
import { kpiSummary } from '../test/fixtures'

describe('KpiCards', () => {
  it('shows the machine count and open alert count', () => {
    render(<KpiCards summary={kpiSummary} />)
    expect(screen.getByText('Machines').parentElement).toHaveTextContent('2')
    expect(screen.getByText('Open alerts').parentElement).toHaveTextContent('1')
  })

  it('shows model accuracy as a percentage when available', () => {
    render(<KpiCards summary={kpiSummary} />)
    expect(screen.getByText(/79\.6%/)).toBeInTheDocument()
  })

  it('omits the accuracy card when prediction KPIs are not available', () => {
    const summary = { ...kpiSummary, prediction: { status: 'not_applicable' } }
    render(<KpiCards summary={summary} />)
    expect(screen.queryByText(/Model accuracy/i)).not.toBeInTheDocument()
  })
})
