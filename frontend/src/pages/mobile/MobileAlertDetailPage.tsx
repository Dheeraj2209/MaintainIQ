// /m/alerts/:id (design/2026-10-07-mobile-operator-pwa-design.md, "Pages"):
// where a push tap lands. A summary, the quick actions sticky above the tab
// bar, then the full "Why this alert?" explanation (the desktop drawer's
// view). Kept current by useAlertExplanation's live re-fetch.
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import type { Alert } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import { AlertCloseDialog } from '../../components/AlertCloseDialog'
import type { AlertCloseResult } from '../../components/AlertCloseForm'
import { AlertExplanationView } from '../../components/AlertExplanationView'
import { healthLabel } from '../../components/healthStyles'
import { useAlertExplanation } from '../../lib/useAlertExplanation'
import { formatRelative } from '../../lib/telemetryFormat'
import { MobileAlertCard } from '../../mobile/MobileAlertCard'
import { useQuickAlertActions } from '../../mobile/useQuickAlertActions'

export function MobileAlertDetailPage() {
  const { id } = useParams<{ id: string }>()
  const alertId = Number(id)
  // Keyed so another id starts from a clean slate (no stale explanation).
  if (!Number.isInteger(alertId) || alertId <= 0) return <NotFound alertId={id ?? ''} />
  return <AlertDetail key={alertId} alertId={alertId} />
}

function NotFound({ alertId }: { alertId: number | string }) {
  return (
    <div className="space-y-3 py-6 text-center">
      <p role="alert" className="text-sm text-critical">
        Alert #{alertId} not found
      </p>
      <Link to="/m/alerts" className="inline-flex min-h-11 items-center text-sm text-accent">
        ← Back to alerts
      </Link>
    </div>
  )
}

function AlertDetail({ alertId }: { alertId: number }) {
  const { user } = useAuth()
  const { explanation, error, notFound, reload } = useAlertExplanation(alertId)
  const { alerts, setServerAlerts, replaceAlert, acknowledge, createWorkOrder } = useQuickAlertActions({
    userId: user?.id,
    reload: () => void reload(),
  })
  const [closing, setClosing] = useState<Alert | null>(null)

  // The explanation carries the alert; it is the "server row" the quick
  // actions lay their optimistic edits over.
  useEffect(() => {
    if (explanation) setServerAlerts([explanation.alert])
  }, [explanation, setServerAlerts])

  if (notFound) return <NotFound alertId={alertId} />

  const alert = alerts[0] ?? null

  function handleClosed(result: AlertCloseResult) {
    setClosing(null)
    toast.success(result.closed ? `Alert #${result.alert.id} closed` : 'Outcome recorded')
    replaceAlert(result.alert)
  }

  return (
    <div className="space-y-4">
      <Link to="/m/alerts" className="inline-flex min-h-11 items-center text-sm text-accent">
        ← Back to alerts
      </Link>

      {error && (
        <p role="alert" className="rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
          {error}
        </p>
      )}

      {!alert ? (
        !error && <p className="py-6 text-center text-sm text-text-muted">Loading alert…</p>
      ) : (
        <>
          <section aria-label="Alert summary" className="glass space-y-1 rounded-2xl p-4">
            <h1 className="text-xl font-bold text-text">
              {alert.machine_id} <span className="font-mono text-sm font-normal text-text-muted">#{alert.id}</span>
            </h1>
            <p className="text-sm text-text-muted">
              {healthLabel(alert.health_state)} · opened {formatRelative(alert.opened_at)}
              {alert.status !== 'open' && ' · resolved'}
            </p>
            <Link to={`/alerts/${alert.id}`} className="inline-flex min-h-11 items-center text-xs text-accent">
              Desktop view
            </Link>
          </section>

          {/* Sticky just above the bottom tab bar, so the actions stay in
              reach while reading the explanation. */}
          <div className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10">
            <MobileAlertCard
              alert={alert}
              user={user}
              actionsOnly
              onAcknowledge={(a) => void acknowledge(a)}
              onWorkOrder={(a) => void createWorkOrder(a)}
              onClose={setClosing}
              onWhy={() => document.getElementById('why')?.scrollIntoView?.({ behavior: 'smooth' })}
            />
          </div>

          <section id="why" aria-labelledby="why-heading" className="scroll-mt-20 space-y-3">
            <h2 id="why-heading" className="text-lg font-semibold text-text">
              Why this alert?
            </h2>
            {explanation && <AlertExplanationView explanation={explanation} />}
          </section>
        </>
      )}

      <AlertCloseDialog open={closing != null} alert={closing} onClose={() => setClosing(null)} onSaved={handleClosed} />
    </div>
  )
}
