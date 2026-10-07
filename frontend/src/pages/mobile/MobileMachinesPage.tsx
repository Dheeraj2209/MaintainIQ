// /m/machines (design/2026-10-07-mobile-operator-pwa-design.md, "Pages"): the
// fleet as tappable rows with a filter, for when there is no label to scan.
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import type { MachineSummary } from '../../api/types'
import { formatRul } from '../../components/dashboard/risk'
import { healthClasses, healthLabel } from '../../components/healthStyles'
import { Input, Label } from '../../components/ui/input'
import { cn } from '../../lib/cn'
import { useLiveEvents } from '../../realtime/LiveEventsProvider'

export function MobileMachinesPage() {
  const { lastEvent } = useLiveEvents()
  const [machines, setMachines] = useState<MachineSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  const load = useCallback(async () => {
    try {
      setMachines(await api.getMachines())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load machines')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (lastEvent && lastEvent.machine_id) void load()
  }, [lastEvent, load])

  const needle = query.trim().toLowerCase()
  const shown = (machines ?? []).filter((m) => m.machine_id.toLowerCase().includes(needle))

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-bold text-text">Machines</h1>
      <Label>
        Filter machines
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Machine id"
          className="min-h-11"
        />
      </Label>

      {error && (
        <p role="alert" className="rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
          {error}
        </p>
      )}

      {machines == null ? (
        !error && <p className="py-6 text-center text-sm text-text-muted">Loading machines…</p>
      ) : shown.length === 0 ? (
        <p className="py-6 text-center text-sm text-text-muted">No machines match.</p>
      ) : (
        <ul className="glass divide-y divide-white/10 overflow-hidden rounded-2xl">
          {shown.map((m) => (
            <li key={m.machine_id}>
              <Link
                to={`/m/machines/${encodeURIComponent(m.machine_id)}`}
                className="flex min-h-14 items-center gap-3 px-4 py-2 transition hover:bg-white/5"
              >
                <span aria-hidden className={cn('h-2.5 w-2.5 shrink-0 rounded-full', healthClasses(m.health_state).dot)} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-text">{m.machine_id}</span>
                  <span className={cn('text-xs', healthClasses(m.health_state).text)}>{healthLabel(m.health_state)}</span>
                </span>
                <span className="text-right text-xs text-text-muted">
                  <span className="block font-mono">RUL {formatRul(m.predicted_rul_minutes)}</span>
                  {m.open_alert_count > 0 && (
                    <span className="block">
                      {m.open_alert_count} open alert{m.open_alert_count === 1 ? '' : 's'}
                    </span>
                  )}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
