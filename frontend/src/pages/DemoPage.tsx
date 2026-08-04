import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { DemoSeverity, MachineSummary, SimulateFaultResponse } from '../api/types'
import { api } from '../api/client'

const SEVERITIES: DemoSeverity[] = ['healthy', 'degrading', 'faulty', 'critical']

interface LogEntry extends SimulateFaultResponse {
  requested_severity: DemoSeverity
  at: string
}

export function DemoPage() {
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [machineId, setMachineId] = useState('')
  const [severity, setSeverity] = useState<DemoSeverity>('critical')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [log, setLog] = useState<LogEntry[]>([])

  useEffect(() => {
    api
      .getMachines()
      .then((m) => {
        setMachines(m)
        // Defaulting to m[0] regardless of state used to silently no-op the
        // walkthrough: this dataset's machines are real bearing-failure runs,
        // so the first machine is often already sitting on an open critical
        // alert, and simulating "critical" again does nothing (see
        // apply_reading's same-or-lower-severity guard in src/alerts/live.py)
        // — no new alert, no broadcast, no email. Prefer a currently-healthy
        // machine so the default action actually produces a visible change.
        const healthy = m.find((x) => x.health_state === 'healthy')
        setMachineId((prev) => prev || healthy?.machine_id || m[0]?.machine_id || '')
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load machines'))
  }, [])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!machineId) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.simulateFault({ machine_id: machineId, severity })
      setLog((prev) => [
        { ...result, requested_severity: severity, at: new Date().toLocaleTimeString() },
        ...prev,
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to simulate fault')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Demo control</h1>
        <p className="text-xs text-text-muted">
          Simulate a sensor reading to drive the live prediction → alert → email → dashboard pipeline without
          waiting on real hardware. Open the dashboard in another session to watch it update live.
        </p>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="glass rounded-2xl p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Trigger a reading</h2>
          <form onSubmit={handleSubmit} className="grid gap-2">
            <label className="grid gap-0.5 text-xs text-text-muted">
              Machine
              <select
                value={machineId}
                onChange={(e) => setMachineId(e.target.value)}
                className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
              >
                {machines.map((m) => (
                  <option key={m.machine_id} value={m.machine_id}>
                    {m.machine_id} — {m.health_state}
                    {m.open_alert_count > 0 ? ' (alert open)' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-0.5 text-xs text-text-muted">
              Severity
              <select
                value={severity}
                onChange={(e) => setSeverity(e.target.value as DemoSeverity)}
                className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
              >
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-xs text-text-muted">
              No effect if the machine is already at or above this severity — pick a lower severity
              or a different machine to see a new alert/email.
            </p>
            <button
              type="submit"
              disabled={busy || !machineId}
              className="mt-1 justify-self-start rounded-md bg-accent px-4 py-2 text-sm font-semibold text-black transition hover:bg-accent-hover disabled:opacity-50"
            >
              {busy ? 'Simulating…' : 'Simulate reading'}
            </button>
          </form>
        </section>

        <section className="glass rounded-2xl p-4 lg:col-span-2">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Simulation log</h2>
          {log.length === 0 ? (
            <p className="py-4 text-sm text-text-muted">No simulations run yet this session.</p>
          ) : (
            <ul className="space-y-2">
              {log.map((entry, i) => (
                <li key={i} className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-text">
                      {entry.machine_id} → {entry.requested_severity}
                    </span>
                    <span className="text-xs text-text-muted">{entry.at}</span>
                  </div>
                  <p className="mt-1 text-text-muted">
                    Health state: <span className="text-text">{entry.health_state}</span>
                    {entry.probable_cause && <> · Cause: {entry.probable_cause}</>}
                  </p>
                  <p className="mt-0.5 text-xs text-text-muted">
                    {entry.alert
                      ? `Alert ${entry.alert.status === 'resolved' ? 'resolved' : entry.alert.severity + ' severity'} · ${entry.emails_sent} email(s) sent`
                      : 'No alert state change'}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
