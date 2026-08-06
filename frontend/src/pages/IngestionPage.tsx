import { useCallback, useEffect, useState } from 'react'
import type { MachineSummary, ReplayStatus } from '../api/types'
import { api } from '../api/client'
import { Card } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Select } from '../components/ui/input'
import { Badge } from '../components/ui/badge'

export function IngestionPage() {
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [machineId, setMachineId] = useState('')
  const [speed, setSpeed] = useState(1)
  const [status, setStatus] = useState<ReplayStatus>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadStatus = useCallback(() => {
    api
      .getReplayStatus()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load replay status'))
  }, [])

  useEffect(() => {
    api
      .getMachines()
      .then((m) => {
        setMachines(m)
        setMachineId((prev) => prev || m[0]?.machine_id || '')
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load machines'))
    loadStatus()
  }, [loadStatus])

  async function handleStart() {
    if (!machineId) return
    setBusy(true)
    setError(null)
    try {
      await api.startReplay({ machine_id: machineId, speed_multiplier: speed })
      loadStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start replay')
    } finally {
      setBusy(false)
    }
  }

  async function handleStop() {
    if (!machineId) return
    setBusy(true)
    setError(null)
    try {
      await api.stopReplay(machineId)
      loadStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to stop replay')
    } finally {
      setBusy(false)
    }
  }

  const rows = Object.entries(status)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Ingestion control</h1>
        <p className="text-xs text-text-muted">Replay stored readings back through the live predict + persist path</p>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      <Card className="panel-notch p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Start / stop a replay</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-0.5 text-xs text-text-muted">
            Machine
            <Select value={machineId} onChange={(e) => setMachineId(e.target.value)}>
              {machines.map((m) => (
                <option key={m.machine_id} value={m.machine_id}>
                  {m.machine_id}
                </option>
              ))}
            </Select>
          </label>
          <label className="grid gap-0.5 text-xs text-text-muted">
            Speed ×
            <input
              type="number"
              min={0.1}
              step={0.1}
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              className="w-24 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
            />
          </label>
          <Button type="button" onClick={handleStart} disabled={busy || !machineId}>
            {busy ? 'Working…' : 'Start replay'}
          </Button>
          <Button type="button" variant="outline" onClick={handleStop} disabled={busy || !machineId}>
            Stop replay
          </Button>
          <Button type="button" variant="outline" onClick={loadStatus} disabled={busy}>
            Refresh
          </Button>
        </div>
      </Card>

      <Card className="panel-notch overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="bg-white/[0.04]">
            <tr className="text-xs uppercase text-text-muted">
              <th className="px-3 py-2">Machine</th>
              <th className="px-3 py-2">State</th>
              <th className="px-3 py-2">Cycle</th>
              <th className="px-3 py-2">Replayed</th>
              <th className="px-3 py-2">Last timestamp</th>
              <th className="px-3 py-2">Error</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-text-muted">No replays tracked yet.</td>
              </tr>
            ) : (
              rows.map(([id, s]) => (
                <tr key={id} className="border-t border-white/10">
                  <td className="px-3 py-2 font-mono text-text">{id}</td>
                  <td className="px-3 py-2">
                    <Badge variant={s.running ? 'healthy' : 'unknown'}>{s.running ? 'Running' : 'Stopped'}</Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-text-muted">{s.cycle ?? '—'}</td>
                  <td className="px-3 py-2 font-mono text-text-muted">{s.replayed}</td>
                  <td className="px-3 py-2 font-mono text-text-muted">{s.last_ts ?? '—'}</td>
                  <td className="px-3 py-2 text-critical">{s.error ?? '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
