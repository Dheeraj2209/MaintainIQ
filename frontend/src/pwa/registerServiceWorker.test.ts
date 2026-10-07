import { describe, expect, it } from 'vitest'
import { installPwaStubs } from '../test/pwaStubs'
import { registerServiceWorker } from './registerServiceWorker'

describe('registerServiceWorker', () => {
  it('registers /sw.js at the root scope in a production build', () => {
    const stubs = installPwaStubs()
    registerServiceWorker(true)
    expect(stubs.register).toHaveBeenCalledWith('/sw.js', { scope: '/' })
  })

  it('does nothing in a dev or test build', () => {
    const stubs = installPwaStubs()
    registerServiceWorker()
    registerServiceWorker(false)
    expect(stubs.register).not.toHaveBeenCalled()
  })

  it('does nothing without service-worker support', () => {
    installPwaStubs({ serviceWorker: false })
    expect(() => registerServiceWorker(true)).not.toThrow()
  })
})
