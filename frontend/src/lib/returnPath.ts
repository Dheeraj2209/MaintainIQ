// Where LoginPage sends a user after signing in (design/2026-10-07-mobile-operator-pwa-design.md,
// decision 14): the full location RequireAuth saved in `state.from`, so a
// push tap or QR scan by a signed-out user comes back to the exact path,
// search and hash. Only a same-origin path is honoured; anything else (a
// protocol-relative "//host", a backslash variant browsers treat the same,
// a full URL) falls back to the dashboard.
const DEFAULT_PATH = '/dashboard'

interface SavedLocation {
  pathname?: unknown
  search?: unknown
  hash?: unknown
}

function part(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function returnPath(state: unknown): string {
  const from = (state as { from?: SavedLocation } | null | undefined)?.from
  const pathname = from?.pathname
  if (typeof pathname !== 'string' || !pathname.startsWith('/') || /^\/[/\\]/.test(pathname)) return DEFAULT_PATH
  return pathname + part(from?.search) + part(from?.hash)
}
