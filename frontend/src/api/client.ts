// Thin typed fetch wrapper around the FastAPI backend. Base URL comes from
// VITE_API_BASE_URL (see .env.example); defaults to '/api', which works both
// in dev (Vite proxy) and prod (FastAPI serves the SPA from the same origin).
import type {
  Alert,
  KpiSummary,
  MachineDetail,
  MachineSummary,
  MaintenanceCreate,
  MaintenanceRecord,
  TrendPoint,
} from './types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  getKpiSummary: () => request<KpiSummary>('/kpis'),
  getMachines: () => request<MachineSummary[]>('/machines'),
  getMachine: (id: string) => request<MachineDetail>(`/machines/${encodeURIComponent(id)}`),
  getTrends: (id: string, metric: string, limit = 500) =>
    request<TrendPoint[]>(
      `/machines/${encodeURIComponent(id)}/trends?metric=${encodeURIComponent(metric)}&limit=${limit}`,
    ),
  getAlerts: (status?: string) =>
    request<Alert[]>(`/alerts${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  getMaintenanceHistory: (id: string) =>
    request<MaintenanceRecord[]>(`/maintenance/${encodeURIComponent(id)}`),
  logMaintenance: (payload: MaintenanceCreate) =>
    request<MaintenanceRecord>('/maintenance', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
