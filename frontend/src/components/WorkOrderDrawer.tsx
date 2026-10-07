// Work-order detail drawer (design/2026-10-07-work-orders-escalation-design.md,
// Frontend UX): facts, the linked alert, the append-only event timeline, and
// the actions the signed-in user may take. Actions mirror the server's table —
// assign/cancel/edit for admin and supervisor on a non-terminal order, start and
// complete for the assignee too — and the server stays the authority: a 409
// (someone else got there first) is toasted and the drawer re-fetches.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '../api/client'
import { isWorkOrderEvent } from '../api/types'
import type { Assignee, WorkOrder, WorkOrderDetail, WorkOrderEvent, WorkOrderPriority, WorkOrderUpdate } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { isoToLocalInput, localInputToIso, nowLocalInput } from '../lib/datetime'
import { canEditFeedback } from '../lib/feedback'
import { formatRelative } from '../lib/telemetryFormat'
import { useDialogFocus } from '../lib/useDialogFocus'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { AlertCloseForm } from './AlertCloseForm'
import type { AlertCloseResult } from './AlertCloseForm'
import {
  feedbackOutcomeLabel,
  feedbackOutcomeTone,
  pageLevelTone,
  wasLadderPaged,
  workOrderPriorityTone,
  workOrderStatusLabel,
  workOrderStatusTone,
} from './healthStyles'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input, Label, Select, Textarea } from './ui/input'

interface Props {
  workOrderId: number
  onClose: () => void
  // Called after a successful action, so the owning list can refresh even
  // when the realtime socket is down.
  onChanged?: (workOrder: WorkOrder) => void
}

const TERMINAL = new Set(['done', 'cancelled'])

// 'outcome': the "Record what happened" step offered after completing an
// order raised from an alert (design/2026-10-07-prediction-feedback-design.md).
type Pending = 'complete' | 'cancel' | 'edit' | 'outcome' | null

const PRIORITIES: WorkOrderPriority[] = ['low', 'medium', 'high']

// "Ada Admin created" / "assigned to Sam Supervisor" / "Sam Supervisor started".
function describeEvent(e: WorkOrderEvent): string {
  const who = e.user_name ?? (e.user_id != null ? `User #${e.user_id}` : 'System')
  switch (e.event) {
    case 'assigned': {
      const to = e.assigned_to_name ?? (e.assigned_to != null ? `user #${e.assigned_to}` : 'someone')
      return e.from_status === e.to_status ? `${who} reassigned to ${to}` : `${who} assigned to ${to}`
    }
    case 'created':
    case 'edited':
    case 'started':
    case 'completed':
    case 'cancelled':
      return `${who} ${e.event}`
  }
}

