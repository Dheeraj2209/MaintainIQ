import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { DotItemDotProps } from 'recharts'
import type { TrendPoint } from '../api/types'
import { useChartColors } from '../lib/chartColors'
import { markerIndex, type ChartMarker } from '../lib/chartMarker'

interface Props {
  metric: string
  points: TrendPoint[]
  // A vertical line at one point in time, e.g. the reading that triggered an
  // alert ("Why this alert?"). Announced in the aria-label too, since the SVG
  // isn't exposed to assistive tech (or to jsdom).
  // `index` pins the marker to one point; without it the last point with the
  // exact timestamp is used.
  marker?: ChartMarker
}

// Compact time label (drop the date's seconds) for the axis.
function compactTime(timestamp: string): string {
  return timestamp.replace('T', ' ').slice(0, 16)
}

// Points more than 2 standard deviations from the series mean are flagged as
// visual outliers (amber dot) — a plain statistical heuristic over the real
// values, not a fabricated per-point severity field (the API doesn't expose
// one on TrendPoint).
function outlierIndexes(values: number[]): Set<number> {
  if (values.length < 4) return new Set()
  const mean = values.reduce((sum, v) => sum + v, 0) / values.length
  const variance = values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / values.length
  const stddev = Math.sqrt(variance)
  if (stddev === 0) return new Set()
  const flagged = new Set<number>()
  values.forEach((v, i) => {
    if (Math.abs(v - mean) / stddev > 2) flagged.add(i)
  })
  return flagged
}

// Shows the last N readings for one metric. The outer div carries role="img"
// + aria-label so the chart is announced and testable without reaching into
// recharts' SVG internals (which don't lay out under jsdom).
export function TrendChart({ metric, points, marker }: Props) {
  const colors = useChartColors()

  if (points.length === 0) {
    return <p className="py-6 text-sm text-text-muted">No data for this metric.</p>
  }

  const data = points.map((p, i) => ({ i, t: compactTime(p.timestamp), value: p.value }))
  const labelOf = (i: unknown) => data[Number(i)]?.t ?? ''
  const markerAt = markerIndex(points, marker)
  const outliers = outlierIndexes(data.map((d) => d.value ?? 0))

  function renderDot(props: DotItemDotProps) {
    const { cx, cy, index } = props
    if (index === undefined || !outliers.has(index) || cx === undefined || cy === undefined) {
      return <circle key={index} cx={cx} cy={cy} r={0} fill="none" />
    }
    return <circle key={index} cx={cx} cy={cy} r={4} fill={colors.accent2} stroke={colors.bg} strokeWidth={1.5} />
  }

  return (
    <div
      role="img"
      aria-label={`Trend of ${metric}${marker ? `, ${marker.label} at ${marker.timestamp}` : ''}`}
      className="h-56 w-full font-mono"
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <defs>
            <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors.accent} stopOpacity={0.35} />
              <stop offset="100%" stopColor={colors.accent} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={colors.border} vertical={false} />
          <XAxis dataKey="i" tickFormatter={labelOf} tick={{ fontSize: 10, fill: colors.textMuted }} minTickGap={40} stroke={colors.border} />
          <YAxis tick={{ fontSize: 10, fill: colors.textMuted }} width={48} domain={['auto', 'auto']} stroke={colors.border} />
          <Tooltip
            contentStyle={{
              backgroundColor: colors.surface,
              border: `1px solid ${colors.border}`,
              borderRadius: 12,
              fontSize: 12,
              boxShadow: '0 12px 40px -16px rgba(0,0,0,0.8)',
            }}
            labelFormatter={labelOf}
            labelStyle={{ color: colors.textMuted }}
            itemStyle={{ color: colors.text }}
          />
          <Area
            type="monotone"
            dataKey="value"
            name={metric}
            stroke={colors.accent}
            fill="url(#trend-fill)"
            dot={renderDot}
            strokeWidth={2}
            isAnimationActive={false}
          />
          {marker && markerAt !== null && (
            <ReferenceLine
              x={markerAt}
              stroke={colors.accent2}
              strokeDasharray="4 3"
              label={{ value: marker.label, position: 'insideTopRight', fontSize: 10, fill: colors.accent2 }}
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
