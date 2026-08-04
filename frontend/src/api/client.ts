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
  NotificationOut,
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
  getMaintenanceHistory: (id: string) =>
    request<MaintenanceRecord[]>(`/maintenance/${encodeURIComponent(id)}`),
  logMaintenance: (payload: MaintenanceCreate) =>
    request<MaintenanceRecord>('/maintenance', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

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
}
