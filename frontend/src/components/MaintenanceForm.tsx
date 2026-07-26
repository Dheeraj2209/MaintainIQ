import { useState } from 'react'
import type { MaintenanceCreate } from '../api/types'

interface Props {
  machineId: string
  onSubmit: (payload: MaintenanceCreate) => Promise<unknown>
}

type Status = { kind: 'idle' | 'ok' | 'err'; message?: string }

export function MaintenanceForm({ machineId, onSubmit }: Props) {
  const [when, setWhen] = useState('')
  const [description, setDescription] = useState('')
  const [technician, setTechnician] = useState('')
  const [status, setStatus] = useState<Status>({ kind: 'idle' })
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setStatus({ kind: 'idle' })
    try {
      // datetime-local yields "YYYY-MM-DDTHH:mm" (no seconds); append ':00'
      // to match the backend's ISO expectation.
      await onSubmit({
        machine_id: machineId,
        performed_at: when ? `${when}:00` : '',
        description: description || null,
        technician: technician || null,
      })
      setStatus({ kind: 'ok', message: 'Logged.' })
      setWhen('')
      setDescription('')
      setTechnician('')
    } catch (err) {
      setStatus({ kind: 'err', message: err instanceof Error ? err.message : 'Failed to log maintenance' })
    } finally {
      setBusy(false)
    }
  }

  const field = 'rounded border border-slate-300 px-2 py-1 text-sm'

  return (
    <form onSubmit={handleSubmit} className="mt-3 grid gap-2">
      <strong className="text-sm text-slate-700">Log a maintenance record</strong>
      <label className="grid gap-0.5 text-xs text-slate-500">
        When
        <input
          type="datetime-local"
          required
          value={when}
          onChange={(e) => setWhen(e.target.value)}
          className={field}
        />
      </label>
      <label className="grid gap-0.5 text-xs text-slate-500">
        Description
        <input
          type="text"
          value={description}
          placeholder="e.g. re-greased bearing"
          onChange={(e) => setDescription(e.target.value)}
          className={field}
        />
      </label>
      <label className="grid gap-0.5 text-xs text-slate-500">
        Technician
        <input
          type="text"
          value={technician}
          onChange={(e) => setTechnician(e.target.value)}
          className={field}
        />
      </label>
      <button
        type="submit"
        disabled={busy}
        className="justify-self-start rounded bg-healthy px-3 py-1.5 text-sm font-medium text-white hover:brightness-95 disabled:opacity-50"
      >
        Log maintenance
      </button>
      {status.kind !== 'idle' && (
        <p className={`text-xs ${status.kind === 'ok' ? 'text-healthy' : 'text-critical'}`}>{status.message}</p>
      )}
    </form>
  )
}
