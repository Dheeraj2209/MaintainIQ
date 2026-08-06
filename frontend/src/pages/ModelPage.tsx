import { useEffect, useState } from 'react'
import type { ModelHealth, ModelTelemetry } from '../api/types'
import { api } from '../api/client'
import { Card } from '../components/ui/card'
import { MetricCard } from '../components/ui/metric-card'

function fmt(n: number | null, digits = 1): string {
  return n != null ? n.toFixed(digits) : '—'
}

function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

export function ModelPage() {
  const [health, setHealth] = useState<ModelHealth | null>(null)
  const [telemetry, setTelemetry] = useState<ModelTelemetry | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    Promise.all([api.getModelHealth(), api.getModelTelemetry()])
      .then(([h, t]) => {
        setHealth(h)
        setTelemetry(t)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load model observability'))
  }, [])

  if (error) {
    return (
      <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
    )
  }

  if (!health || !telemetry) {
    return <p className="text-sm text-text-muted">Loading model observability…</p>
  }

  const healthTone = health.status === 'healthy' ? 'healthy' : 'critical'

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Model observability</h1>
        <p className="text-xs text-text-muted">Inference heartbeat and rolling telemetry for the active RUL model</p>
      </div>

      <Card className="panel-notch p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-text-muted">Health</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCard label="Status" value={health.status} tone={healthTone} />
          <MetricCard label="Model version" value={health.model_version ?? '—'} tone="accent" />
          <MetricCard label="Active" value={health.active ? 'Yes' : 'No'} tone={health.active ? 'healthy' : 'unknown'} />
          <MetricCard
            label="Since last inference (s)"
            value={fmt(health.seconds_since_last_inference)}
          />
        </div>
        <p className="mt-2 text-xs text-text-muted">Last inference: {health.last_inference_at ?? 'never'}</p>
      </Card>

      <Card className="panel-notch p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-text-muted">
          Telemetry (last {telemetry.window_minutes} min)
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <MetricCard label="Inferences" value={telemetry.inference_count} tone="accent" />
          <MetricCard label="Error rate" value={pct(telemetry.error_rate)} tone="critical" />
          <MetricCard label="OOD rate" value={pct(telemetry.ood_rate)} tone="degrading" />
          <MetricCard label="Warming-up rate" value={pct(telemetry.warming_up_rate)} tone="degrading" />
          <MetricCard label="Latency p50 (ms)" value={fmt(telemetry.latency_p50_ms)} />
          <MetricCard label="Latency p95 (ms)" value={fmt(telemetry.latency_p95_ms)} />
        </div>
      </Card>
    </div>
  )
}
