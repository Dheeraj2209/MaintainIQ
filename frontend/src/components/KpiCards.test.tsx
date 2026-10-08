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

  it('shows the active model failure-detection F1 with its version', () => {
    render(<KpiCards summary={kpiSummary} />)
    const card = screen.getByText('Failure-detection F1').parentElement!
    expect(card).toHaveTextContent('67.7%')
    expect(card).toHaveTextContent('xjtu-rul-20260930T164318Z')
    // The retired NASA-IMS classifier's accuracy must not be shown.
    expect(screen.queryByText(/Model accuracy/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/logistic_regression/i)).not.toBeInTheDocument()
  })

  it('omits the model card when prediction KPIs are not available', () => {
    const summary = { ...kpiSummary, prediction: { status: 'not_applicable' } }
    render(<KpiCards summary={summary} />)
    expect(screen.queryByText(/Failure-detection F1/i)).not.toBeInTheDocument()
  })

  it('omits the model card when the active model has no F1', () => {
    const summary = { ...kpiSummary, prediction: { status: 'available', accuracy: 0.9 } }
    render(<KpiCards summary={summary} />)
    expect(screen.queryByText(/Failure-detection F1/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Model accuracy/i)).not.toBeInTheDocument()
  })

  it('shows the open work order count', () => {
    render(<KpiCards summary={kpiSummary} />)
    expect(screen.getByText('Open work orders').parentElement).toHaveTextContent('1')
  })
})
