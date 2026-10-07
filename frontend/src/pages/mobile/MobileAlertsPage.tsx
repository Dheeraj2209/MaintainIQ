// /m/alerts (design/2026-10-07-mobile-operator-pwa-design.md, "Pages"): the
// operator's queue. Open (default) or the 50 most recent, as cards with the
// quick actions. Re-fetches on any live event about an alert or a work order.
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '../../api/client'
import { isWorkOrderEvent } from '../../api/types'
import type { Alert, LiveEvent } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import { AlertCloseDialog } from '../../components/AlertCloseDialog'
import type { AlertCloseResult } from '../../components/AlertCloseForm'
import { cn } from '../../lib/cn'
import { MobileAlertCard } from '../../mobile/MobileAlertCard'
import { useQuickAlertActions } from '../../mobile/useQuickAlertActions'
import { useLiveEvents } from '../../realtime/LiveEventsProvider'

type Filter = 'open' | 'recent'

const RECENT_LIMIT = 50

// Alert, paging and feedback events carry an `alert`; work-order events can
// add or clear a card's "WO #n" chip. Device events change no alert.
function touchesAlerts(event: LiveEvent): boolean {
  return 'alert' in event || isWorkOrderEvent(event)
}

export function MobileAlertsPage() {
  const { user } = useAuth()
  const { lastEvent } = useLiveEvents()
  const navigate = useNavigate()
  const [filter, setFilter] = useState<Filter>('open')
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [closing, setClosing] = useState<Alert | null>(null)

  // Bumped to force a re-fetch (after a 409 on "Work order").
  const [reloadTick, setReloadTick] = useState(0)
  const reload = useCallback(() => setReloadTick((n) => n + 1), [])
  const { alerts, setServerAlerts, replaceAlert, removeAlert, acknowledge, createWorkOrder } = useQuickAlertActions({
    userId: user?.id,
    reload,
  })

  const load = useCallback(async () => {
    try {
      const rows = await api.getAlerts(filter === 'open' ? 'open' : undefined)
      setServerAlerts(filter === 'recent' ? rows.slice(0, RECENT_LIMIT) : rows)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load alerts')
    } finally {
      setLoaded(true)
    }
  }, [filter, setServerAlerts])

  useEffect(() => {
    void load()
  }, [load, reloadTick])

  useEffect(() => {
    if (lastEvent && touchesAlerts(lastEvent)) void load()
  }, [lastEvent, load])

  function handleClosed(result: AlertCloseResult) {
    setClosing(null)
    toast.success(result.closed ? `Alert #${result.alert.id} closed` : 'Outcome recorded')
    if (filter === 'open' && result.alert.status === 'resolved') {
      removeAlert(result.alert.id)
    } else {
      replaceAlert(result.alert)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-xl font-bold text-text">Alerts</h1>
        <div role="radiogroup" aria-label="Show" className="flex rounded-xl border border-white/10 bg-white/5 p-0.5">
          {(['open', 'recent'] as const).map((f) => (
            <button
              key={f}
              type="button"
              role="radio"
              aria-checked={filter === f}
              onClick={() => setFilter(f)}
              className={cn(
                'min-h-11 min-w-11 rounded-lg px-4 text-sm font-medium transition',
                filter === f ? 'bg-accent/25 text-text' : 'text-text-muted',
              )}
            >
              {f === 'open' ? 'Open' : 'Recent'}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <p role="alert" className="rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
          {error}
        </p>
      )}

      {!loaded ? (
        <p className="py-6 text-center text-sm text-text-muted">Loading alerts…</p>
      ) : alerts.length === 0 ? (
        <p className="py-10 text-center text-sm text-text-muted">
          {filter === 'open' ? 'No open alerts. Machines are healthy.' : 'No alerts yet.'}
        </p>
      ) : (
        <ul className="space-y-3">
          {alerts.map((a) => (
            <li key={a.id}>
              <MobileAlertCard
                alert={a}
                user={user}
                onAcknowledge={(x) => void acknowledge(x)}
                onWorkOrder={(x) => void createWorkOrder(x)}
                onClose={setClosing}
                onWhy={(x) => navigate(`/m/alerts/${x.id}`)}
              />
            </li>
          ))}
        </ul>
      )}

      <AlertCloseDialog open={closing != null} alert={closing} onClose={() => setClosing(null)} onSaved={handleClosed} />
    </div>
  )
}
