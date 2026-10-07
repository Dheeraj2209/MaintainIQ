// One alert on the phone (design/2026-10-07-mobile-operator-pwa-design.md,
// "Pages"): severity bar, what and when, status chips, and the four quick
// actions as full-size touch targets (min 44 x 44 px, always with a text
// label). Tapping the summary opens /m/alerts/:id.
import { CheckCheck, ClipboardList, HelpCircle, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Alert, UserOut } from '../api/types'
import { Badge } from '../components/ui/badge'
import {
  causeLabel,
  feedbackOutcomeLabel,
  feedbackOutcomeTone,
  healthLabel,
  pageLevelTone,
  wasLadderPaged,
  severityClasses,
} from '../components/healthStyles'
import { cn } from '../lib/cn'
import { canEditFeedback, feedbackMode } from '../lib/feedback'
import { formatRelative } from '../lib/telemetryFormat'
import { PENDING_WORK_ORDER } from './useQuickAlertActions'

interface Props {
  alert: Alert
  user: UserOut | null
  onAcknowledge: (alert: Alert) => void
  onWorkOrder: (alert: Alert) => void
  onClose: (alert: Alert) => void
  // Omitted on the detail page, which is where "Why?" leads.
  onWhy?: (alert: Alert) => void
  // Hide the tappable summary (the detail page shows its own).
  actionsOnly?: boolean
}

const OUTCOME_ACTION = {
  close: { label: 'Close', aria: (id: number) => `Close alert #${id}` },
  record: { label: 'Outcome', aria: (id: number) => `Record outcome for alert #${id}` },
  edit: { label: 'Outcome', aria: (id: number) => `Edit outcome for alert #${id}` },
} as const

function ActionButton({
  label,
  onClick,
  disabled,
  icon,
  children,
  primary,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  icon: ReactNode
  children: ReactNode
  primary?: boolean
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex min-h-11 min-w-11 flex-1 items-center justify-center gap-1.5 rounded-xl border px-3 text-sm font-medium transition active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50',
        primary
          ? 'border-accent/50 bg-accent/20 text-text hover:bg-accent/30'
          : 'border-white/10 bg-white/5 text-text hover:bg-white/10',
      )}
    >
      {icon}
      {children}
    </button>
  )
}

export function MobileAlertCard({ alert, user, onAcknowledge, onWorkOrder, onClose, onWhy, actionsOnly }: Props) {
  const isOpen = alert.status === 'open'
  const acknowledged = alert.acknowledged_at != null
  const workOrderId = alert.active_work_order_id ?? null
  const creatingOrder = workOrderId === PENDING_WORK_ORDER
  const mode = feedbackMode(alert)
  const outcome = OUTCOME_ACTION[mode]

  const chips = (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge className={severityClasses(alert.severity)}>{alert.severity}</Badge>
      {!isOpen && <Badge variant="neutral">Resolved</Badge>}
      {acknowledged && <Badge variant="accent">Acknowledged</Badge>}
      {wasLadderPaged(alert) && <Badge variant={pageLevelTone(alert.page_level)}>Paged L{alert.page_level}</Badge>}
      {creatingOrder && <Badge variant="neutral">Creating work order…</Badge>}
      {alert.feedback && (
        <Badge variant={feedbackOutcomeTone(alert.feedback.outcome)}>{feedbackOutcomeLabel(alert.feedback.outcome)}</Badge>
      )}
    </div>
  )

  return (
    <article aria-label={`Alert #${alert.id} on ${alert.machine_id}`} className="glass flex overflow-hidden rounded-2xl">
      {/* Severity as intensity, paired with the text chip above. */}
      <span aria-hidden className={cn('w-1.5 shrink-0', severityClasses(alert.severity))} />
      <div className="min-w-0 flex-1 space-y-3 p-3">
        {!actionsOnly && (
          <Link to={`/m/alerts/${alert.id}`} className="block space-y-1.5 rounded-lg focus-visible:outline-2 focus-visible:outline-accent">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-base font-semibold text-text">{alert.machine_id}</span>
              <span className="shrink-0 font-mono text-xs text-text-muted">
                #{alert.id} · {formatRelative(alert.opened_at)}
              </span>
            </div>
            <p className="text-sm text-text-muted">
              {healthLabel(alert.health_state)} · {causeLabel(alert.probable_cause)}
            </p>
          </Link>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          {chips}
          {workOrderId != null && !creatingOrder && (
            <Link
              to={`/work-orders/${workOrderId}`}
              className="inline-flex min-h-11 items-center rounded-full px-1 text-xs font-medium text-accent underline-offset-2 hover:underline"
            >
              WO #{workOrderId}
            </Link>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          {isOpen && !acknowledged && (
            <ActionButton
              primary
              label={`Acknowledge alert #${alert.id}`}
              onClick={() => onAcknowledge(alert)}
              icon={<CheckCheck className="h-4 w-4" aria-hidden />}
            >
              Acknowledge
            </ActionButton>
          )}
          {isOpen && workOrderId == null && (
            <ActionButton
              label={`Create work order for alert #${alert.id}`}
              onClick={() => onWorkOrder(alert)}
              icon={<ClipboardList className="h-4 w-4" aria-hidden />}
            >
              Work order
            </ActionButton>
          )}
          <ActionButton
            label={outcome.aria(alert.id)}
            onClick={() => onClose(alert)}
            disabled={creatingOrder || !canEditFeedback(user, alert.feedback)}
            icon={<XCircle className="h-4 w-4" aria-hidden />}
          >
            {outcome.label}
          </ActionButton>
          {onWhy && (
            <ActionButton
              label={`Why alert #${alert.id}?`}
              onClick={() => onWhy(alert)}
              icon={<HelpCircle className="h-4 w-4" aria-hidden />}
            >
              Why?
            </ActionButton>
          )}
        </div>
      </div>
    </article>
  )
}
