import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TrendChart } from './TrendChart'
import { markerIndex } from '../lib/chartMarker'
import { trendPoints } from '../test/fixtures'

describe('TrendChart', () => {
  it('shows an empty state when there is no data', () => {
    render(<TrendChart metric="vibration_h_rms" points={[]} />)
    expect(screen.getByText(/no data/i)).toBeInTheDocument()
  })

  it('renders a labeled chart region when data is present', () => {
    render(<TrendChart metric="rul_minutes" points={trendPoints} />)
    expect(screen.getByRole('img', { name: /rul_minutes/ })).toBeInTheDocument()
  })

  it('announces a marker (the alert trigger) in the chart label', () => {
    render(
      <TrendChart
        metric="Horizontal kurtosis"
        points={trendPoints}
        marker={{ timestamp: trendPoints[1].timestamp, label: 'Trigger' }}
      />,
    )
    expect(screen.getByRole('img')).toHaveAccessibleName(
      `Trend of Horizontal kurtosis, Trigger at ${trendPoints[1].timestamp}`,
    )
  })

  it('keeps the plain label without a marker', () => {
    render(<TrendChart metric="rul_minutes" points={trendPoints} />)
    expect(screen.getByRole('img')).toHaveAccessibleName('Trend of rul_minutes')
  })

  it('resolves the marker to a unique point even when readings share a minute', () => {
    // 2 s MQTT telemetry: every point falls in the same minute label.
    const points = Array.from({ length: 30 }, (_, i) => ({
      timestamp: `2026-10-07T10:00:${String(i * 2).padStart(2, '0')}+00:00`,
      value: i,
    }))
    expect(markerIndex(points, { timestamp: points[17].timestamp, label: 'Trigger' })).toBe(17)
    expect(markerIndex(points, { timestamp: points[29].timestamp, label: 'Trigger', index: 29 })).toBe(29)
    expect(markerIndex(points, { timestamp: 'not-in-data', label: 'Trigger' })).toBeNull()
    expect(markerIndex(points, { timestamp: points[0].timestamp, label: 'Trigger', index: 30 })).toBeNull()
    expect(markerIndex(points, undefined)).toBeNull()
  })
})
