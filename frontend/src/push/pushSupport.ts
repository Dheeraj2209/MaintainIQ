// Browser-side Web Push plumbing (design/2026-10-07-mobile-operator-pwa-design.md,
// decision 18), kept out of the hook so AuthContext.logout can unsubscribe
// this device without rendering anything.
import { api } from '../api/client'

// Cap on the logout-time unsubscribe: a hung push service or API must never
// hold up signing out.
export const UNSUBSCRIBE_TIMEOUT_MS = 2000

// The VAPID public key (unpadded base64url) as the raw bytes
// PushManager.subscribe wants for applicationServerKey.
export function urlBase64ToUint8Array(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = base64url.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (base64url.length % 4)) % 4)
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes
}

// Whether this browser has every API push needs. Secure context and a
// registered worker are checked separately (usePush reports each).
export function pushSupported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

// The service-worker registration, or null (unsupported, or a dev build where
// none is registered). Never waits on `navigator.serviceWorker.ready`, which
// would hang forever without a worker.
export async function currentRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (!pushSupported()) return null
  return (await navigator.serviceWorker.getRegistration()) ?? null
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  const registration = await currentRegistration()
  return registration ? registration.pushManager.getSubscription() : null
}

// Explicit logout: stop paging this device for the user who is leaving
// (shared phones). DELETE on the server, then unsubscribe in the browser.
// Best-effort and capped at UNSUBSCRIBE_TIMEOUT_MS; it never throws.
export async function unsubscribeThisDevice(): Promise<void> {
  const work = (async () => {
    const subscription = await currentSubscription()
    if (!subscription) return
    try {
      await api.unsubscribePush(subscription.endpoint)
    } finally {
      await subscription.unsubscribe()
    }
  })().catch(() => undefined)
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, UNSUBSCRIBE_TIMEOUT_MS)
  })
  await Promise.race([work, timeout])
  clearTimeout(timer)
}

// True when `subscription` was made with exactly `key` (the server's current
// VAPID public key). A mismatch means the keys were rotated.
export function sameServerKey(subscription: PushSubscription, key: Uint8Array): boolean {
  const current = subscription.options?.applicationServerKey
  if (!current) return false
  const bytes = new Uint8Array(current)
  return bytes.length === key.length && bytes.every((b, i) => b === key[i])
}
