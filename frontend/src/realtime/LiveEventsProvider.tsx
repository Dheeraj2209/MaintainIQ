import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { toast } from 'sonner'
import { BASE } from '../api/client'
import type { LiveEvent } from '../api/types'
import { useAuth } from '../auth/AuthContext'

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
    default:
      return `${event.machine_id}: update`
  }
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
      socket = new WebSocket(wsUrl())

      socket.onopen = () => {
        if (cancelled) return
        setConnected(true)
        backoffRef.current = INITIAL_BACKOFF_MS
      }

      socket.onmessage = (event) => {
        if (cancelled) return
        try {
          const parsed = JSON.parse(event.data) as LiveEvent
          setLastEvent(parsed)
          if (parsed.type === 'alert_resolved') {
            toast.success(describe(parsed))
          } else {
            toast.warning(describe(parsed))
          }
        } catch {
          // ignore malformed frames
        }
      }

      socket.onclose = () => {
        if (cancelled) return
        setConnected(false)
        const delay = backoffRef.current
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS)
        timerRef.current = setTimeout(connect, delay)
      }

      socket.onerror = () => {
        socket?.close()
      }
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
