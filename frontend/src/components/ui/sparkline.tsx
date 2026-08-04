import { Line, LineChart, ResponsiveContainer } from 'recharts'

interface Props {
  data: number[]
  color: string
}

// Minimal axis-less trend line for inline use inside metric cards.
export function Sparkline({ data, color }: Props) {
  if (data.length < 2) return null
  const points = data.map((value, i) => ({ i, value }))

  return (
    <div className="h-8 w-20" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points}>
          <Line
            type="monotone"
            dataKey="value"
            stroke={color}
            dot={false}
            strokeWidth={1.5}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