export function WorkOrderDrawer({ workOrderId, onClose, onChanged }: Props) {
  const { user } = useAuth()
  const { lastEvent } = useLiveEvents()
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(true, onClose, panelRef)

  const [detail, setDetail] = useState<WorkOrderDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [assignees, setAssignees] = useState<Assignee[]>([])
  const [assignTo, setAssignTo] = useState('')
  const [pending, setPending] = useState<Pending>(null)
  const [busy, setBusy] = useState(false)
  const [notes, setNotes] = useState('')
  const [performedAt, setPerformedAt] = useState('')
  const [maintenanceType, setMaintenanceType] = useState<'corrective' | 'preventive'>('corrective')
  const [reason, setReason] = useState('')
  const [editTitle, setEditTitle] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editPriority, setEditPriority] = useState<WorkOrderPriority>('medium')
  const [editDue, setEditDue] = useState('')
  // A slower, older load must not overwrite a newer one.
  const loadSeq = useRef(0)

  const supervising = user?.role === 'admin' || user?.role === 'supervisor'

  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const d = await api.getWorkOrder(workOrderId)
      if (seq !== loadSeq.current) return
      setDetail(d)
      setError(null)
    } catch (e) {
      if (seq !== loadSeq.current) return
      setError(e instanceof Error ? e.message : 'Failed to load work order')
    }
  }, [workOrderId])

  useEffect(() => {
    setDetail(null)
    void load()
  }, [load])

  useEffect(() => {
    if (lastEvent && isWorkOrderEvent(lastEvent) && lastEvent.work_order.id === workOrderId) void load()
  }, [lastEvent, load, workOrderId])

  useEffect(() => {
    if (!supervising) return
    let cancelled = false
    api
      .getAssignees()
      .then((a) => !cancelled && setAssignees(a))
      .catch(() => !cancelled && setAssignees([]))
    return () => {
      cancelled = true
    }
  }, [supervising])

  // `after` chains a follow-up step onto a successful action (Complete →
  // record the outcome) without duplicating the error handling.
  async function run(action: () => Promise<WorkOrder>, success: string, after?: (updated: WorkOrder) => void) {
    setBusy(true)
    try {
      const updated = await action()
      toast.success(success)
      setPending(null)
      onChanged?.(updated)
      after?.(updated)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setBusy(false)
      await load()
    }
  }

  function openPending(next: Pending) {
    setPending(next)
    if (next === 'complete') {
      setNotes('')
      setPerformedAt(nowLocalInput())
      setMaintenanceType('corrective')
    } else if (next === 'cancel') {
      setReason('')
    } else if (next === 'edit' && detail) {
      setEditTitle(detail.title)
      setEditDescription(detail.description ?? '')
      setEditPriority(detail.priority)
      setEditDue(isoToLocalInput(detail.due_at))
    }
  }

  function handleAssign(e: FormEvent) {
    e.preventDefault()
    if (!assignTo) return
    void run(() => api.assignWorkOrder(workOrderId, Number(assignTo)), `Work order #${workOrderId} assigned`)
  }

  function handleComplete(e: FormEvent) {
    e.preventDefault()
    const performed = localInputToIso(performedAt)
    void run(
      () =>
        api.completeWorkOrder(workOrderId, {
          ...(notes.trim() ? { notes: notes.trim() } : {}),
          ...(performed ? { performed_at: performed } : {}),
          maintenance_type: maintenanceType,
        }),
      `Work order #${workOrderId} completed — maintenance logged`,
      () => {
        // Offer the outcome only when this user may record it: no feedback
        // yet, or feedback they're allowed to change.
        if (detail?.alert && canEditFeedback(user, detail.alert.feedback)) setPending('outcome')
      },
    )
  }

  function handleOutcomeSaved(result: AlertCloseResult) {
    setPending(null)
    toast.success(result.closed ? `Alert #${result.alert.id} closed` : 'Outcome recorded')
    void load()
  }

  function handleCancel(e: FormEvent) {
    e.preventDefault()
    void run(() => api.cancelWorkOrder(workOrderId, reason.trim() || undefined), `Work order #${workOrderId} cancelled`)
  }

  // Sends only the fields that differ from the loaded order; an emptied
  // description or due date is sent as null (clears it). Nothing changed:
  // just close the form — the server would not record an edit anyway.
  function handleEdit(e: FormEvent) {
    e.preventDefault()
    if (!detail) return
    const changes: WorkOrderUpdate = {}
    const title = editTitle.trim()
    if (title && title !== detail.title) changes.title = title
    const description = editDescription.trim() || null
    if (description !== (detail.description ?? null)) changes.description = description
    if (editPriority !== detail.priority) changes.priority = editPriority
    if (editDue !== isoToLocalInput(detail.due_at)) changes.due_at = localInputToIso(editDue)
    if (Object.keys(changes).length === 0) {
      setPending(null)
      return
    }
    void run(() => api.updateWorkOrder(workOrderId, changes), `Work order #${workOrderId} updated`)
  }

  const isAssignee = detail != null && user != null && detail.assigned_to === user.id
  const terminal = detail != null && TERMINAL.has(detail.status)
  const canAssign = detail != null && supervising && !terminal
  const canCancel = canAssign
  const canEdit = canAssign
  const canStart = detail?.status === 'assigned' && (supervising || isAssignee)
  const canComplete = detail?.status === 'in_progress' && (supervising || isAssignee)
  const now = Date.now()

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-bg/60 backdrop-blur-sm" onClick={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="work-order-drawer-heading"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="glass-panel m-3 flex w-full max-w-md flex-col overflow-y-auto rounded-2xl border p-5 focus:outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="work-order-drawer-heading" className="font-mono text-sm uppercase tracking-wide text-text-muted">
            Work order #{workOrderId}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-lg leading-none text-text-muted hover:text-text">
            ×
          </button>
        </div>

        {error && (
          <div role="alert" className="mt-3 rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
            {error}
          </div>
        )}

        {!detail ? (
          !error && <p className="mt-4 text-sm text-text-muted">Loading…</p>
        ) : (
          <>
            <h3 className="mt-2 text-lg font-semibold text-text">{detail.title}</h3>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge variant={workOrderStatusTone(detail.status)}>{workOrderStatusLabel(detail.status)}</Badge>
              <Badge variant={workOrderPriorityTone(detail.priority)}>{detail.priority} priority</Badge>
            </div>
            {detail.description && <p className="mt-3 whitespace-pre-line text-sm text-text-muted">{detail.description}</p>}

            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <Fact label="Machine">
                <Link to={`/machines/${encodeURIComponent(detail.machine_id)}`} className="font-mono text-accent hover:text-accent-hover">
                  {detail.machine_id}
                </Link>
              </Fact>
              <Fact label="Assignee">{detail.assigned_to_name ?? (detail.assigned_to != null ? `User #${detail.assigned_to}` : 'Unassigned')}</Fact>
              <Fact label="Created by">
                {detail.created_by_name ?? '—'} · <When iso={detail.created_at} now={now} />
              </Fact>
              <Fact label="Due">{detail.due_at ? <When iso={detail.due_at} now={now} absolute /> : '—'}</Fact>
              {detail.started_at && (
                <Fact label="Started">
                  <When iso={detail.started_at} now={now} />
                </Fact>
              )}
              {detail.completed_at && (
                <Fact label="Completed">
                  <When iso={detail.completed_at} now={now} />
                </Fact>
              )}
              {detail.cancelled_at && (
                <Fact label="Cancelled">
                  <When iso={detail.cancelled_at} now={now} />
                </Fact>
              )}
              {detail.maintenance_record_id != null && (
                <Fact label="Maintenance record">
                  <span className="font-mono">#{detail.maintenance_record_id}</span>
                </Fact>
              )}
            </dl>
            {detail.notes && (
              <p className="mt-2 text-sm text-text">
                <span className="text-xs uppercase tracking-wide text-text-muted">Notes </span>
                {detail.notes}
              </p>
            )}

            {detail.alert && (
              <section aria-label="Linked alert" className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-text">Alert #{detail.alert.id}</span>
                  <Badge variant={workOrderPriorityTone(detail.alert.severity)}>{detail.alert.severity}</Badge>
                  <span className="text-xs text-text-muted">{detail.alert.status}</span>
                  {detail.alert.feedback && (
                    <Badge variant={feedbackOutcomeTone(detail.alert.feedback.outcome)}>
                      {feedbackOutcomeLabel(detail.alert.feedback.outcome)}
                    </Badge>
                  )}
                  {wasLadderPaged(detail.alert) && (
                    <Badge
                      variant={pageLevelTone(detail.alert.page_level)}
                      title={detail.alert.last_paged_at ? `Last paged ${detail.alert.last_paged_at}` : undefined}
                    >
                      Paged L{detail.alert.page_level}
                    </Badge>
                  )}
                </div>
                {detail.alert.message && <p className="mt-1 text-xs text-text-muted">{detail.alert.message}</p>}
              </section>
            )}

            {pending === 'outcome' && detail.alert && (
              <section aria-labelledby="record-outcome-heading" className="mt-4 rounded-xl border border-accent/30 bg-accent/[0.06] p-3">
                <h4 id="record-outcome-heading" className="mb-2 text-sm font-semibold text-text">
                  Record what happened
                </h4>
                <AlertCloseForm
                  alert={detail.alert}
                  workOrderId={detail.id}
                  initialNotes={notes.trim()}
                  cancelLabel="Skip"
                  onSaved={handleOutcomeSaved}
                  onCancel={() => setPending(null)}
                />
              </section>
            )}

            {(canAssign || canStart || canComplete || canCancel || canEdit) && (
              <section aria-label="Actions" className="mt-4 space-y-3 border-t border-white/10 pt-4">
                {canAssign && (
                  <form onSubmit={handleAssign} className="flex items-end gap-2">
                    <Label className="flex-1">
                      New assignee
                      <Select value={assignTo} onChange={(e) => setAssignTo(e.target.value)}>
                        <option value="">Choose…</option>
                        {assignees.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name} ({a.role})
                          </option>
                        ))}
                      </Select>
                    </Label>
                    <Button type="submit" size="sm" disabled={busy || !assignTo}>
                      {detail.assigned_to != null ? 'Reassign' : 'Assign'}
                    </Button>
                  </form>
                )}

                <div className="flex flex-wrap gap-2">
                  {canStart && (
                    <Button type="button" variant="accent" size="sm" disabled={busy} onClick={() => void run(() => api.startWorkOrder(workOrderId), `Work order #${workOrderId} started`)}>
                      Start work
                    </Button>
                  )}
                  {canComplete && pending !== 'complete' && (
                    <Button type="button" variant="accent" size="sm" disabled={busy} onClick={() => openPending('complete')}>
                      Complete
                    </Button>
                  )}
                  {canCancel && pending !== 'cancel' && (
                    <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => openPending('cancel')}>
                      Cancel work order
                    </Button>
                  )}
                  {canEdit && pending !== 'edit' && (
                    <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => openPending('edit')}>
                      Edit
                    </Button>
                  )}
                </div>

                {canEdit && pending === 'edit' && (
                  <form onSubmit={handleEdit} className="grid gap-2 rounded-xl border border-white/10 p-3">
                    <Label>
                      Title
                      <Input type="text" required maxLength={200} value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
                    </Label>
                    <Label>
                      Description
                      <Textarea maxLength={2000} value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
                    </Label>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <Label>
                        Priority
                        <Select value={editPriority} onChange={(e) => setEditPriority(e.target.value as WorkOrderPriority)}>
                          {PRIORITIES.map((p) => (
                            <option key={p} value={p}>
                              {p[0].toUpperCase() + p.slice(1)}
                            </option>
                          ))}
                        </Select>
                      </Label>
                      <Label>
                        Due
                        <Input type="datetime-local" value={editDue} onChange={(e) => setEditDue(e.target.value)} />
                      </Label>
                    </div>
                    <div className="flex gap-2">
                      <Button type="submit" variant="accent" size="sm" disabled={busy}>
                        Save changes
                      </Button>
                      <Button type="button" variant="ghost" size="sm" onClick={() => setPending(null)}>
                        Back
                      </Button>
                    </div>
                  </form>
                )}

                {canComplete && pending === 'complete' && (
                  <form onSubmit={handleComplete} className="grid gap-2 rounded-xl border border-white/10 p-3">
                    <p className="text-xs text-text-muted">Completing logs a maintenance record for {detail.machine_id}.</p>
                    <Label>
                      Completion notes
                      <Textarea maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} />
                    </Label>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <Label>
                        Performed at
                        <Input type="datetime-local" value={performedAt} onChange={(e) => setPerformedAt(e.target.value)} />
                      </Label>
                      <Label>
                        Type
                        <Select value={maintenanceType} onChange={(e) => setMaintenanceType(e.target.value as 'corrective' | 'preventive')}>
                          <option value="corrective">Corrective</option>
                          <option value="preventive">Preventive</option>
                        </Select>
                      </Label>
                    </div>
                    <div className="flex gap-2">
                      <Button type="submit" variant="accent" size="sm" disabled={busy}>
                        Confirm completion
                      </Button>
                      <Button type="button" variant="ghost" size="sm" onClick={() => setPending(null)}>
                        Back
                      </Button>
                    </div>
                  </form>
                )}

                {canCancel && pending === 'cancel' && (
                  <form onSubmit={handleCancel} className="grid gap-2 rounded-xl border border-white/10 p-3">
                    <Label>
                      Cancel reason
                      <Input type="text" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
                    </Label>
                    <div className="flex gap-2">
                      <Button type="submit" variant="outline" size="sm" disabled={busy}>
                        Confirm cancel
                      </Button>
                      <Button type="button" variant="ghost" size="sm" onClick={() => setPending(null)}>
                        Back
                      </Button>
                    </div>
                  </form>
                )}
              </section>
            )}

            <section aria-labelledby="work-order-timeline-heading" className="mt-4 border-t border-white/10 pt-4">
              <h4 id="work-order-timeline-heading" className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                Timeline
              </h4>
              <ol aria-label="Timeline" className="mt-2 space-y-2">
                {detail.events.map((e) => (
                  <li key={e.id} className="border-l-2 border-accent/30 pl-3 text-sm">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="text-text">{describeEvent(e)}</span>
                      <When iso={e.created_at} now={now} className="text-xs text-text-muted" />
                    </div>
                    {e.note && <p className="text-xs text-text-muted">{e.note}</p>}
                  </li>
                ))}
              </ol>
            </section>
          </>
        )}
      </div>
    </div>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-text-muted">{label}</dt>
      <dd className="text-text">{children}</dd>
    </div>
  )
}

// Relative time with the absolute timestamp on hover (or the reverse, for
// due dates, where the calendar time is the useful part).
function When({ iso, now, absolute, className }: { iso: string; now: number; absolute?: boolean; className?: string }) {
  if (absolute) {
    const t = new Date(iso)
    return (
      <time dateTime={iso} title={iso} className={className}>
        {Number.isNaN(t.getTime()) ? iso : t.toLocaleString()}
      </time>
    )
  }
  return (
    <time dateTime={iso} title={iso} className={className}>
      {formatRelative(iso, now)}
    </time>
  )
}
