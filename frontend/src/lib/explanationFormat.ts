// Display rules for "Why this alert?" (design/2026-10-07-alert-explanation-design.md),
// kept out of the component files so those only export components (fast
// refresh) and so the wording can be unit-tested without rendering.
import type { AlertExplanation, ExplanationPrediction, FactorDirection, RuleCheck } from '../api/types'
import { causeLabel } from '../components/healthStyles'

// One decimal for whole numbers (a threshold reads "5.0"), three significant
// digits otherwise, and no decimals once a value reaches the hundreds (rpm).
export function formatValue(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 100) return value.toFixed(0)
  if (Number.isInteger(value)) return value.toFixed(1)
  return String(Number(value.toPrecision(3)))
}

export function formatRatio(ratio: number): string {
  if (ratio >= 10) return ratio.toFixed(0)
  if (ratio >= 0.1) return ratio.toFixed(1)
  return String(Number(ratio.toPrecision(1)))
}

export function directionText(direction: FactorDirection, ratio: number): string {
  if (direction === 'up') return `↑ ${formatRatio(ratio)}× baseline`
  if (direction === 'down') return `↓ ${formatRatio(ratio)}× baseline`
  return '≈ baseline'
}

// The promoted vibration columns the probable-cause rules read
// (src/root_cause/rule_based.py), named as the trigger chart names them.
const CHECK_FEATURE_LABEL: Record<string, string> = {
  vibration_h_rms: 'Horizontal RMS',
  vibration_h_kurtosis: 'Horizontal kurtosis',
  vibration_v_rms: 'Vertical RMS',
  vibration_v_kurtosis: 'Vertical kurtosis',
}

const OPERATOR_SYMBOL: Record<string, string> = { '>=': '≥', '<=': '≤' }

// "Horizontal kurtosis 9.4 ≥ 5.0 — passed" / "… — not met".
export function checkText(check: RuleCheck): string {
  const feature = CHECK_FEATURE_LABEL[check.feature] ?? check.feature
  const operator = OPERATOR_SYMBOL[check.operator] ?? check.operator
  const comparison =
    check.threshold == null
      ? `${feature} ${operator}`
      : `${feature} ${formatValue(check.value)} ${operator} ${formatValue(check.threshold)}`
  return `${comparison} — ${check.passed ? 'passed' : 'not met'}`
}

// A cause inside a sentence ("Actual cause: bearing wear").
export function causeText(cause: string | null | undefined): string {
  return causeLabel(cause).toLowerCase()
}

const MATCH_REASON_LABEL: Record<string, string> = {
  same_probable_cause: 'Same probable cause',
  actual_cause_matches: 'Actual cause matched',
  same_machine: 'Same machine',
  same_severity: 'Same severity',
}

export function matchReasonLabel(reason: string): string {
  return MATCH_REASON_LABEL[reason] ?? reason.replaceAll('_', ' ')
}

// "42 min (90% interval 12–72)", or "> 120 min (lower bound)" when the model
// only knows the machine has at least that long.
export function rulText(prediction: ExplanationPrediction): string {
  const rul = prediction.predicted_rul_minutes
  if (rul == null) return '—'
  const minutes = `${Math.round(rul)} min`
  if (prediction.rul_estimate_kind === 'lower_bound') return `> ${minutes} (lower bound)`
  const { prediction_interval_low: low, prediction_interval_high: high } = prediction
  if (low == null || high == null) return minutes
  return `${minutes} (90% interval ${Math.round(low)}–${Math.round(high)})`
}

export function orderWarnings(warnings: string[]): string[] {
  const ood = (w: string) => w.startsWith('out_of_distribution')
  return [...warnings.filter(ood), ...warnings.filter((w) => !ood(w))]
}

// The OOD banner shows whenever the prediction or any input the explanation
// checked against the training bounds is outside them.
export function hasOutOfRange(explanation: AlertExplanation): boolean {
  if (explanation.prediction?.out_of_distribution) return true
  const factors = explanation.key_factors
  if (!factors) return false
  return (
    factors.factors.some((f) => f.outside_training_bounds === true) ||
    factors.operating_conditions.some((c) => c.outside_training_bounds === true)
  )
}
