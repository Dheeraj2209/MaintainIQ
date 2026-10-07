import { describe, expect, it } from 'vitest'
import {
  causeText,
  checkText,
  directionText,
  formatRatio,
  formatValue,
  hasOutOfRange,
  matchReasonLabel,
  orderWarnings,
  rulText,
} from './explanationFormat'
import { alertExplanation, alertExplanationReconstructed } from '../test/fixtures'

describe('explanationFormat', () => {
  it('formats values with one decimal for whole numbers and three significant digits otherwise', () => {
    expect(formatValue(5)).toBe('5.0')
    expect(formatValue(9.4)).toBe('9.4')
    expect(formatValue(0.4213)).toBe('0.421')
    expect(formatValue(2100)).toBe('2100')
    expect(formatValue(null)).toBe('—')
  })

  it('formats ratios and directions against the baseline', () => {
    expect(formatRatio(3.13)).toBe('3.1')
    expect(formatRatio(0.5)).toBe('0.5')
    expect(formatRatio(0.04)).toBe('0.04')
    expect(formatRatio(14.6)).toBe('15')
    expect(directionText('up', 3.13)).toBe('↑ 3.1× baseline')
    expect(directionText('down', 0.5)).toBe('↓ 0.5× baseline')
    expect(directionText('steady', 1.01)).toBe('≈ baseline')
  })

  it('writes a rule check as a sentence with its outcome', () => {
    expect(checkText({ feature: 'vibration_h_kurtosis', value: 9.4, operator: '>=', threshold: 5, passed: true })).toBe(
      'Horizontal kurtosis 9.4 ≥ 5.0 — passed',
    )
    expect(checkText({ feature: 'vibration_h_rms', value: 0, operator: '>', threshold: 0, passed: false })).toBe(
      'Horizontal RMS 0.0 > 0.0 — not met',
    )
    expect(
      checkText({ feature: 'vibration_h_kurtosis', value: null, operator: 'is missing', threshold: null, passed: true }),
    ).toBe('Horizontal kurtosis is missing — passed')
  })

  it('words causes in lower case and match reasons for people', () => {
    expect(causeText('bearing_wear')).toBe('bearing wear')
    expect(causeText('sensor_or_data_quality_issue')).toBe('sensor / data quality issue')
    expect(causeText(null)).toBe('not specified')
    expect(matchReasonLabel('same_probable_cause')).toBe('Same probable cause')
    expect(matchReasonLabel('actual_cause_matches')).toBe('Actual cause matched')
    expect(matchReasonLabel('something_new')).toBe('something new')
  })

  it('writes RUL as a point estimate with its interval or as a lower bound', () => {
    expect(rulText(alertExplanation.prediction!)).toBe('42 min (90% interval 12–72)')
    expect(rulText({ ...alertExplanation.prediction!, rul_estimate_kind: 'lower_bound', predicted_rul_minutes: 120 })).toBe(
      '> 120 min (lower bound)',
    )
    expect(rulText({ ...alertExplanation.prediction!, prediction_interval_low: null })).toBe('42 min')
    expect(rulText({ ...alertExplanation.prediction!, predicted_rul_minutes: null })).toBe('—')
  })

  it('puts out-of-distribution warnings first', () => {
    expect(orderWarnings(['a: x', 'out_of_distribution: y', 'b: z'])).toEqual(['out_of_distribution: y', 'a: x', 'b: z'])
  })

  it('detects anything outside the training range', () => {
    expect(hasOutOfRange(alertExplanation)).toBe(true)
    expect(hasOutOfRange(alertExplanationReconstructed)).toBe(false)
    const inRange = structuredClone(alertExplanation)
    inRange.prediction!.out_of_distribution = false
    expect(hasOutOfRange(inRange)).toBe(true) // a key factor is still out of range
    inRange.key_factors!.factors.forEach((f) => (f.outside_training_bounds = false))
    expect(hasOutOfRange(inRange)).toBe(false)
    inRange.key_factors!.operating_conditions[0].outside_training_bounds = true
    expect(hasOutOfRange(inRange)).toBe(true)
  })
})
