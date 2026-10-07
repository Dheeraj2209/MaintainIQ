import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { server } from './server'
import { MockWebSocket } from './mockWebSocket'
import { installPwaStubs, restorePwaStubs } from './pwaStubs'

// findBy*/waitFor default to 1 s, which a page several requests deep can miss
// when the whole suite is loading the machine. A longer ceiling only slows
// down genuine failures; passing tests return as soon as they match.
configure({ asyncUtilTimeout: 3000 })

// One MSW server for all component tests: start before, reset handlers between
// (so a test's per-test override doesn't leak), stop after.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// jsdom has no real WebSocket network stack; stub it globally so
// LiveEventsProvider can construct one in every test without erroring, and
// individual tests can drive MockWebSocket.instances[...] to simulate events.
beforeEach(() => {
  MockWebSocket.reset()
  vi.stubGlobal('WebSocket', MockWebSocket)
})

// Service worker, Push API, Notification and secure context, faked per test
// (src/test/pwaStubs.ts): supported, permission "default", no subscription,
// no BarcodeDetector, no camera. Tests re-install with their own options.
beforeEach(() => {
  installPwaStubs()
})
afterEach(() => restorePwaStubs())

// jsdom implements neither matchMedia nor requestAnimationFrame; framer-motion
// (introduced for the UI's transitions/pulses) reads both unconditionally, so
// without these stubs every test that renders an animated component throws.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

if (!window.requestAnimationFrame) {
  window.requestAnimationFrame = (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 16) as unknown as number
  window.cancelAnimationFrame = (id: number) => clearTimeout(id)
}

// jsdom has no IntersectionObserver either, and framer-motion's `whileInView`
// (used for the landing page's scroll reveals) constructs one on mount. The
// stub reports every observed element as immediately intersecting, so reveal
// animations settle into their visible end state and the content is queryable.
if (!('IntersectionObserver' in window)) {
  class StubIntersectionObserver implements IntersectionObserver {
    readonly root: Element | Document | null = null
    readonly rootMargin: string = '0px'
    readonly scrollMargin: string = '0px'
    readonly thresholds: ReadonlyArray<number> = [0]
    // A plain field rather than a constructor parameter property: the build's
    // tsconfig sets `erasableSyntaxOnly`, which rejects the shorthand.
    private readonly callback: IntersectionObserverCallback

    constructor(callback: IntersectionObserverCallback) {
      this.callback = callback
    }

    observe(target: Element) {
      this.callback([{ target, isIntersecting: true } as IntersectionObserverEntry], this)
    }
    unobserve() {}
    disconnect() {}
    takeRecords(): IntersectionObserverEntry[] {
      return []
    }
  }
  vi.stubGlobal('IntersectionObserver', StubIntersectionObserver)
}
