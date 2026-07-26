import { useCallback, useEffect, useState } from 'react'
import type { Alert, KpiSummary, MachineSummary } from './api/types'
import { api } from './api/client'
import { KpiCards } from './components/KpiCards'
import { MachineGrid } from './components/MachineGrid'
import { AlertsPanel } from './components/AlertsPanel'
import { MachineDetail } from './components/MachineDetail'

export default function App() {
  const [kpis, setKpis] = useState<KpiSummary | null>(null)
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const loadFleet = useCallback(() => {
    setError(null)
    Promise.all([api.getKpiSummary(), api.getMachines(), api.getAlerts('open')])
      .then(([k, m, a]) => {
        setKpis(k)
        setMachines(m)
        setAlerts(a)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load fleet data'))
  }, [])

  useEffect(() => {
    loadFleet()
  }, [loadFleet, refreshKey])

  const handleLogged = useCallback(() => setRefreshKey((k) => k + 1), [])

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
          <div>
            <h1 className="text-xl font-bold">MaintainIQ</h1>
            <p className="text-xs text-slate-500">Predictive maintenance dashboard</p>
          </div>
          <button
            type="button"
            onClick={loadFleet}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100"
          >
            Refresh
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-6 px-4 py-6">
        {error && (
          <div className="rounded-lg border border-critical/40 bg-critical/5 p-3 text-sm text-critical">
            {error}
          </div>
        )}

        {kpis && <KpiCards summary={kpis} />}

        <div className="grid gap-6 lg:grid-cols-3">
          <section aria-label="Machine list" className="lg:col-span-2">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Machines</h2>
            <MachineGrid machines={machines} selectedId={selectedId} onSelect={setSelectedId} />
          </section>

          <section aria-label="Open alerts">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Open alerts</h2>
            <AlertsPanel alerts={alerts} onSelect={setSelectedId} />
          </section>
        </div>

        {selectedId && (
          <MachineDetail key={selectedId} machineId={selectedId} onLogged={handleLogged} />
        )}
      </main>
    </div>
  )
}
