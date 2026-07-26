import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { TrendPoint } from '../api/types'

interface Props {
  metric: string
  points: TrendPoint[]
}

// Shows the last N readings for one metric. The outer div carries role="img"
// + aria-label so the chart is announced and testable without reaching into
// recharts' SVG internals (which don't lay out under jsdom).
export function TrendChart({ metric, points }: Props) {
  if (points.length === 0) {
    return <p className="py-6 text-sm text-slate-400">No data for this metric.</p>
  }

  const data = points.map((p) => ({
    // Compact time label (drop the date's seconds) for the axis.
    t: p.timestamp.replace('T', ' ').slice(0, 16),
    value: p.value,
  }))

  return (
    <div role="img" aria-label={`Trend of ${metric}`} className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={40} />
          <YAxis tick={{ fontSize: 10 }} width={48} domain={['auto', 'auto']} />
          <Tooltip />
          <Line
            type="monotone"
            dataKey="value"
            name={metric}
            stroke="#2563eb"
            dot={false}
            strokeWidth={1.5}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
