import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { server } from './server'
import { MockWebSocket } from './mockWebSocket'

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
