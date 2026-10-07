import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { isFeedbackEvent } from '../api/types'
import type { FieldAccuracy, ModelHealth, ModelTelemetry } from '../api/types'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Button } from '../components/ui/button'
import { Card } from '../components/ui/card'
import { MetricCard } from '../components/ui/metric-card'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

function fmt(n: number | null, digits = 1): string {
  return n != null ? n.toFixed(digits) : '—'
}

function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

function pctOrDash(rate: number | null | undefined): string {
  return rate != null ? pct(rate) : '—'
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
    <div className="rise-children space-y-6">
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

      <FieldAccuracyCard />
    </div>
  )
}

// Real-world accuracy from the outcomes operators record when they close
// alerts (design/2026-10-07-prediction-feedback-design.md). Loaded on its own,
// outside the page's Promise.all, so a failure stays inside this card; it
// re-fetches whenever someone closes an alert or records an outcome.
function FieldAccuracyCard() {
  const { user } = useAuth()
  const { lastEvent } = useLiveEvents()
  const [accuracy, setAccuracy] = useState<FieldAccuracy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const canExport = user?.role === 'admin' || user?.role === 'supervisor'

  const load = useCallback(() => {
    api
      .getFieldAccuracy()
      .then((a) => {
        setAccuracy(a)
        setError(null)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load field accuracy'))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (lastEvent && isFeedbackEvent(lastEvent)) load()
  }, [lastEvent, load])

  async function handleExport() {
    setExporting(true)
    try {
      const { filename, content, episodeCount } = await api.downloadFeedbackExport()
      const url = URL.createObjectURL(new Blob([content], { type: 'text/csv' }))
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      toast.success(`Exported ${episodeCount} episode(s)`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to export feedback')
    } finally {
      setExporting(false)
    }
  }

  const nothingToExport = accuracy?.exportable_episode_count === 0

  return (
    <Card role="region" aria-labelledby="field-accuracy-heading" className="panel-notch p-4">
      <h2 id="field-accuracy-heading" className="mb-3 text-sm font-semibold uppercase tracking-wide text-text-muted">
        Field accuracy (operator feedback)
      </h2>
      {error ? (
        <div role="alert" className="rounded-xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
          {error}
        </div>
      ) : !accuracy ? (
        <p className="text-sm text-text-muted">Loading field accuracy…</p>
      ) : (
        <FieldAccuracyBody accuracy={accuracy} />
      )}

      {canExport && accuracy && (
        <div className="mt-4 border-t border-white/10 pt-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={exporting || nothingToExport}
            title={nothingToExport ? 'No confirmed failures with a failure time yet' : undefined}
            onClick={() => void handleExport()}
          >
            {exporting ? 'Exporting…' : 'Export retraining CSV'}
          </Button>
          <p className="mt-1 text-xs text-text-muted">
            Merge with the XJTU-SY feature table and retrain manually — see README.
          </p>
        </div>
      )}
    </Card>
  )
}

function FieldAccuracyBody({ accuracy }: { accuracy: FieldAccuracy }) {
  if (accuracy.status !== 'available') {
    return (
      <p className="text-sm text-text-muted">
        No labelled alerts yet — close alerts with an outcome to start measuring field accuracy.
      </p>
    )
  }
  const { lead_time: lead, rul_error: rul, offline } = accuracy
  return (
    <>
      {/* String values: counts here are small, and the count-up animation
          would misreport them for its first frames. */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <MetricCard label="Labelled alerts" value={String(accuracy.labelled_count)} tone="accent" />
        <MetricCard label="Precision" value={pctOrDash(accuracy.precision)} tone="healthy" />
        <MetricCard label="False-alarm rate" value={pctOrDash(accuracy.false_alarm_rate)} tone="degrading" />
        <MetricCard
          label="Median lead time (min)"
          value={fmt(lead.median_minutes)}
          sub={`${lead.within_horizon_count}/${lead.count} within ${accuracy.horizon_minutes} min`}
        />
        <MetricCard
          label="RUL MAE (min, point estimates)"
          value={fmt(rul.mae_minutes)}
          sub={`${rul.lower_bound_count} censored lower bounds excluded`}
        />
        <MetricCard label="Root-cause accuracy" value={pctOrDash(accuracy.root_cause.accuracy)} tone="accent" />
      </div>

      <p className="mt-3 text-xs text-text-muted">
        {offline
          ? `Offline benchmark (per snapshot): precision ${pctOrDash(offline.precision)} · recall ${pctOrDash(offline.recall)} (model ${offline.model_version ?? '—'})`
          : 'No offline benchmark recorded for the active model'}
      </p>

      {accuracy.by_model_version.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm" aria-label="By model version">
            <thead>
              <tr className="text-xs uppercase text-text-muted">
                <th className="py-1 pr-3">Version</th>
                <th className="py-1 pr-3">Labelled</th>
                <th className="py-1 pr-3">Precision</th>
                <th className="py-1 pr-3">False-alarm rate</th>
                <th className="py-1 pr-3">Median lead (min)</th>
                <th className="py-1">RUL MAE (min)</th>
              </tr>
            </thead>
            <tbody>
              {accuracy.by_model_version.map((v) => (
                <tr key={v.model_version ?? 'unlinked'} className="border-t border-white/10 font-mono text-text">
                  <td className="py-1 pr-3">
                    {v.model_version ?? <span className="font-sans text-text-muted">(unlinked)</span>}
                  </td>
                  <td className="py-1 pr-3">{v.labelled_count}</td>
                  <td className="py-1 pr-3">{pctOrDash(v.precision)}</td>
                  <td className="py-1 pr-3">{pctOrDash(v.false_alarm_rate)}</td>
                  <td className="py-1 pr-3">{fmt(v.median_lead_minutes)}</td>
                  <td className="py-1">{fmt(v.rul_mae_minutes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-xs text-text-muted">Missed failures can&apos;t be measured from alert feedback.</p>
    </>
  )
}
