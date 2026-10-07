/* MaintainIQ service worker (design/2026-10-07-mobile-operator-pwa-design.md,
   decision 7). Hand-written on purpose: these rules are the whole requirement.

   Never intercepted, so the browser handles them exactly as without a worker:
   non-GET requests, other origins (the font CDNs), anything under /api/
   (which includes the /api/ws socket) and /ws. API data and auth are never
   cached.

   - Navigations: network first; offline, the cached /index.html (the app
     shell), so a deploy is picked up on the next online load.
   - /assets/* (Vite's content-hashed files): cache first, filled at runtime,
     trimmed to MAX_ASSETS entries.
   - /icons/*, /manifest.webmanifest, /favicon.svg: stale-while-revalidate.

   Bump SW_VERSION when these rules change; activate deletes every older
   miq-* cache. Served with Cache-Control: no-cache (src/api/app.py). */
const SW_VERSION = 'v1'
const SHELL_CACHE = `miq-shell-${SW_VERSION}`
const ASSET_CACHE = `miq-assets-${SW_VERSION}`
const CURRENT_CACHES = [SHELL_CACHE, ASSET_CACHE]
const PRECACHE = ['/index.html', '/manifest.webmanifest', '/icons/icon-192.png']
const MAX_ASSETS = 60
const FALLBACK_URL = '/m/alerts'

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith('miq-') && !CURRENT_CACHES.includes(key))
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  )
})

function cacheable(response) {
  return response && response.ok && response.type === 'basic'
}

async function trim(cache, max) {
  const keys = await cache.keys()
  for (let i = 0; i < keys.length - max; i++) await cache.delete(keys[i])
}

async function networkFirstNavigation(request) {
  try {
    return await fetch(request)
  } catch (err) {
    const cache = await caches.open(SHELL_CACHE)
    const shell = await cache.match('/index.html')
    if (shell) return shell
    throw err
  }
}

async function cacheFirstAsset(request) {
  const cache = await caches.open(ASSET_CACHE)
  const hit = await cache.match(request)
  if (hit) return hit
  const response = await fetch(request)
  if (cacheable(response)) {
    await cache.put(request, response.clone())
    await trim(cache, MAX_ASSETS)
  }
  return response
}

async function staleWhileRevalidate(event) {
  const cache = await caches.open(SHELL_CACHE)
  const hit = await cache.match(event.request)
  const refresh = fetch(event.request)
    .then(async (response) => {
      if (cacheable(response)) await cache.put(event.request, response.clone())
      return response
    })
    .catch(() => undefined)
  if (hit) {
    event.waitUntil(refresh)
    return hit
  }
  const response = await refresh
  return response || Response.error()
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/') || url.pathname === '/api' || url.pathname === '/ws' || url.pathname.startsWith('/ws/')) return

  if (request.mode === 'navigate') {
    event.respondWith(networkFirstNavigation(request))
  } else if (url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirstAsset(request))
  } else if (
    url.pathname.startsWith('/icons/') ||
    url.pathname === '/manifest.webmanifest' ||
    url.pathname === '/favicon.svg'
  ) {
    event.respondWith(staleWhileRevalidate(event))
  }
})

// A page (src/notifications/push.py payload). A missing or malformed payload
// still shows something rather than nothing: browsers penalise silent pushes.
self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = (event.data && event.data.json()) || {}
  } catch {
    data = {}
  }
  const title = typeof data.title === 'string' && data.title ? data.title : 'MaintainIQ'
  const body = typeof data.body === 'string' && data.body ? data.body : 'Open the app for details'
  const options = {
    body,
    // One notification per alert/incident: an escalation or ladder page
    // replaces the earlier one (and alerts again) instead of stacking.
    tag: typeof data.tag === 'string' ? data.tag : undefined,
    renotify: typeof data.tag === 'string',
    data: { url: typeof data.url === 'string' ? data.url : FALLBACK_URL },
    icon: '/icons/icon-192.png',
    badge: '/icons/badge-96.png',
  }
  event.waitUntil(self.registration.showNotification(title, options))
})

// Only a same-origin path is ever opened; anything else goes to the alerts.
function safeTarget(url) {
  if (typeof url !== 'string' || !url.startsWith('/') || url.startsWith('//') || url.startsWith('/\\')) {
    return FALLBACK_URL
  }
  return url
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = safeTarget(event.notification.data && event.notification.data.url)
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async (windows) => {
      const client = windows.find((w) => 'focus' in w)
      if (client) {
        await client.focus()
        // navigate() rejects for a window this worker doesn't control (one
        // opened with a hard reload, say): open the deep link afresh then.
        if ('navigate' in client) return client.navigate(target).catch(() => self.clients.openWindow(target))
        return self.clients.openWindow(target)
      }
      return self.clients.openWindow(target)
    }),
  )
})
