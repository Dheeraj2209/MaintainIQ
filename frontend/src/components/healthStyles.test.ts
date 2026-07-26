import { describe, expect, it } from 'vitest'
import { healthClasses, healthLabel } from './healthStyles'

describe('healthStyles', () => {
  it('maps each known state to a distinct badge class', () => {
    const states = ['healthy', 'degrading', 'faulty', 'critical'] as const
    const classes = states.map((s) => healthClasses(s).badge)
    expect(new Set(classes).size).toBe(states.length)
  })

  it('falls back to unknown styling for an unrecognized state', () => {
    expect(healthClasses('nonsense')).toEqual(healthClasses('unknown'))
  })

  it('capitalizes the label', () => {
    expect(healthLabel('critical')).toBe('Critical')
  })
})
