import { describe, expect, it } from 'vitest'
import {
  factorIntensity,
  causeLabel,
  deviceStateClasses,
  feedbackOutcomeLabel,
  feedbackOutcomeTone,
  deviceStateLabel,
  healthClasses,
  healthLabel,
  pageLevelTone,
  wasLadderPaged,
  workOrderPriorityTone,
  workOrderStatusLabel,
  workOrderStatusTone,
} from './healthStyles'

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

  it('maps each device state onto the existing health intensity tokens', () => {
    expect(deviceStateClasses('online')).toEqual(healthClasses('healthy'))
    expect(deviceStateClasses('stale')).toEqual(healthClasses('degrading'))
    expect(deviceStateClasses('offline')).toEqual(healthClasses('critical'))
    expect(deviceStateClasses('never_reported')).toEqual(healthClasses('unknown'))
    expect(deviceStateClasses('rebooting')).toEqual(healthClasses('unknown'))
  })

  it('labels each device state', () => {
    expect(deviceStateLabel('online')).toBe('Online')
    expect(deviceStateLabel('stale')).toBe('Stale')
    expect(deviceStateLabel('offline')).toBe('Offline')
    expect(deviceStateLabel('never_reported')).toBe('Never reported')
    expect(deviceStateLabel('rebooting')).toBe('Unknown')
  })

  it('maps each work-order status onto the badge tones', () => {
    expect(workOrderStatusTone('open')).toBe('neutral')
    expect(workOrderStatusTone('assigned')).toBe('accent')
    expect(workOrderStatusTone('in_progress')).toBe('degrading')
    expect(workOrderStatusTone('done')).toBe('healthy')
    expect(workOrderStatusTone('cancelled')).toBe('unknown')
    expect(workOrderStatusTone('archived')).toBe('unknown')
  })

  it('labels each work-order status', () => {
    expect(workOrderStatusLabel('open')).toBe('Open')
    expect(workOrderStatusLabel('assigned')).toBe('Assigned')
    expect(workOrderStatusLabel('in_progress')).toBe('In progress')
    expect(workOrderStatusLabel('done')).toBe('Done')
    expect(workOrderStatusLabel('cancelled')).toBe('Cancelled')
    expect(workOrderStatusLabel('archived')).toBe('Unknown')
  })

  it('reuses the severity ramp for work-order priority', () => {
    expect(workOrderPriorityTone('high')).toBe('critical')
    expect(workOrderPriorityTone('medium')).toBe('faulty')
    expect(workOrderPriorityTone('low')).toBe('degrading')
    expect(workOrderPriorityTone('urgent')).toBe('unknown')
  })

  it('gets louder with each paging level', () => {
    expect(pageLevelTone(0)).toBe('neutral')
    expect(pageLevelTone(1)).toBe('degrading')
    expect(pageLevelTone(2)).toBe('critical')
    expect(pageLevelTone(3)).toBe('critical')
  })

  it('labels and tones each feedback outcome, falling back for an unknown value', () => {
    expect(feedbackOutcomeLabel('confirmed_failure')).toBe('Confirmed failure')
    expect(feedbackOutcomeLabel('maintenance_prevented')).toBe('Prevented by maintenance')
    expect(feedbackOutcomeLabel('false_alarm')).toBe('False alarm')
    expect(feedbackOutcomeLabel('unknown')).toBe('Unknown')
    expect(feedbackOutcomeLabel('mystery')).toBe('Unknown')

    expect(feedbackOutcomeTone('confirmed_failure')).toBe('critical')
    expect(feedbackOutcomeTone('maintenance_prevented')).toBe('healthy')
    expect(feedbackOutcomeTone('false_alarm')).toBe('degrading')
    expect(feedbackOutcomeTone('unknown')).toBe('unknown')
    expect(feedbackOutcomeTone('mystery')).toBe('unknown')
  })

  it('labels root causes and passes unrecognised ones through', () => {
    expect(causeLabel('bearing_wear')).toBe('Bearing wear')
    expect(causeLabel('imbalance')).toBe('Imbalance')
    expect(causeLabel('sensor_or_data_quality_issue')).toBe('Sensor / data quality issue')
    expect(causeLabel('unknown')).toBe('Unknown')
    expect(causeLabel('other')).toBe('Other')
    expect(causeLabel('misalignment')).toBe('misalignment')
    expect(causeLabel(null)).toBe('Not specified')
  })

  it("maps a key factor's share of the top score onto the intensity ramp", () => {
    expect(factorIntensity(1, 'up')).toBe('critical')
    expect(factorIntensity(0.66, 'down')).toBe('critical')
    expect(factorIntensity(0.5, 'up')).toBe('faulty')
    expect(factorIntensity(0.2, 'up')).toBe('degrading')
    expect(factorIntensity(0.9, 'steady')).toBe('unknown')
  })
})

describe('wasLadderPaged', () => {
  it('needs a level above 0 and a last_paged_at', () => {
    expect(wasLadderPaged({ page_level: 1, last_paged_at: '2026-10-07T12:15:00Z' })).toBe(true)
    expect(wasLadderPaged({ page_level: 0, last_paged_at: '2026-10-07T12:00:00Z' })).toBe(false)
    // Pre-ladder / batch seed alerts: top level, never paged.
    expect(wasLadderPaged({ page_level: 2, last_paged_at: null })).toBe(false)
  })
})
