// Shared rules for prediction feedback (design/2026-10-07-prediction-feedback-design.md),
// kept out of component files so those only export components (fast refresh).
import type { Alert, AlertFeedback, FeedbackCause, FeedbackOutcome, UserOut } from '../api/types'

export const FEEDBACK_OUTCOMES: { value: FeedbackOutcome; description: string }[] = [
  { value: 'confirmed_failure', description: 'The machine failed, or was found failed.' },
  { value: 'maintenance_prevented', description: 'A real developing fault, fixed before it failed.' },
  { value: 'false_alarm', description: 'Nothing was wrong with the machine.' },
  { value: 'unknown', description: "Closed without knowing — left out of accuracy numbers." },
]

export const FEEDBACK_CAUSES: FeedbackCause[] = [
  'bearing_wear',
  'imbalance',
  'sensor_or_data_quality_issue',
  'unknown',
  'other',
]

// What the close/record form does for this alert: an open alert is closed
// (and its outcome recorded) in one call; a resolved one only records or
// edits the outcome.
export type FeedbackMode = 'close' | 'record' | 'edit'

export function feedbackMode(alert: Alert): FeedbackMode {
  if (alert.status === 'open') return 'close'
  return alert.feedback ? 'edit' : 'record'
}

export const FEEDBACK_MODE_TITLE: Record<FeedbackMode, string> = {
  close: 'Close alert',
  record: 'Record outcome',
  edit: 'Edit outcome',
}

// The server's rule: anyone may record the first outcome; replacing one is
// for its recorder, an admin or a supervisor.
export function canEditFeedback(user: UserOut | null, feedback: AlertFeedback | null | undefined): boolean {
  if (!feedback) return true
  if (!user) return false
  return user.role === 'admin' || user.role === 'supervisor' || user.id === feedback.recorded_by
}
