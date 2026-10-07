// Close an alert / record what actually happened (design/2026-10-07-prediction-feedback-design.md,
// Frontend UX). Used by AlertCloseDialog and inline by the work-order drawer's
// "Record what happened" step after completion. The mode follows the alert:
// an open alert is closed (POST /close, which also records the outcome), a
// resolved one gets its outcome recorded or edited (PUT /feedback). No outcome
// is preselected, so nobody records one by just clicking through.
import { useId, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api/client'
import type { Alert, AlertFeedback, AlertFeedbackIn, FeedbackCause, FeedbackOutcome } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { isoToLocalInput, localInputToIso, nowLocalInput } from '../lib/datetime'
import { FEEDBACK_CAUSES, FEEDBACK_MODE_TITLE, FEEDBACK_OUTCOMES, canEditFeedback, feedbackMode } from '../lib/feedback'
import { causeLabel, feedbackOutcomeLabel, feedbackOutcomeTone } from './healthStyles'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input, Label, Select, Textarea } from './ui/input'

export interface AlertCloseResult {
  alert: Alert
  feedback: AlertFeedback
  closed: boolean
}

interface Props {
  alert: Alert
  // The work order this outcome is about (the drawer's order); defaults to
  // the existing feedback's, then the alert's active order.
  workOrderId?: number | null
  // Prefills notes when there is no existing feedback (completion notes).
  initialNotes?: string
  onSaved: (result: AlertCloseResult) => void
  onCancel: () => void
  cancelLabel?: string
}

const SUBMIT_LABEL = { close: 'Close alert', record: 'Record outcome', edit: 'Save outcome' } as const

export function AlertCloseForm({ alert, workOrderId, initialNotes = '', onSaved, onCancel, cancelLabel = 'Cancel' }: Props) {
  const { user } = useAuth()
  const existing = alert.feedback ?? null
  const mode = feedbackMode(alert)
  const readOnly = !canEditFeedback(user, existing)
  const linkedWorkOrder = workOrderId ?? existing?.work_order_id ?? alert.active_work_order_id ?? null
  const ids = useId()

  const [outcome, setOutcome] = useState<FeedbackOutcome | null>(existing?.outcome ?? null)
  const [cause, setCause] = useState<FeedbackCause | ''>(existing?.actual_cause ?? '')
  const [failureAt, setFailureAt] = useState(() => isoToLocalInput(existing?.actual_failure_at))
  const [notes, setNotes] = useState(existing ? (existing.notes ?? '') : initialNotes)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (readOnly && existing) {
    return (
      <div className="grid gap-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={feedbackOutcomeTone(existing.outcome)}>{feedbackOutcomeLabel(existing.outcome)}</Badge>
          <span className="text-text-muted">Cause: {causeLabel(existing.actual_cause)}</span>
        </div>
        {existing.notes && <p className="whitespace-pre-line text-text">{existing.notes}</p>}
        <p className="text-xs text-text-muted">
          Recorded by {existing.recorded_by_name ?? `user #${existing.recorded_by}`} — only they, an admin or a
          supervisor can change it.
        </p>
        <div className="flex justify-end">
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
            {cancelLabel}
          </Button>
        </div>
      </div>
    )
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!outcome) return
    setBusy(true)
    setError(null)
    // Every key is sent, so an edit can also clear a field.
    const payload: AlertFeedbackIn = {
      outcome,
      actual_cause: cause || null,
      actual_failure_at: outcome === 'confirmed_failure' ? localInputToIso(failureAt) : null,
      notes: notes.trim() || null,
      work_order_id: linkedWorkOrder,
    }
    try {
      if (mode === 'close') {
        onSaved(await api.closeAlert(alert.id, payload))
      } else {
        const feedback = await api.submitAlertFeedback(alert.id, payload)
        onSaved({ alert: { ...alert, feedback }, feedback, closed: false })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save the outcome')
    } finally {
      setBusy(false)
    }
  }

  const legendId = `${ids}-legend`

  return (
    <form onSubmit={handleSubmit} className="grid gap-3" aria-label={FEEDBACK_MODE_TITLE[mode]}>
      <fieldset role="radiogroup" aria-labelledby={legendId} className="grid gap-1.5">
        <legend id={legendId} className="mb-1 text-xs font-medium text-text-muted">
          What actually happened?
        </legend>
        {FEEDBACK_OUTCOMES.map((o, i) => {
          const id = `${ids}-${o.value}`
          return (
            <div
              key={o.value}
              className={`flex items-start gap-2 rounded-xl border px-3 py-2 transition ${
                outcome === o.value ? 'border-accent/50 bg-accent/10' : 'border-white/10 bg-white/[0.03] hover:bg-white/[0.06]'
              }`}
            >
              <input
                id={id}
                type="radio"
                name={`${ids}-outcome`}
                value={o.value}
                checked={outcome === o.value}
                onChange={() => setOutcome(o.value)}
                aria-describedby={`${id}-desc`}
                data-autofocus={i === 0 ? true : undefined}
                className="mt-0.5 accent-[var(--color-accent)]"
              />
              <label htmlFor={id} className="grid cursor-pointer gap-0.5 text-sm text-text">
                {feedbackOutcomeLabel(o.value)}
                <span id={`${id}-desc`} className="text-xs text-text-muted">
                  {o.description}
                </span>
              </label>
            </div>
          )
        })}
      </fieldset>

      <div className="grid gap-1">
        <Label>
          Actual cause
          <Select
            value={cause}
            aria-describedby={`${ids}-cause-hint`}
            onChange={(e) => setCause(e.target.value as FeedbackCause | '')}
          >
            <option value="">Not specified</option>
            {FEEDBACK_CAUSES.map((c) => (
              <option key={c} value={c}>
                {causeLabel(c)}
              </option>
            ))}
          </Select>
        </Label>
        <p id={`${ids}-cause-hint`} className="text-xs text-text-muted">
          Model said: {causeLabel(alert.probable_cause)}
        </p>
      </div>

      {outcome === 'confirmed_failure' && (
        <Label>
          Failure time (optional)
          <Input type="datetime-local" max={nowLocalInput()} value={failureAt} onChange={(e) => setFailureAt(e.target.value)} />
        </Label>
      )}

      <Label>
        Notes
        <Textarea maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Label>

      {linkedWorkOrder != null && <p className="text-xs text-text-muted">Linked to work order #{linkedWorkOrder}</p>}
      {mode === 'close' && (
        <p className="text-xs text-text-muted">
          Closing resolves this alert now. If the machine still reads abnormal, the next reading opens a new alert.
        </p>
      )}
      {alert.active_work_order_id != null && alert.active_work_order_id !== workOrderId && (
        <p className="text-xs text-text-muted">
          Work order #{alert.active_work_order_id} stays open — complete or cancel it separately.
        </p>
      )}

      {error && (
        <div role="alert" className="rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
          {error}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          {cancelLabel}
        </Button>
        <Button type="submit" variant="accent" size="sm" disabled={busy || !outcome}>
          {busy ? 'Saving…' : SUBMIT_LABEL[mode]}
        </Button>
      </div>
    </form>
  )
}
