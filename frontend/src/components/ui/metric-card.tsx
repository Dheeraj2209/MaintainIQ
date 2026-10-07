import { animate, useReducedMotion } from 'framer-motion'
import { useEffect, useState, type CSSProperties } from 'react'
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
// own signal color. Also feeds `--spot-tone`, which colours the hover spotlight
// (index.css) — with `default` falling back to the brand accent rather than
// `transparent`, since an invisible spotlight isn't a spotlight.
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

// Sparklines render through Recharts, which cannot read CSS custom properties
// from stroke props — so these literals must be kept in step with the tokens
// in index.css (and with FALLBACK in lib/chartColors.ts).
const TONE_SPARKLINE: Record<Tone, string> = {
  default: '#eef1f8',
  accent: '#7c6cff',
  accent2: '#4dc9ff',
  healthy: '#5b6a86',
  degrading: '#3bb8c9',
  faulty: '#9d6cf0',
  critical: '#e85ad0',
  unknown: '#3f4657',
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
    // No `overflow-hidden`: the proximity ring is drawn at `inset: -1px` and
    // would be clipped away. The corner glow blob that used to live here is
    // gone with it — it was a static stand-in for lighting, and now that the
    // cursor actually lights the card there is no reason for an idle card to
    // glow at all.
    <Card
      style={{ '--spot-tone': tone === 'default' ? 'var(--color-accent)' : TONE_GLOW[tone] } as CSSProperties}
      className={cn('hover-glow relative p-4', className)}
    >
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted">{label}</p>
      <div className="mt-1.5 flex items-end justify-between gap-2">
        <p className={cn('font-mono text-2xl font-semibold tabular-nums', TONE_TEXT[tone])}>{displayValue}</p>
        {trend && trend.length > 1 && <Sparkline data={trend} color={TONE_SPARKLINE[tone]} />}
      </div>
      {sub && <p className="mt-1 text-xs text-text-muted">{sub}</p>}
    </Card>
  )
}
