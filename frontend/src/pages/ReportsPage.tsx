import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { toast } from 'sonner'
import type { Report, ReportFormat, ReportType } from '../api/types'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Card } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Select } from '../components/ui/input'
import { Badge } from '../components/ui/badge'

const ALL_TYPES: ReportType[] = ['machine_prognostic', 'model_performance', 'fleet_summary']

// Human-friendly labels for the <option> elements. The visible text
// deliberately differs from the raw ReportType strings (spaces instead of
// underscores) so it doesn't collide with the identical literal strings the
// reports table renders (e.g. "machine_prognostic") in unscoped text
// queries; the raw type is still exposed via aria-label so role/name-based
// queries (and the RBAC test) can find the option by its literal type.
function labelForType(t: ReportType): string {
  return t.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

export function ReportsPage() {
  const { user } = useAuth()
  const isOperator = user?.role === 'operator'
  const allowedTypes: ReportType[] = isOperator ? ['machine_prognostic'] : ALL_TYPES

  const [reports, setReports] = useState<Report[]>([])
  const [reportType, setReportType] = useState<ReportType>('machine_prognostic')
  const [scope, setScope] = useState('m1')
  const [format, setFormat] = useState<ReportFormat>('json')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    api
      .listReports()
      .then(setReports)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load reports'))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleGenerate(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.createReport({ report_type: reportType, scope, format })
      toast.success('Report generated')
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate report')
    } finally {
      setBusy(false)
    }
  }

  async function handleDownload(id: number) {
    try {
      const { filename, content } = await api.downloadReport(id)
      const blob = new Blob([content], { type: 'text/plain' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to download report')
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Reports</h1>
        <p className="text-xs text-text-muted">Generate and download prognostic, model-performance, and fleet reports</p>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      <Card className="panel-notch p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Generate a report</h2>
        <form onSubmit={handleGenerate} className="flex flex-wrap items-end gap-3">
          <label className="grid gap-0.5 text-xs text-text-muted">
            Report type
            <Select value={reportType} onChange={(e) => setReportType(e.target.value as ReportType)}>
              {allowedTypes.map((t) => (
                <option key={t} value={t} aria-label={t}>
                  {labelForType(t)}
                </option>
              ))}
            </Select>
          </label>
          <label className="grid gap-0.5 text-xs text-text-muted">
            Scope
            <input
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              className="w-40 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25"
            />
          </label>
          <label className="grid gap-0.5 text-xs text-text-muted">
            Format
            <Select value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
              <option value="json">json</option>
              <option value="markdown">markdown</option>
            </Select>
          </label>
          <Button type="submit" disabled={busy}>
            {busy ? 'Generating…' : 'Generate'}
          </Button>
        </form>
      </Card>

      <Card className="panel-notch overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="bg-white/[0.04]">
            <tr className="text-xs uppercase text-text-muted">
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Scope</th>
              <th className="px-3 py-2">Format</th>
              <th className="px-3 py-2">Generated</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {reports.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-text-muted">No reports generated yet.</td>
              </tr>
            ) : (
              reports.map((r) => (
                <tr key={r.id} className="border-t border-white/10">
                  <td className="px-3 py-2 text-text">{r.report_type}</td>
                  <td className="px-3 py-2 font-mono text-text-muted">{r.scope}</td>
                  <td className="px-3 py-2">
                    <Badge variant="unknown">{r.format}</Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-text-muted">{r.generated_at}</td>
                  <td className="px-3 py-2">
                    <Button type="button" variant="outline" size="sm" onClick={() => handleDownload(r.id)}>
                      Download
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
