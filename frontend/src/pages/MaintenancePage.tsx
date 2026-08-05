import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import type { MachineSummary, MaintenanceCreate, MaintenanceRecord } from '../api/types'
import { api } from '../api/client'
import { MaintenanceForm } from '../components/MaintenanceForm'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { Label, Select } from '../components/ui/input'

export function MaintenancePage() {
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [records, setRecords] = useState<MaintenanceRecord[]>([])
  const [selectedMachineId, setSelectedMachineId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const { lastEvent } = useLiveEvents()

  const load = useCallback(async () => {
    setError(null)
    try {
      const m = await api.getMachines()
      setMachines(m)
      setSelectedMachineId((prev) => prev || m[0]?.machine_id || '')
      const histories = await Promise.all(m.map((machine) => api.getMaintenanceHistory(machine.machine_id, { limit: 200 })))
      setRecords(histories.flat())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load maintenance history')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (lastEvent) load()
  }, [lastEvent, load])

  const sorted = useMemo(
    () => [...records].sort((a, b) => (a.performed_at < b.performed_at ? 1 : -1)),
    [records],
  )

  const logMaintenance = (payload: MaintenanceCreate) =>
    api.logMaintenance(payload).then((record) => {
      setRecords((prev) => [record, ...prev])
      return record
    })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Maintenance</h1>
        <p className="text-xs text-text-muted">Fleet-wide maintenance history and record logging</p>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="panel-notch glass rounded-2xl p-4 lg:col-span-2">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">History</h2>
          {loading ? (
            <p className="py-4 text-sm text-text-muted">Loading…</p>
          ) : sorted.length === 0 ? (
            <p className="py-4 text-sm text-text-muted">No maintenance records logged yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-xs uppercase text-text-muted">
                    <th className="py-2 pr-3">Machine</th>
                    <th className="py-2 pr-3">Performed</th>
                    <th className="py-2 pr-3">Description</th>
                    <th className="py-2 pr-3">Technician</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r) => (
                    <tr key={r.id} className="border-b border-white/10 last:border-0">
                      <td className="py-2 pr-3">
                        <Link
                          to={`/machines/${encodeURIComponent(r.machine_id)}`}
                          className="text-accent hover:text-accent-hover"
                        >
                          {r.machine_id}
                        </Link>
                      </td>
                      <td className="py-2 pr-3 text-text-muted">{r.performed_at}</td>
                      <td className="py-2 pr-3 text-text-muted">{r.description ?? '—'}</td>
                      <td className="py-2 pr-3 text-text-muted">{r.technician ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="panel-notch glass rounded-2xl p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Log a record</h2>
          <Label>
            Machine
            <Select value={selectedMachineId} onChange={(e) => setSelectedMachineId(e.target.value)}>
              {machines.map((m) => (
                <option key={m.machine_id} value={m.machine_id}>
                  {m.machine_id}
                </option>
              ))}
            </Select>
          </Label>
          {selectedMachineId && (
            <MaintenanceForm key={selectedMachineId} machineId={selectedMachineId} onSubmit={logMaintenance} />
          )}
        </section>
      </div>
    </div>
  )
}
