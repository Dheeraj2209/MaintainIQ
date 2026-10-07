// The "Why this alert?" fetch, shared by the desktop drawer
// (AlertExplanationPanel) and the mobile alert page
// (design/2026-10-07-mobile-operator-pwa-design.md, decision 13). Loads on
// mount and again on any live event about this alert (escalation, resolve,
// ack, page, close/outcome) or a work order raised from it.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import { isWorkOrderEvent } from '../api/types'
import type { AlertExplanation, LiveEvent } from '../api/types'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

export function isAboutAlert(event: LiveEvent, alertId: number): boolean {
  if ('alert' in event) return event.alert.id === alertId
  return isWorkOrderEvent(event) && event.work_order.alert_id === alertId
}

export interface AlertExplanationState {
  explanation: AlertExplanation | null
  error: string | null
  // 404 from the server: the alert does not exist.
  notFound: boolean
  reload: () => Promise<void>
}

export function useAlertExplanation(alertId: number): AlertExplanationState {
  const { lastEvent } = useLiveEvents()
  const [explanation, setExplanation] = useState<AlertExplanation | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  // A slower, older load must not overwrite a newer one.
  const loadSeq = useRef(0)
  // The event already current when the view opened is not news to it.
  const seenEvent = useRef(lastEvent)

  const reload = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const e = await api.getAlertExplanation(alertId)
      if (seq !== loadSeq.current) return
      setExplanation(e)
      setError(null)
      setNotFound(false)
    } catch (e) {
      if (seq !== loadSeq.current) return
      if (e instanceof ApiError && e.status === 404) {
        setNotFound(true)
        setError(`Alert #${alertId} not found`)
      } else setError(e instanceof Error ? e.message : 'Failed to load the explanation')
    }
  }, [alertId])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (!lastEvent || lastEvent === seenEvent.current) return
    seenEvent.current = lastEvent
    if (isAboutAlert(lastEvent, alertId)) void reload()
  }, [lastEvent, reload, alertId])

  return { explanation, error, notFound, reload }
}
