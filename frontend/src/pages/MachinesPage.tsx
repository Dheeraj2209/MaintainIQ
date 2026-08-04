import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { HealthState, MachineSummary } from '../api/types'
import { api } from '../api/client'
import { MachineGrid } from '../components/MachineGrid'
import { healthLabel } from '../components/healthStyles'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

const STATES: Array<HealthState | 'all'> = ['all', 'healthy', 'degrading', 'faulty', 'critical', 'unknown']

export function MachinesPage() {
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<HealthState | 'all'>('all')
  const [search, setSearch] = useState('')
  const navigate = useNavigate()
  const { lastEvent } = useLiveEvents()

  const load = () => {
    setError(null)
    api
      .getMachines()
      .then(setMachines)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load machines'))
  }

  useEffect(load, [])
  useEffect(() => {
    if (lastEvent) load()
  }, [lastEvent])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return machines
      .filter((m) => filter === 'all' || m.health_state === filter)
      .filter((m) => !q || m.machine_id.toLowerCase().includes(q))
  }, [machines, filter, search])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-text">Machines</h1>
          <p className="text-xs text-text-muted">{machines.length} machines in the fleet</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="search"
            aria-label="Search machines"
            placeholder="Search by ID…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur placeholder:text-text-muted/60 focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
          />
          <select
            aria-label="Filter by health state"
            value={filter}
            onChange={(e) => setFilter(e.target.value as HealthState | 'all')}
            className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
          >
            {STATES.map((s) => (
              <option key={s} value={s}>
                {s === 'all' ? 'All states' : healthLabel(s)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      <MachineGrid
        machines={filtered}
        selectedId={null}
        onSelect={(id) => navigate(`/machines/${encodeURIComponent(id)}`)}
      />
    </div>
  )
}
