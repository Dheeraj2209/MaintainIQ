// Modal wrapper around AlertCloseForm (design/2026-10-07-prediction-feedback-design.md,
// Frontend UX). Pages own the open state, as with CreateWorkOrderDialog:
// MachineDetailPage keeps it outside MachineDetail, which remounts on every
// live event for the machine and would drop a half-filled form.
import { useRef } from 'react'
import type { Alert } from '../api/types'
import { FEEDBACK_MODE_TITLE, feedbackMode } from '../lib/feedback'
import { useDialogFocus } from '../lib/useDialogFocus'
import { AlertCloseForm } from './AlertCloseForm'
import type { AlertCloseResult } from './AlertCloseForm'

interface Props {
  open: boolean
  alert: Alert | null
  workOrderId?: number | null
  onClose: () => void
  onSaved: (result: AlertCloseResult) => void
}

export function AlertCloseDialog(props: Props) {
  if (!props.open || !props.alert) return null
  // Keyed so every opening starts from a form prefilled from that alert.
  return <DialogBody key={props.alert.id} {...props} alert={props.alert} />
}

function DialogBody({ alert, workOrderId, onClose, onSaved }: Props & { alert: Alert }) {
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(true, onClose, panelRef)
  const title = FEEDBACK_MODE_TITLE[feedbackMode(alert)]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="alert-close-heading"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="glass-panel max-h-[calc(100dvh-2rem)] w-full max-w-lg overflow-y-auto rounded-2xl border p-5 focus:outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="alert-close-heading" className="text-lg font-semibold text-text">
              {title}
            </h2>
            <p className="mt-0.5 text-xs text-text-muted">
              Alert #{alert.id} on {alert.machine_id} · {alert.severity} · {alert.status}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Dismiss" className="text-text-muted hover:text-text">
            ×
          </button>
        </div>
        <div className="mt-4">
          <AlertCloseForm alert={alert} workOrderId={workOrderId} onSaved={onSaved} onCancel={onClose} />
        </div>
      </div>
    </div>
  )
}
