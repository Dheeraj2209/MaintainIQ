import { useState } from 'react'
import type { FormEvent } from 'react'
import type { MaintenanceCreate } from '../api/types'
import { Input, Label } from './ui/input'
import { Button } from './ui/button'

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

  async function handleSubmit(e: FormEvent) {
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

  return (
    <form onSubmit={handleSubmit} className="mt-3 grid gap-2">
      <strong className="text-sm text-text">Log a maintenance record</strong>
      <Label>
        When
        <Input type="datetime-local" required value={when} onChange={(e) => setWhen(e.target.value)} />
      </Label>
      <Label>
        Description
        <Input
          type="text"
          value={description}
          placeholder="e.g. re-greased bearing"
          onChange={(e) => setDescription(e.target.value)}
        />
      </Label>
      <Label>
        Technician
        <Input type="text" value={technician} onChange={(e) => setTechnician(e.target.value)} />
      </Label>
      <Button type="submit" variant="accent" size="sm" disabled={busy} className="justify-self-start">
        Log maintenance
      </Button>
      {status.kind !== 'idle' && (
        <p className={`text-xs ${status.kind === 'ok' ? 'text-healthy' : 'text-critical'}`}>{status.message}</p>
      )}
    </form>
  )
}
