import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import { pushConfig, pushConfigDisabled } from '../test/fixtures'
import { DEFAULT_ENDPOINT, fakeSubscription, installPwaStubs } from '../test/pwaStubs'
import { urlBase64ToUint8Array } from './pushSupport'
import { usePush } from './usePush'

const serverKey = urlBase64ToUint8Array(pushConfig.public_key!)

async function settled() {
  const hook = renderHook(() => usePush())
  // Several awaits deep; room for a loaded machine running the whole suite.
  await waitFor(() => expect(hook.result.current.state).not.toBe('loading'), { timeout: 5000 })
  return hook
}

// Records the method + JSON body of every /api/push/subscribe call.
function recordSubscribeCalls() {
  const calls: { method: string; body: Record<string, unknown> }[] = []
  server.use(
    http.post('/api/push/subscribe', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>
      calls.push({ method: 'POST', body })
      return HttpResponse.json({ id: 3, endpoint: body.endpoint, is_active: true, created_at: 'x', updated_at: 'x', last_used_at: null })
    }),
    http.delete('/api/push/subscribe', async ({ request }) => {
      calls.push({ method: 'DELETE', body: (await request.json()) as Record<string, unknown> })
      return new HttpResponse(null, { status: 204 })
    }),
  )
  return calls
}

describe('urlBase64ToUint8Array', () => {
  it('decodes an unpadded base64url VAPID key to its 65 raw bytes', () => {
    expect(serverKey).toHaveLength(65)
    expect(serverKey[0]).toBe(0x04)
    expect(Array.from(urlBase64ToUint8Array('-_8'))).toEqual([0xfb, 0xff])
  })
})

