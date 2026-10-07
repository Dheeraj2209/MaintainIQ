// "Create work order" modal (design/2026-10-07-work-orders-escalation-design.md,
// Frontend UX). Raised from an alert (any role; the server also acknowledges
// the alert) or free-standing for a machine (admin/supervisor only). Pages own
// the open state: MachineDetailPage keeps it outside MachineDetail, which is
// remounted on every live event for the machine and would lose a half-filled
// form.
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { Alert, Assignee, MachineSummary, WorkOrder, WorkOrderPriority } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { localInputToIso } from '../lib/datetime'
import { useDialogFocus } from '../lib/useDialogFocus'
import { Button } from './ui/button'
import { Input, Label, Select, Textarea } from './ui/input'

interface Props {
  open: boolean
  // Set: create from this alert. Unset: a free-standing order.
  alert?: Alert | null
  // Free-standing orders for a known machine skip the machine picker.
  machineId?: string
  onClose: () => void
  onCreated: (workOrder: WorkOrder) => void
}

const PRIORITIES: WorkOrderPriority[] = ['low', 'medium', 'high']

// Decision 13: the alert's severity maps straight onto a priority.
function priorityFor(alert: Alert | null | undefined): WorkOrderPriority {
  const severity = alert?.severity
  return severity === 'low' || severity === 'medium' || severity === 'high' ? severity : 'medium'
}

function defaultTitle(alert: Alert | null | undefined): string {
  if (!alert) return ''
  return alert.message ?? `${alert.machine_id}: ${alert.health_state} alert`
}

export function CreateWorkOrderDialog(props: Props) {
  if (!props.open) return null
  // Keyed so every opening starts from a fresh form prefilled from the alert.
  return <DialogBody key={props.alert?.id ?? `machine-${props.machineId ?? ''}`} {...props} />
}

function DialogBody({ alert = null, machineId, onClose, onCreated }: Props) {
  const { user } = useAuth()
  const canAssign = user?.role === 'admin' || user?.role === 'supervisor'
  const pickMachine = !alert && !machineId
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(true, onClose, panelRef)

  const [title, setTitle] = useState(() => defaultTitle(alert))
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<WorkOrderPriority>(() => priorityFor(alert))
  const [assignedTo, setAssignedTo] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [machine, setMachine] = useState('')
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [assignees, setAssignees] = useState<Assignee[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; existingId: number | null } | null>(null)

  // Operators can't assign (the server would 403), so they never ask.
  useEffect(() => {
    if (!canAssign) return
    let cancelled = false
    api
      .getAssignees()
      .then((a) => !cancelled && setAssignees(a))
      .catch(() => !cancelled && setAssignees([]))
    return () => {
      cancelled = true
    }
  }, [canAssign])

  useEffect(() => {
    if (!pickMachine) return
    let cancelled = false
    api
      .getMachines()
      .then((m) => !cancelled && setMachines(m))
      .catch(() => !cancelled && setMachines([]))
    return () => {
      cancelled = true
    }
  }, [pickMachine])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const dueIso = localInputToIso(dueAt)
    const optional = {
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(canAssign && assignedTo ? { assigned_to: Number(assignedTo) } : {}),
      ...(dueIso ? { due_at: dueIso } : {}),
    }
    try {
      const created = alert
        ? await api.createWorkOrderFromAlert(alert.id, { title: title.trim(), ...optional, priority })
        : await api.createWorkOrder({
            machine_id: machineId ?? machine,
            title: title.trim(),
            ...optional,
            priority,
          })
      onCreated(created)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create work order'
      // 409 "alert {id} already has an active work order: #{wo}" — offer the way there.
      const match = err instanceof ApiError && err.status === 409 ? /#(\d+)/.exec(message) : null
      setError({ message, existingId: match ? Number(match[1]) : null })
    } finally {
      setBusy(false)
    }
  }

  const heading = alert ? 'Create work order' : 'New work order'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-work-order-heading"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="glass-panel max-h-[calc(100dvh-2rem)] w-full max-w-lg overflow-y-auto rounded-2xl border p-5 focus:outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="create-work-order-heading" className="text-lg font-semibold text-text">
              {heading}
            </h2>
            <p className="mt-0.5 text-xs text-text-muted">
              {alert
                ? `From alert #${alert.id} on ${alert.machine_id}${alert.acknowledged_at ? '' : ' — this also acknowledges it'}`
                : machineId
                  ? `Machine ${machineId}`
                  : 'Not tied to an alert'}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-text-muted hover:text-text">
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 grid gap-3">
          {pickMachine && (
            <Label>
              Machine
              <Select required value={machine} onChange={(e) => setMachine(e.target.value)}>
                <option value="" disabled>
                  Select a machine
                </option>
                {machines.map((m) => (
                  <option key={m.machine_id} value={m.machine_id}>
                    {m.machine_id}
                  </option>
                ))}
              </Select>
            </Label>
          )}
          <Label>
            Title
            <Input
              data-autofocus
              type="text"
              required
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Label>
          <Label>
            Description
            <Textarea maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Label>
          <div className="grid gap-3 sm:grid-cols-2">
            <Label>
              Priority
              <Select value={priority} onChange={(e) => setPriority(e.target.value as WorkOrderPriority)}>
                {PRIORITIES.map((p) => (
                  <option key={p} value={p}>
                    {p.charAt(0).toUpperCase() + p.slice(1)}
                  </option>
                ))}
              </Select>
            </Label>
            <Label>
              Due (optional)
              <Input type="datetime-local" value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
            </Label>
          </div>
          {canAssign && (
            <Label>
              Assign to
              <Select value={assignedTo} onChange={(e) => setAssignedTo(e.target.value)}>
                <option value="">Unassigned</option>
                {assignees.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.role})
                  </option>
                ))}
              </Select>
            </Label>
          )}

          {error && (
            <div role="alert" className="rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
              {error.message}
              {error.existingId != null && (
                <>
                  {' '}
                  <Link to={`/work-orders/${error.existingId}`} className="font-medium text-accent underline hover:text-accent-hover">
                    Open work order #{error.existingId}
                  </Link>
                </>
              )}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="accent" size="sm" disabled={busy}>
              {busy ? 'Creating…' : 'Create work order'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
