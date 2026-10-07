import { describe, expect, it } from 'vitest'
import { returnPath } from './returnPath'

describe('returnPath', () => {
  it('rebuilds pathname + search + hash from the location RequireAuth saved', () => {
    expect(returnPath({ from: { pathname: '/m/alerts/3', search: '?x=1', hash: '#why' } })).toBe('/m/alerts/3?x=1#why')
    expect(returnPath({ from: { pathname: '/alerts' } })).toBe('/alerts')
  })

  it('falls back to /dashboard for no state or anything not a same-origin path', () => {
    expect(returnPath(null)).toBe('/dashboard')
    expect(returnPath(undefined)).toBe('/dashboard')
    expect(returnPath({})).toBe('/dashboard')
    expect(returnPath({ from: { pathname: '//evil.com' } })).toBe('/dashboard')
    expect(returnPath({ from: { pathname: 'https://evil.com/x' } })).toBe('/dashboard')
    expect(returnPath({ from: { pathname: '/\\evil.com' } })).toBe('/dashboard')
    expect(returnPath({ from: { pathname: 42 } })).toBe('/dashboard')
  })
})