describe('usePush', () => {
  it('is unsupported without a service worker', async () => {
    installPwaStubs({ serviceWorker: false })
    const { result } = await settled()
    expect(result.current.state).toBe('unsupported')
  })

  it('is unsupported without the Push API or Notification', async () => {
    installPwaStubs({ pushManager: false })
    expect((await settled()).result.current.state).toBe('unsupported')
    installPwaStubs({ notification: false })
    expect((await settled()).result.current.state).toBe('unsupported')
  })

  it('is insecure outside a secure context', async () => {
    installPwaStubs({ secure: false })
    expect((await settled()).result.current.state).toBe('insecure')
  })

  it('is unavailable when no service worker is registered (a dev build)', async () => {
    installPwaStubs({ registration: false })
    expect((await settled()).result.current.state).toBe('unavailable')
  })

  it('is server-disabled when the server has no VAPID keys', async () => {
    server.use(http.get('/api/push/vapid-public-key', () => HttpResponse.json(pushConfigDisabled)))
    expect((await settled()).result.current.state).toBe('server-disabled')
  })

  it('is denied when notifications are blocked for the site', async () => {
    installPwaStubs({ permission: 'denied' })
    expect((await settled()).result.current.state).toBe('denied')
  })

  it('is off with no subscription on this device', async () => {
    expect((await settled()).result.current.state).toBe('off')
  })

  it('enable() asks permission, subscribes with the server key and posts toJSON()', async () => {
    const stubs = installPwaStubs()
    const calls = recordSubscribeCalls()
    const { result } = await settled()

    await act(() => result.current.enable())

    expect(stubs.requestPermission).toHaveBeenCalledTimes(1)
    expect(stubs.subscribe).toHaveBeenCalledTimes(1)
    const options = stubs.subscribe.mock.calls[0][0] as PushSubscriptionOptionsInit
    expect(options.userVisibleOnly).toBe(true)
    expect(Array.from(options.applicationServerKey as Uint8Array)).toEqual(Array.from(serverKey))
    expect(calls).toEqual([{ method: 'POST', body: stubs.current()!.toJSON() as unknown as Record<string, unknown> }])
    expect(result.current.state).toBe('on')
    expect(result.current.error).toBeNull()
  })

  it('enable() stays off when permission is refused', async () => {
    const stubs = installPwaStubs({ grant: 'denied' })
    const { result } = await settled()
    await act(() => result.current.enable())
    expect(stubs.subscribe).not.toHaveBeenCalled()
    expect(result.current.state).toBe('denied')
  })

  it('enable() undoes the browser subscription when the server rejects it', async () => {
    const stubs = installPwaStubs()
    server.use(http.post('/api/push/subscribe', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })))
    const { result } = await settled()

    await act(() => result.current.enable())

    expect(stubs.subscribe).toHaveBeenCalledTimes(1)
    expect(stubs.current()).toBeNull()
    expect(result.current.state).toBe('off')
    expect(result.current.error).toMatch(/boom/)
  })

  it('disable() deletes this endpoint on the server and unsubscribes', async () => {
    const sub = fakeSubscription(DEFAULT_ENDPOINT, serverKey)
    installPwaStubs({ subscription: sub })
    const calls = recordSubscribeCalls()
    const { result } = await settled()
    expect(result.current.state).toBe('on')

    await act(() => result.current.disable())

    expect(calls.at(-1)).toEqual({ method: 'DELETE', body: { endpoint: DEFAULT_ENDPOINT } })
    expect(sub.unsubscribe).toHaveBeenCalled()
    expect(result.current.state).toBe('off')
  })

  it('disable() still unsubscribes the browser when the DELETE fails, and reports it', async () => {
    const sub = fakeSubscription(DEFAULT_ENDPOINT, serverKey)
    installPwaStubs({ subscription: sub })
    server.use(http.delete('/api/push/subscribe', () => HttpResponse.json({ detail: 'down' }, { status: 503 })))
    const { result } = await settled()

    await act(() => result.current.disable())

    expect(sub.unsubscribe).toHaveBeenCalled()
    expect(result.current.state).toBe('off')
    expect(result.current.error).toMatch(/down/)
  })

  it('re-posts an existing subscription on mount, so the server row is refreshed', async () => {
    const sub = fakeSubscription(DEFAULT_ENDPOINT, serverKey)
    const stubs = installPwaStubs({ subscription: sub, permission: 'granted' })
    const calls = recordSubscribeCalls()
    const { result } = await settled()

    expect(result.current.state).toBe('on')
    expect(calls).toEqual([{ method: 'POST', body: sub.toJSON() as unknown as Record<string, unknown> }])
    expect(stubs.subscribe).not.toHaveBeenCalled()
    expect(sub.unsubscribe).not.toHaveBeenCalled()
  })

  it('re-subscribes on mount when the server key was rotated', async () => {
    const stale = fakeSubscription(DEFAULT_ENDPOINT, new Uint8Array([1, 2, 3]))
    const stubs = installPwaStubs({ subscription: stale, permission: 'granted' })
    const calls = recordSubscribeCalls()
    const { result } = await settled()

    await waitFor(() => expect(result.current.state).toBe('on'), { timeout: 5000 })
    expect(stale.unsubscribe).toHaveBeenCalled()
    expect(stubs.subscribe).toHaveBeenCalledTimes(1)
    expect(Array.from(stubs.subscribe.mock.calls[0][0].applicationServerKey as Uint8Array)).toEqual(Array.from(serverKey))
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1)
  })

  it('sendTest() posts /push/test and returns the counts', async () => {
    let tested = 0
    server.use(
      http.post('/api/push/test', () => {
        tested += 1
        return HttpResponse.json({ sent: 2, failed: 0, expired: 0 })
      }),
    )
    installPwaStubs({ subscription: fakeSubscription(DEFAULT_ENDPOINT, serverKey), permission: 'granted' })
    const { result } = await settled()

    let counts: unknown
    await act(async () => {
      counts = await result.current.sendTest()
    })
    expect(tested).toBe(1)
    expect(counts).toEqual({ sent: 2, failed: 0, expired: 0 })
  })
})
