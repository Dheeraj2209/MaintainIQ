import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { toast } from 'sonner'
import { ApiError, BASE, api } from '../api/client'
import { KNOWN_EVENT_TYPES, isWorkOrderEvent } from '../api/types'
import type { LiveEvent, WorkOrderLiveEvent } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { feedbackOutcomeLabel } from '../components/healthStyles'

interface LiveEventsState {
  connected: boolean
  lastEvent: LiveEvent | null
}

const LiveEventsContext = createContext<LiveEventsState>({ connected: false, lastEvent: null })

const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30_000

// BASE is normally a relative '/api' (dev proxy + prod same-origin both work
// off that), but honor an absolute VITE_API_BASE_URL too.
function wsUrl(): string {
  if (/^https?:\/\//i.test(BASE)) {
    return BASE.replace(/^http/i, 'ws') + '/ws'
  }
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${BASE}/ws`
}

// Exhaustive over the LiveEvent union: adding an event type without a case
// here fails `tsc -b` (npm run build).
function describe(event: LiveEvent): string {
  switch (event.type) {
    case 'alert_created':
      return `${event.machine_id}: new ${event.alert.severity} alert (${event.alert.health_state})`
    case 'alert_escalated':
      return `${event.machine_id}: alert escalated to ${event.alert.severity}`
    case 'alert_resolved':
      return `${event.machine_id}: alert resolved`
    case 'alert_acknowledged':
      return `${event.machine_id}: alert acknowledged`
    case 'device_offline': {
      const where = event.machine_id ? ` (${event.machine_id})` : ''
      // lwt: the broker saw the connection drop; silent: heartbeats stopped.
      const what = event.incident?.kind === 'lwt' ? 'disconnected' : 'went silent'
      return `Sensor node ${event.device_id}${where} ${what}`
    }
    case 'device_online':
      return `Sensor node ${event.device_id} is back online`
    case 'work_order_created':
      return `Work order #${event.work_order.id} opened for ${event.machine_id}`
    case 'work_order_updated':
      return `Work order #${event.work_order.id} ${describeChange(event)}`
    case 'alert_paged':
      return `${event.machine_id}: alert unacknowledged — paged level ${event.page_level}`
    case 'alert_closed':
      return `${event.machine_id}: alert closed — ${feedbackOutcomeLabel(event.feedback.outcome)}`
    case 'alert_feedback_recorded':
      return `${event.machine_id}: outcome recorded — ${feedbackOutcomeLabel(event.feedback.outcome)}`
  }
}

function describeChange(event: WorkOrderLiveEvent): string {
  switch (event.change) {
    case 'assigned':
      return `assigned to ${event.work_order.assigned_to_name ?? `user #${event.work_order.assigned_to}`}`
    case 'started':
    case 'completed':
    case 'cancelled':
      return event.change
    default:
      return 'updated'
  }
}

// Good news is green, work-order and outcome bookkeeping is informational,
// and anything that may need a person (new/escalated/paged alerts, a silent
// node) warns.
function toastKind(event: LiveEvent): 'success' | 'info' | 'warning' {
  if (event.type === 'alert_resolved' || event.type === 'device_online' || event.type === 'alert_closed') return 'success'
  if (event.type === 'alert_feedback_recorded') return 'info'
  if (isWorkOrderEvent(event)) return event.change === 'completed' ? 'success' : 'info'
  return 'warning'
}

// Frames from a newer server (event types this build doesn't know) are
// dropped: no lastEvent update and no toast, rather than a describe() crash.
function parseEvent(data: string): LiveEvent | null {
  const parsed: unknown = JSON.parse(data)
  if (typeof parsed !== 'object' || parsed === null) return null
  const type = (parsed as { type?: unknown }).type
  return typeof type === 'string' && KNOWN_EVENT_TYPES.has(type) ? (parsed as LiveEvent) : null
}

// One shared socket per session, opened once a user is known and reopened
// with backoff on drop. Pages consume it via useLiveEvents() and re-fetch
// whenever `lastEvent` changes; this provider also drives the global toast.
export function LiveEventsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const [connected, setConnected] = useState(false)
  const [lastEvent, setLastEvent] = useState<LiveEvent | null>(null)
  const backoffRef = useRef(INITIAL_BACKOFF_MS)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!user) return

    let cancelled = false
    let socket: WebSocket | null = null

    function connect() {
      if (cancelled) return
      const ws = new WebSocket(wsUrl())
      socket = ws
      let opened = false

      ws.onopen = () => {
        if (cancelled) return
        opened = true
        setConnected(true)
        backoffRef.current = INITIAL_BACKOFF_MS
      }

      ws.onmessage = (event) => {
        if (cancelled) return
        try {
          const parsed = parseEvent(event.data)
          if (!parsed) return
          setLastEvent(parsed)
          toast[toastKind(parsed)](describe(parsed))
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = (event) => {
        if (cancelled) return
        setConnected(false)
        // The server rejects an expired session before accepting the socket,
        // so the browser reports a plain 1006 on a socket that never opened
        // (4401 is honoured too). A phone left on one page makes no REST
        // calls, so ask /auth/me: a 401 there fires miq:unauthorized, which
        // signs the app out (and this effect cancels) instead of showing
        // "Reconnecting…" forever.
        if (!opened || event.code === 4401) {
          api.me().then(scheduleReconnect, (e: unknown) => {
            // Signed out: the provider tears down with the user. Anything
            // else (server down, offline) retries as before.
            if (!(e instanceof ApiError && e.status === 401)) scheduleReconnect()
          })
        } else {
          scheduleReconnect()
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    }

    function scheduleReconnect() {
      if (cancelled) return
      const delay = backoffRef.current
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS)
      timerRef.current = setTimeout(connect, delay)
    }

    connect()

    return () => {
      cancelled = true
      if (timerRef.current) clearTimeout(timerRef.current)
      socket?.close()
      setConnected(false)
    }
  }, [user])

  return <LiveEventsContext.Provider value={{ connected, lastEvent }}>{children}</LiveEventsContext.Provider>
}

export function useLiveEvents(): LiveEventsState {
  return useContext(LiveEventsContext)
}
