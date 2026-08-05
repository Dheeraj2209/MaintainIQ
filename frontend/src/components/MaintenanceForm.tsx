import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { Alert, MaintenanceCreate } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { Input, Label, Select } from './ui/input'
import { Button } from './ui/button'

interface Props {
  machineId: string
  onSubmit: (payload: MaintenanceCreate) => Promise<unknown>
  linkedAlert?: Alert | null
}

type Status = { kind: 'idle' | 'ok' | 'err'; message?: string }
type MaintenanceType = 'preventive' | 'corrective'

export function MaintenanceForm({ machineId, onSubmit, linkedAlert = null }: Props) {
  const { user } = useAuth()
  const [when, setWhen] = useState('')
  const [description, setDescription] = useState('')
  const [technician, setTechnician] = useState('')
  const [type, setType] = useState<MaintenanceType>(linkedAlert ? 'corrective' : 'preventive')
  const [chipDismissed, setChipDismissed] = useState(false)
  const [status, setStatus] = useState<Status>({ kind: 'idle' })
  const [busy, setBusy] = useState(false)
  const technicianTouched = useRef(false)

  useEffect(() => {
    if (!technicianTouched.current && user?.name) setTechnician(user.name)
  }, [user])

  useEffect(() => {
    setType(linkedAlert ? 'corrective' : 'preventive')
    setChipDismissed(false)
  }, [linkedAlert])

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
        alert_id: linkedAlert?.id ?? null,
        type,
      })
      setStatus({ kind: 'ok', message: 'Logged.' })
      setWhen('')
      setDescription('')
    } catch (err) {
      setStatus({ kind: 'err', message: err instanceof Error ? err.message : 'Failed to log maintenance' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mt-3 grid gap-2">
      <strong className="text-sm text-text">Log a maintenance record</strong>
      {linkedAlert && !chipDismissed && (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-accent/30 bg-accent/10 px-3 py-1.5 text-xs text-text">
          <span>
            Logging maintenance for alert #{linkedAlert.id}: {linkedAlert.message ?? linkedAlert.health_state}
          </span>
          <button type="button" onClick={() => setChipDismissed(true)} className="text-text-muted hover:text-text" aria-label="Dismiss">
            ×
          </button>
        </div>
      )}
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
        <Input
          type="text"
          value={technician}
          onChange={(e) => {
            technicianTouched.current = true
            setTechnician(e.target.value)
          }}
        />
      </Label>
      <Label>
        Type
        <Select value={type} onChange={(e) => setType(e.target.value as MaintenanceType)}>
          <option value="preventive">Preventive</option>
          <option value="corrective">Corrective</option>
        </Select>
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
