import { useEffect, useState } from 'react'

// Recharts needs literal color strings (it can't consume CSS variables in
// stroke/fill props), so we read the current theme's custom properties off
// the root element once on mount instead of hardcoding hex values that would
// silently drift from src/index.css whenever the theme changes.
export interface ChartColors {
  bg: string
  border: string
  textMuted: string
  surface: string
  text: string
  accent: string
  accent2: string
  healthy: string
  degrading: string
  faulty: string
  critical: string
  unknown: string
}

const VAR_MAP: Record<keyof ChartColors, string> = {
  bg: '--color-bg',
  border: '--color-border',
  textMuted: '--color-text-muted',
  surface: '--color-surface',
  text: '--color-text',
  accent: '--color-accent',
  accent2: '--color-accent-2',
  healthy: '--color-healthy',
  degrading: '--color-degrading',
  faulty: '--color-faulty',
  critical: '--color-critical',
  unknown: '--color-unknown',
}

// Sensible fallbacks matching index.css, used until the effect runs (and as
// a safety net under jsdom, where computed custom properties may read empty).
const FALLBACK: ChartColors = {
  bg: '#070809',
  border: '#2b2f39',
  textMuted: '#9aa1ad',
  surface: '#12141a',
  text: '#f2f4f8',
  accent: '#e23a3a',
  accent2: '#d4af37',
  healthy: '#3ddc84',
  degrading: '#f0a93a',
  faulty: '#ff7a45',
  critical: '#ff5470',
  unknown: '#7a7a7a',
}

function readChartColors(): ChartColors {
  if (typeof window === 'undefined') return FALLBACK
  const styles = getComputedStyle(document.documentElement)
  const result = { ...FALLBACK }
  for (const key of Object.keys(VAR_MAP) as (keyof ChartColors)[]) {
    const value = styles.getPropertyValue(VAR_MAP[key]).trim()
    if (value) result[key] = value
  }
  return result
}

export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(FALLBACK)

  useEffect(() => {
    setColors(readChartColors())
  }, [])

  return colors
}
