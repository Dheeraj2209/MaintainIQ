/**
 * Shared vocabulary for the risk widgets. Lives outside any component file so
 * the two widgets that need it don't have to import from each other, and so
 * "what counts as urgent" has exactly one definition on the client.
 */

/** Risk score at or above which a machine is treated as needing intervention now. */
export const URGENT_RISK = 80

/**
 * Remaining life in the coarsest unit that still reads as precise. Shared so
 * the same number never appears in two different shapes on one screen.
 */
export function formatRul(minutes: number | null): string {
  if (minutes === null) return '—'
  if (minutes <= 0) return 'overdue'
  if (minutes < 90) return `${Math.round(minutes)} min`
  const hours = minutes / 60
  if (hours < 48) return `${Math.round(hours)} h`
  return `${Math.round(hours / 24)} d`
}
