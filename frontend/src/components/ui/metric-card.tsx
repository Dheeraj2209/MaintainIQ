import { animate, useReducedMotion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { cn } from '../../lib/cn'
import { Card } from './card'
import { Sparkline } from './sparkline'

type Tone = 'default' | 'accent' | 'accent2' | 'healthy' | 'degrading' | 'faulty' | 'critical' | 'unknown'

const TONE_TEXT: Record<Tone, string> = {
  default: 'text-text',
  accent: 'text-accent',
  accent2: 'text-accent-2',
  healthy: 'text-healthy',
  degrading: 'text-degrading',
  faulty: 'text-faulty',
  critical: 'text-critical',
  unknown: 'text-unknown',
}

// A faint tone-tinted radial glow bloom behind the value, keyed off the same
// CSS custom property the sparkline reads, so each card is subtly lit in its
// own signal color.
const TONE_GLOW: Record<Tone, string> = {
  default: 'transparent',
  accent: 'var(--color-accent)',
  accent2: 'var(--color-accent-2)',
  healthy: 'var(--color-healthy)',
  degrading: 'var(--color-degrading)',
  faulty: 'var(--color-faulty)',
  critical: 'var(--color-critical)',
  unknown: 'var(--color-unknown)',
}

const TONE_SPARKLINE: Record<Tone, string> = {
  default: '#f2f0ec',
  accent: '#e23a3a',
  accent2: '#d4af37',
  healthy: '#3ddc84',
  degrading: '#f0a93a',
  faulty: '#ff7a45',
  critical: '#ff5470',
  unknown: '#7a7a7a',
}

interface MetricCardProps {
  label: string
  value: number | string
  sub?: string
  tone?: Tone
  trend?: number[]
  className?: string
}

function useCountUp(target: number) {
  const [display, setDisplay] = useState(target)
  const reduceMotion = useReducedMotion()

  useEffect(() => {
    if (reduceMotion) {
      setDisplay(target)
      return
    }
    const controls = animate(0, target, {
      duration: 0.6,
      ease: 'easeOut',
      onUpdate: (v) => setDisplay(Math.round(v)),
    })
    return () => controls.stop()
  }, [target, reduceMotion])

  return display
}

export function MetricCard({ label, value, sub, tone = 'default', trend, className }: MetricCardProps) {
  const isNumeric = typeof value === 'number'
  const animated = useCountUp(isNumeric ? value : 0)
  const displayValue = isNumeric ? animated : value

  return (
    <Card className={cn('hover-glow relative overflow-hidden p-4', className)}>
      {tone !== 'default' && (
        <div
          aria-hidden
          className="pointer-events-none absolute -right-6 -top-8 h-24 w-24 rounded-full opacity-20 blur-2xl"
          style={{ backgroundColor: TONE_GLOW[tone] }}
        />
      )}
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted">{label}</p>
      <div className="mt-1.5 flex items-end justify-between gap-2">
        <p className={cn('font-mono text-2xl font-semibold tabular-nums', TONE_TEXT[tone])}>{displayValue}</p>
        {trend && trend.length > 1 && <Sparkline data={trend} color={TONE_SPARKLINE[tone]} />}
      </div>
      {sub && <p className="mt-1 text-xs text-text-muted">{sub}</p>}
    </Card>
  )
}
