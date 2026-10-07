// "Why this alert?" drawer (design/2026-10-07-alert-explanation-design.md,
// Frontend UX): the same chrome as WorkOrderDrawer around AlertExplanationView.
// The fetch and live re-fetch live in useAlertExplanation, shared with the
// mobile alert page, so the similar-incident outcomes and the snapshot stay
// current while it is open.
import { useRef } from 'react'
import { useAlertExplanation } from '../lib/useAlertExplanation'
import { useDialogFocus } from '../lib/useDialogFocus'
import { AlertExplanationView } from './AlertExplanationView'

interface Props {
  alertId: number
  onClose: () => void
}

export function AlertExplanationPanel({ alertId, onClose }: Props) {
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(true, onClose, panelRef)
  const { explanation, error } = useAlertExplanation(alertId)

  const subject = explanation ? explanation.alert.machine_id : `Alert #${alertId}`

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-bg/60 backdrop-blur-sm" onClick={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="alert-explanation-heading"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="glass-panel m-3 flex w-full max-w-2xl flex-col overflow-y-auto rounded-2xl border p-5 focus:outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="alert-explanation-heading" className="text-lg font-semibold text-text">
            Why this alert? — {subject}
          </h2>
          <button
            type="button"
            data-autofocus
            onClick={onClose}
            aria-label="Close"
            className="text-lg leading-none text-text-muted hover:text-text"
          >
            ×
          </button>
        </div>

        {error && (
          <div role="alert" className="mt-3 rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
            {error}
          </div>
        )}

        <div className="mt-3">
          {explanation ? (
            <AlertExplanationView explanation={explanation} />
          ) : (
            !error && <p className="text-sm text-text-muted">Loading explanation…</p>
          )}
        </div>
      </div>
    </div>
  )
}
