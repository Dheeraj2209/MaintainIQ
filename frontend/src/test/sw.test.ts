// public/sw.js runs in a ServiceWorkerGlobalScope, which jsdom doesn't have.
// Each test evaluates the real file against a fake `self`, `caches`, `fetch`
// and `clients`, then fires the captured event listeners.
import { describe, expect, it, vi } from 'vitest'
// The file's text, exactly as the server ships it from dist/.
import SOURCE from '../../public/sw.js?raw'

const ORIGIN = 'https://plant.example'

interface FakeResponse {
  ok: boolean
  type: string
  body: string
  clone: () => FakeResponse
}

function res(body: string, { ok = true, type = 'basic' } = {}): FakeResponse {
  const r: FakeResponse = { ok, type, body, clone: () => r }
  return r
}

function keyOf(req: string | { url: string }): string {
  const url = typeof req === 'string' ? req : req.url
  return url.startsWith('/') ? ORIGIN + url : url
}

class FakeCache {
  store = new Map<string, FakeResponse>()
  async match(req: string | { url: string }) {
    return this.store.get(keyOf(req))
  }
  async put(req: string | { url: string }, r: FakeResponse) {
    this.store.set(keyOf(req), r)
  }
  async addAll(urls: string[]) {
    for (const u of urls) this.store.set(keyOf(u), res(`precached ${u}`))
  }
  async keys() {
    return [...this.store.keys()].map((url) => ({ url }))
  }
  async delete(req: string | { url: string }) {
    return this.store.delete(keyOf(req))
  }
}

function loadWorker({ windows = [] as { focus: () => Promise<unknown>; navigate: (u: string) => Promise<unknown> }[] } = {}) {
  const listeners: Record<string, (event: unknown) => void> = {}
  const stores = new Map<string, FakeCache>()
  const caches = {
    open: vi.fn(async (name: string) => {
      if (!stores.has(name)) stores.set(name, new FakeCache())
      return stores.get(name)!
    }),
    keys: vi.fn(async () => [...stores.keys()]),
    delete: vi.fn(async (name: string) => stores.delete(name)),
  }
  const fetchFn = vi.fn<(req: unknown) => Promise<FakeResponse>>(async () => res('network'))
  const clients = {
    claim: vi.fn(async () => {}),
    matchAll: vi.fn(async () => windows),
    openWindow: vi.fn(async () => null),
  }
  const showNotification = vi.fn(async () => {})
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type: string, fn: (event: unknown) => void) => {
      listeners[type] = fn
    },
    skipWaiting: vi.fn(async () => {}),
    clients,
    registration: { showNotification },
  }
  new Function('self', 'caches', 'fetch', 'Response', SOURCE)(self, caches, fetchFn, { error: () => res('', { ok: false, type: 'error' }) })
  return { listeners, stores, caches, fetchFn, clients, showNotification, self }
}

// Fires a fetch event; resolves to what was passed to respondWith (or null).
async function fire(w: ReturnType<typeof loadWorker>, url: string, init: { method?: string; mode?: string } = {}) {
  // Assigned inside the respondWith callback, which TS cannot see run.
  let responded = null as Promise<FakeResponse> | null
  const event = {
    request: { url: url.startsWith('/') ? ORIGIN + url : url, method: init.method ?? 'GET', mode: init.mode ?? 'cors' },
    respondWith: vi.fn((p: Promise<FakeResponse>) => {
      responded = p
    }),
    waitUntil: vi.fn(),
  }
  w.listeners.fetch(event)
  return { event, response: responded ? await responded : null }
}

async function lifecycle(w: ReturnType<typeof loadWorker>, type: 'install' | 'activate') {
  let done: Promise<unknown> = Promise.resolve()
  w.listeners[type]({ waitUntil: (p: Promise<unknown>) => (done = p) })
  await done
}

describe('service worker fetch handling', () => {
  it.each([
    ['a POST', '/m/alerts', { method: 'POST' }],
    ['an API read', '/api/alerts', {}],
    ['the API socket', '/api/ws', {}],
    ['the bare socket path', '/ws', {}],
    ['a cross-origin font', 'https://fonts.gstatic.com/s/jetbrainsmono.woff2', {}],
    ['an API navigation', '/api/reports/1/download', { mode: 'navigate' }],
  ])('never intercepts %s', async (_label, url, init) => {
    const w = loadWorker()
    const { event } = await fire(w, url, init)
    expect(event.respondWith).not.toHaveBeenCalled()
  })

  it('serves the cached shell for a navigation when the network is down', async () => {
    const w = loadWorker()
    await lifecycle(w, 'install')
    w.fetchFn.mockRejectedValueOnce(new TypeError('offline'))

    const { response } = await fire(w, '/m/alerts/42', { mode: 'navigate' })

    expect(response?.body).toBe('precached /index.html')
  })

  it('prefers the network for navigations when online', async () => {
    const w = loadWorker()
    await lifecycle(w, 'install')
    const { response } = await fire(w, '/m/alerts', { mode: 'navigate' })
    expect(response?.body).toBe('network')
  })

  it('serves hashed assets from cache, and caches a miss only when ok and same-origin', async () => {
    const w = loadWorker()
    w.fetchFn.mockResolvedValueOnce(res('asset body'))
    expect((await fire(w, '/assets/x.js')).response?.body).toBe('asset body')
    expect(w.stores.get('miq-assets-v1')!.store.has(`${ORIGIN}/assets/x.js`)).toBe(true)

    // Second request: from cache, no network.
    w.fetchFn.mockClear()
    expect((await fire(w, '/assets/x.js')).response?.body).toBe('asset body')
    expect(w.fetchFn).not.toHaveBeenCalled()

    w.fetchFn.mockResolvedValueOnce(res('error page', { ok: false }))
    await fire(w, '/assets/missing.js')
    w.fetchFn.mockResolvedValueOnce(res('opaque', { type: 'opaque' }))
    await fire(w, '/assets/opaque.js')
    const cached = [...w.stores.get('miq-assets-v1')!.store.keys()]
    expect(cached).toEqual([`${ORIGIN}/assets/x.js`])
  })
})

