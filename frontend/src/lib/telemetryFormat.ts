// Pure display formatters for the live-telemetry section (LiveTelemetryPanel).
// Kept out of the component file so it only exports components (fast refresh)
// and so they can be unit-tested without rendering.

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

// Compact "12 s ago" / "3 min ago" / "2 h ago" / "4 d ago". Future timestamps
// (device clock ahead of ours) clamp to "just now" rather than "-5 s ago".
export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s} s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} h ago`
  return `${Math.floor(h / 24)} d ago`
}

// A span of seconds as "4 s" / "10 min" / "3 h 2 min" / "2 d" — the Devices
// page's "silent for" column. Rounds to whole seconds first.
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`
  return `${Math.floor(h / 24)} d`
}
