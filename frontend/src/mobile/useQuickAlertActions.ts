// Optimistic quick actions for the mobile alert cards
// (design/2026-10-07-mobile-operator-pwa-design.md, decision 12).
//
// The list the page fetched (`setServerAlerts`) and the user's in-flight
// edits (`pending`) are kept apart, and `alerts` lays the second over the
// first. A re-fetch triggered by a live event therefore lands *under* the
// user's own optimistic state, so it never flickers back, while other users'
// changes still show. Each edit is dropped once its request settles: on
// success the server's row replaces the fetched one, on error the overlay
// simply goes away (the rollback).
import { useCallback, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '../api/client'
import type { Alert } from '../api/types'

// `active_work_order_id` while the order is being created.
export const PENDING_WORK_ORDER = -1

type Overlay = Partial<Pick<Alert, 'acknowledged_at' | 'acknowledged_by' | 'active_work_order_id'>>

interface Options {
  // The signed-in user's id, stamped as acknowledged_by while pending.
  userId?: number
  // Re-fetch the list (after a 409: someone else raised the order first).
  reload?: () => void
}

export interface QuickAlertActions {
  alerts: Alert[]
  setServerAlerts: (alerts: Alert[]) => void
  // Replace one alert with the server's copy (a closed alert, say).
  replaceAlert: (alert: Alert) => void
  // Drop one alert from the fetched list (closed off the Open filter). Works
  // on the fetched rows, never the merged view, so another card's in-flight
  // edit is not baked in where its rollback could not reach it.
  removeAlert: (id: number) => void
  acknowledge: (alert: Alert) => Promise<void>
  createWorkOrder: (alert: Alert) => Promise<void>
}

function message(e: unknown, fallback: string): string {
  return e instanceof Error ? e.message : fallback
}

export function useQuickAlertActions({ userId, reload }: Options = {}): QuickAlertActions {
  const [serverAlerts, setServerAlerts] = useState<Alert[]>([])
  const [pending, setPending] = useState<ReadonlyMap<number, Overlay>>(new Map())

  const alerts = useMemo(
    () => serverAlerts.map((a) => (pending.has(a.id) ? { ...a, ...pending.get(a.id) } : a)),
    [serverAlerts, pending],
  )

  const overlay = useCallback((id: number, changes: Overlay | null) => {
    setPending((current) => {
      const next = new Map(current)
      if (changes) next.set(id, { ...current.get(id), ...changes })
      else next.delete(id)
      return next
    })
  }, [])

  const replaceAlert = useCallback((alert: Alert) => {
    setServerAlerts((current) => current.map((a) => (a.id === alert.id ? { ...a, ...alert } : a)))
  }, [])

  const removeAlert = useCallback((id: number) => {
    setServerAlerts((current) => current.filter((a) => a.id !== id))
  }, [])

  const acknowledge = useCallback(
    async (alert: Alert) => {
      overlay(alert.id, { acknowledged_at: new Date().toISOString(), acknowledged_by: userId ?? null })
      try {
        // Idempotent on the server, so a retried lost response is safe.
        const updated = await api.acknowledgeAlert(alert.id)
        replaceAlert(updated)
      } catch (e) {
        toast.error(`Could not acknowledge alert #${alert.id}: ${message(e, 'request failed')}`)
      } finally {
        overlay(alert.id, null)
      }
    },
    [overlay, replaceAlert, userId],
  )

  const createWorkOrder = useCallback(
    async (alert: Alert) => {
      overlay(alert.id, {
        active_work_order_id: PENDING_WORK_ORDER,
        // The server acknowledges the alert along with the order.
        ...(alert.acknowledged_at ? {} : { acknowledged_at: new Date().toISOString(), acknowledged_by: userId ?? null }),
      })
      try {
        // No body: the server derives the title and priority from the alert.
        const order = await api.createWorkOrderFromAlert(alert.id)
        setServerAlerts((current) =>
          current.map((a) =>
            a.id === alert.id
              ? {
                  ...a,
                  active_work_order_id: order.id,
                  acknowledged_at: a.acknowledged_at ?? order.created_at,
                  acknowledged_by: a.acknowledged_by ?? userId ?? null,
                }
              : a,
          ),
        )
        toast.success(`Work order #${order.id} created`)
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          toast.info(`Alert #${alert.id} already has a work order`)
          reload?.()
        } else {
          toast.error(`Could not create a work order for alert #${alert.id}: ${message(e, 'request failed')}`)
        }
      } finally {
        overlay(alert.id, null)
      }
    },
    [overlay, reload, userId],
  )

  return { alerts, setServerAlerts, replaceAlert, removeAlert, acknowledge, createWorkOrder }
}
