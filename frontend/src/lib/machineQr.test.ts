import { describe, expect, it } from 'vitest'
import { machineDeepLink, parseMachineQr } from './machineQr'

describe('machineDeepLink', () => {
  it('encodes the id into an absolute /m/machines/ URL that round-trips', () => {
    const url = machineDeepLink('https://h', 'Bearing 1/1')
    expect(url).toBe('https://h/m/machines/Bearing%201%2F1')
    expect(parseMachineQr(url, 'https://h')).toBe('Bearing 1/1')
  })
})

describe('parseMachineQr', () => {
  const origin = 'https://plant.example'

  it('accepts a same-origin machine label, with or without a trailing slash', () => {
    expect(parseMachineQr('https://plant.example/m/machines/m1', origin)).toBe('m1')
    expect(parseMachineQr('https://plant.example/m/machines/m1/', origin)).toBe('m1')
  })

  it.each([
    ['another origin', 'https://evil.example/m/machines/m1'],
    ['another port', 'https://plant.example:8443/m/machines/m1'],
    ['a desktop path', 'https://plant.example/machines/m1'],
    ['no id', 'https://plant.example/m/machines/'],
    ['an extra segment', 'https://plant.example/m/machines/m1/extra'],
    ['a javascript: URL', 'javascript:alert(1)'],
    ['plain text', 'm1'],
    ['a relative path', '/m/machines/m1'],
    ['a broken escape', 'https://plant.example/m/machines/%E0%A4%A'],
  ])('rejects %s', (_label, text) => {
    expect(parseMachineQr(text, origin)).toBeNull()
  })
})
