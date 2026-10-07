// Conversions for `<input type="datetime-local">`, which holds wall-clock
// time with no zone. Kept out of component files so those only export
// components (fast refresh).

// "2026-10-07T14:30" (local) → UTC ISO-8601, or null for empty/invalid.
export function localInputToIso(value: string): string | null {
  if (!value) return null
  const t = new Date(value)
  return Number.isNaN(t.getTime()) ? null : t.toISOString()
}

// The current local time in the input's "YYYY-MM-DDTHH:mm" shape.
export function nowLocalInput(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`
}

// An ISO-8601 timestamp as the input's local "YYYY-MM-DDTHH:mm", or "" for
// empty/invalid (the inverse of localInputToIso, to the minute).
export function isoToLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso)
  return Number.isNaN(t.getTime()) ? '' : nowLocalInput(t)
}