describe('service worker lifecycle', () => {
  it('precaches the shell on install and takes over at once', async () => {
    const w = loadWorker()
    await lifecycle(w, 'install')
    expect([...w.stores.get('miq-shell-v1')!.store.keys()]).toEqual([
      `${ORIGIN}/index.html`,
      `${ORIGIN}/manifest.webmanifest`,
      `${ORIGIN}/icons/icon-192.png`,
    ])
    expect(w.self.skipWaiting).toHaveBeenCalled()
  })

  it('deletes old miq-* caches on activate and keeps the current ones', async () => {
    const w = loadWorker()
    await w.caches.open('miq-old-v0')
    await w.caches.open('miq-shell-v1')
    await w.caches.open('miq-assets-v1')
    await w.caches.open('someone-elses-cache')

    await lifecycle(w, 'activate')

    expect([...w.stores.keys()].sort()).toEqual(['miq-assets-v1', 'miq-shell-v1', 'someone-elses-cache'])
    expect(w.clients.claim).toHaveBeenCalled()
  })
})

describe('service worker notifications', () => {
  async function push(w: ReturnType<typeof loadWorker>, data: unknown) {
    let done: Promise<unknown> = Promise.resolve()
    w.listeners.push({ data, waitUntil: (p: Promise<unknown>) => (done = p) })
    await done
  }

  it('shows the pushed alert, tagged so a re-page replaces it', async () => {
    const w = loadWorker()
    const payload = { title: 'MaintainIQ CRITICAL: m1 needs attention', body: 'critical', tag: 'alert-42', url: '/m/alerts/42' }
    await push(w, { json: () => payload })

    expect(w.showNotification).toHaveBeenCalledWith(
      payload.title,
      expect.objectContaining({ body: 'critical', tag: 'alert-42', renotify: true, data: { url: '/m/alerts/42' } }),
    )
  })

  it('falls back to a generic notification without a usable payload', async () => {
    const w = loadWorker()
    await push(w, null)
    await push(w, {
      json: () => {
        throw new SyntaxError('bad json')
      },
    })
    expect(w.showNotification).toHaveBeenCalledTimes(2)
    for (const [title, options] of w.showNotification.mock.calls as unknown as [string, { body: string }][]) {
      expect(title).toBe('MaintainIQ')
      expect(options.body).toBe('Open the app for details')
    }
  })

  async function click(w: ReturnType<typeof loadWorker>, url: unknown) {
    const close = vi.fn()
    let done: Promise<unknown> = Promise.resolve()
    w.listeners.notificationclick({ notification: { close, data: { url } }, waitUntil: (p: Promise<unknown>) => (done = p) })
    await done
    return close
  }

  it('opens the deep link in a new window when none is open', async () => {
    const w = loadWorker()
    const close = await click(w, '/m/alerts/42')
    expect(close).toHaveBeenCalled()
    expect(w.clients.openWindow).toHaveBeenCalledWith('/m/alerts/42')
  })

  it('focuses and navigates an open window instead', async () => {
    const win = { focus: vi.fn(async () => win), navigate: vi.fn(async () => win) }
    const w = loadWorker({ windows: [win] })
    await click(w, '/m/alerts/42')
    expect(win.focus).toHaveBeenCalled()
    expect(win.navigate).toHaveBeenCalledWith('/m/alerts/42')
    expect(w.clients.openWindow).not.toHaveBeenCalled()
  })

  it('opens a new window when the open one is not controlled (navigate rejects)', async () => {
    const win = {
      focus: vi.fn(async () => win),
      navigate: vi.fn(async () => {
        throw new TypeError('not controlled by this worker')
      }),
    }
    const w = loadWorker({ windows: [win] })
    await click(w, '/m/alerts/42')
    expect(win.navigate).toHaveBeenCalledWith('/m/alerts/42')
    expect(w.clients.openWindow).toHaveBeenCalledWith('/m/alerts/42')
  })

  it.each(['https://evil.example/x', '//evil.example/x', '/\\evil.example', 42])('sends %s to the alerts list', async (url) => {
    const w = loadWorker()
    await click(w, url)
    expect(w.clients.openWindow).toHaveBeenCalledWith('/m/alerts')
  })
})
