// Thin typed fetch wrapper around the FastAPI backend. Base URL comes from
// VITE_API_BASE_URL (see README); defaults to '/api', which works both in dev
// (Vite proxy) and prod (FastAPI serves the SPA from the same origin).
import type {
  Alert,
  KpiSummary,
  MachineDetail,
  MachineSummary,
  MaintenanceCreate,
  MaintenanceRecord,
  ModelHealth,
  ModelTelemetry,
  NotificationOut,
  Report,
  ReportCreateRequest,
  ReplayStartRequest,
  ReplayStartResponse,
  ReplayStatus,
  ReplayStopResponse,
  SimulateFaultRequest,
  SimulateFaultResponse,
  TrendPoint,
  UserCreate,
  UserOut,
  UserUpdate,
} from './types'

export const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// Shared response handling: fire the app-wide logout event on 401 and turn any
// non-2xx into an ApiError carrying the backend `detail`. Used by both the JSON
// `request` helper and the raw `downloadReport` fetch below.
async function throwIfError(res: Response): Promise<void> {
  if (res.status === 401) {
    window.dispatchEvent(new Event('miq:unauthorized'))
  }
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
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    // Session is an httpOnly cookie (src/auth/security.py) — 'same-origin' is
    // fetch's default, but spelled out here since the whole app's auth model
    // depends on it: both dev (Vite proxy) and prod (FastAPI serves the SPA)
    // keep API calls same-origin from the browser's point of view.
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  await throwIfError(res)
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  login: (email: string, password: string) =>
    request<UserOut>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
  me: () => request<UserOut>('/auth/me'),

  getKpiSummary: () => request<KpiSummary>('/kpis'),
  getMachines: () => request<MachineSummary[]>('/machines'),
  getMachine: (id: string) => request<MachineDetail>(`/machines/${encodeURIComponent(id)}`),
  getTrends: (id: string, metric: string, limit = 500) =>
    request<TrendPoint[]>(
      `/machines/${encodeURIComponent(id)}/trends?metric=${encodeURIComponent(metric)}&limit=${limit}`,
    ),
  getAlerts: (status?: string) =>
    request<Alert[]>(`/alerts${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  getMaintenanceHistory: (id: string, { limit = 20, offset = 0 }: { limit?: number; offset?: number } = {}) =>
    request<MaintenanceRecord[]>(`/maintenance/${encodeURIComponent(id)}?limit=${limit}&offset=${offset}`),
  logMaintenance: (payload: MaintenanceCreate) =>
    request<MaintenanceRecord>('/maintenance', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  acknowledgeAlert: (id: number) =>
    request<Alert>(`/alerts/${id}/acknowledge`, { method: 'POST' }),

  listUsers: () => request<UserOut[]>('/users'),
  createUser: (payload: UserCreate) =>
    request<UserOut>('/users', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: UserUpdate) =>
    request<UserOut>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  listNotifications: (status?: string) =>
    request<NotificationOut[]>(`/notifications${status ? `?status=${encodeURIComponent(status)}` : ''}`),

  simulateFault: (payload: SimulateFaultRequest) =>
    request<SimulateFaultResponse>('/demo/simulate-fault', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  resetMachine: (machineId: string) =>
    request<SimulateFaultResponse>(`/demo/reset-machine/${encodeURIComponent(machineId)}`, {
      method: 'POST',
    }),

  getModelHealth: () => request<ModelHealth>('/model/health'),
  getModelTelemetry: (windowMinutes?: number) =>
    request<ModelTelemetry>(`/model/telemetry${windowMinutes != null ? `?window_minutes=${windowMinutes}` : ''}`),

  startReplay: (payload: ReplayStartRequest) =>
    request<ReplayStartResponse>('/ingestion/replay/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  stopReplay: (machineId: string) =>
    request<ReplayStopResponse>('/ingestion/replay/stop', {
      method: 'POST',
      body: JSON.stringify({ machine_id: machineId }),
    }),
  getReplayStatus: () => request<ReplayStatus>('/ingestion/replay/status'),

  createReport: (payload: ReportCreateRequest) =>
    request<Report>('/reports', { method: 'POST', body: JSON.stringify(payload) }),
  listReports: (scope?: string) =>
    request<Report[]>(`/reports${scope ? `?scope=${encodeURIComponent(scope)}` : ''}`),
  getReport: (id: number) => request<Report>(`/reports/${id}`),
  // Raw download: the endpoint returns the file body (not JSON) with a
  // Content-Disposition filename. Returns the parsed filename + text content so
  // the caller can build a Blob and trigger a browser download.
  downloadReport: async (id: number): Promise<{ filename: string; content: string }> => {
    const res = await fetch(`${BASE}/reports/${id}/download`, { credentials: 'same-origin' })
    await throwIfError(res)
    const disposition = res.headers.get('Content-Disposition') ?? ''
    const match = /filename="?([^"]+)"?/.exec(disposition)
    const filename = match ? match[1] : `report-${id}`
    const content = await res.text()
    return { filename, content }
  },
}
