// Per-device push opt-in (design/2026-10-07-mobile-operator-pwa-design.md,
// decision 18). Works out what this browser can do, keeps the server row in
// step with the browser subscription, and exposes enable/disable/test for
// PushToggle. Permission is only ever requested from enable(), i.e. from a
// tap: iOS refuses it otherwise, and asking on page load trains people to
// click "Block".
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { PushTestResult } from '../api/types'
import { currentRegistration, pushSupported, sameServerKey, urlBase64ToUint8Array } from './pushSupport'

export type PushState =
  | 'loading'
  | 'unsupported' // no service worker, Push API or Notification
  | 'insecure' // not a secure context (plain http on a LAN address)
  | 'unavailable' // no service worker registered (a dev build)
  | 'server-disabled' // the server has no VAPID keys
  | 'denied' // notifications blocked in the browser's site settings
  | 'off'
  | 'on'
  | 'busy'

export interface PushControls {
  state: PushState
  error: string | null
  enable: () => Promise<void>
  disable: () => Promise<void>
  sendTest: () => Promise<PushTestResult | null>
}

function message(e: unknown, fallback: string): string {
  return e instanceof Error ? e.message : fallback
}

export function usePush(): PushControls {
  const [state, setState] = useState<PushState>('loading')
  const [error, setError] = useState<string | null>(null)
  const registrationRef = useRef<ServiceWorkerRegistration | null>(null)
  const keyRef = useRef<Uint8Array<ArrayBuffer> | null>(null)

  useEffect(() => {
    let cancelled = false
    const settle = (next: PushState) => {
      if (!cancelled) setState(next)
    }

    async function init() {
      if (!pushSupported()) return settle('unsupported')
      if (!window.isSecureContext) return settle('insecure')
      const registration = await currentRegistration()
      if (!registration) return settle('unavailable')
      registrationRef.current = registration

      const config = await api.getPushConfig()
      if (!config.enabled || !config.public_key) return settle('server-disabled')
      const key = urlBase64ToUint8Array(config.public_key)
      keyRef.current = key

      if (Notification.permission === 'denied') return settle('denied')

      let subscription = await registration.pushManager.getSubscription()
      if (!subscription) return settle('off')
      // Keys rotated on the server: the old subscription can never be
      // delivered to, so swap it for one made with the current key.
      if (!sameServerKey(subscription, key)) {
        await subscription.unsubscribe()
        subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key })
      }
      // Re-post on every visit: the idempotent upsert reactivates a row the
      // server deactivated on a 410 and rebinds a shared phone to whoever is
      // signed in now.
      await api.subscribePush(subscription.toJSON())
      settle('on')
    }

    init().catch((e) => {
      if (cancelled) return
      setError(message(e, 'Could not check push notifications'))
      // Turning it on again re-posts whatever the browser holds.
      settle(registrationRef.current ? 'off' : 'unavailable')
    })
    return () => {
      cancelled = true
    }
  }, [])

  const enable = useCallback(async () => {
    const registration = registrationRef.current
    const key = keyRef.current
    if (!registration || !key) return
    setState('busy')
    setError(null)
    const permission = await Notification.requestPermission()
    if (permission !== 'granted') {
      setState(permission === 'denied' ? 'denied' : 'off')
      setError(permission === 'denied' ? null : 'Notification permission was not granted')
      return
    }
    let subscription: PushSubscription
    try {
      subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key })
    } catch (e) {
      setState('off')
      setError(message(e, 'Could not subscribe this device'))
      return
    }
    try {
      await api.subscribePush(subscription.toJSON())
      setState('on')
    } catch (e) {
      // Never leave the browser subscribed to a push the server doesn't know.
      await subscription.unsubscribe().catch(() => false)
      setState('off')
      setError(message(e, 'Could not register this device'))
    }
  }, [])

  const disable = useCallback(async () => {
    const registration = registrationRef.current
    if (!registration) return
    setState('busy')
    setError(null)
    const subscription = await registration.pushManager.getSubscription().catch(() => null)
    if (subscription) {
      try {
        await api.unsubscribePush(subscription.endpoint)
      } catch (e) {
        setError(message(e, 'Could not remove this device on the server'))
      }
      // The browser side goes regardless: the user asked to stop.
      await subscription.unsubscribe().catch(() => false)
    }
    setState('off')
  }, [])

  const sendTest = useCallback(async () => {
    setError(null)
    try {
      return await api.sendTestPush()
    } catch (e) {
      setError(message(e, 'Could not send a test notification'))
      return null
    }
  }, [])

  return { state, error, enable, disable, sendTest }
}
