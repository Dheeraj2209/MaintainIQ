// Thin typed fetch wrapper around the FastAPI backend. Base URL comes from
// VITE_API_BASE_URL (see README); defaults to '/api', which works both in dev
// (Vite proxy) and prod (FastAPI serves the SPA from the same origin).
import type {
  Alert,
  AlertCloseResponse,
  AlertExplanation,
  AlertFeedback,
  AlertFeedbackIn,
  Assignee,
  DeviceDetail,
  DeviceIncident,
  DeviceIncidentStatus,
  FieldAccuracy,
  KpiDetail,
  KpiSummary,
  MachineDetail,
  MachineSummary,
  MaintenanceCreate,
  MaintenanceRecord,
  ModelHealth,
  ModelTelemetry,
  NotificationChannel,
  NotificationOut,
  PushConfig,
  PushSubscriptionOut,
  PushTestResult,
  Report,
  ReportCreateRequest,
  ReplayStartRequest,
  ReplayStartResponse,
  ReplayStatus,
  ReplayStopResponse,
  SimulateFaultRequest,
  SimulateFaultResponse,
  TelemetryDevice,
  TelemetryStatus,
  TrendPoint,
  UserCreate,
  UserOut,
  UserUpdate,
  WorkOrder,
  WorkOrderComplete,
  WorkOrderCreate,
  WorkOrderDetail,
  WorkOrderFromAlert,
  WorkOrderStatus,
  WorkOrderUpdate,
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
  getKpiDetail: () => request<KpiDetail>('/kpis/detail'),
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
  // Prediction feedback (design/2026-10-07-prediction-feedback-design.md).
  // Closing resolves an open alert and records its outcome in one call; on an
  // already-resolved alert it only records the outcome (`closed: false`).
  closeAlert: (id: number, payload: AlertFeedbackIn) =>
    request<AlertCloseResponse>(`/alerts/${id}/close`, { method: 'POST', body: JSON.stringify(payload) }),
  submitAlertFeedback: (id: number, payload: AlertFeedbackIn) =>
    request<AlertFeedback>(`/alerts/${id}/feedback`, { method: 'PUT', body: JSON.stringify(payload) }),
  getAlertFeedback: (id: number) => request<AlertFeedback | null>(`/alerts/${id}/feedback`),
  // "Why this alert?" (design/2026-10-07-alert-explanation-design.md): the
  // alert's evidence snapshot (or a reconstruction) plus similar past
  // incidents. similar_limit is sent only when given (server default 5).
  getAlertExplanation: (id: number, similarLimit?: number) =>
    request<AlertExplanation>(
      `/alerts/${id}/explanation${similarLimit != null ? `?similar_limit=${similarLimit}` : ''}`,
    ),

  // Work orders (design/2026-10-07-work-orders-escalation-design.md). One
  // endpoint per action; the server enforces the status machine (409) and
  // the role/assignee rules (403).
  listWorkOrders: (
    opts: { status?: WorkOrderStatus | 'active'; machineId?: string; assignedTo?: number; alertId?: number } = {},
  ) => {
    const params = new URLSearchParams()
    if (opts.status) params.set('status', opts.status)
    if (opts.machineId) params.set('machine_id', opts.machineId)
    if (opts.assignedTo != null) params.set('assigned_to', String(opts.assignedTo))
    if (opts.alertId != null) params.set('alert_id', String(opts.alertId))
    const qs = params.toString()
    return request<WorkOrder[]>(`/work-orders${qs ? `?${qs}` : ''}`)
  },
  getWorkOrder: (id: number) => request<WorkOrderDetail>(`/work-orders/${id}`),
  getAssignees: () => request<Assignee[]>('/work-orders/assignees'),
  createWorkOrder: (payload: WorkOrderCreate) =>
    request<WorkOrder>('/work-orders', { method: 'POST', body: JSON.stringify(payload) }),
  createWorkOrderFromAlert: (alertId: number, payload: WorkOrderFromAlert = {}) =>
    request<WorkOrder>(`/alerts/${alertId}/work-order`, { method: 'POST', body: JSON.stringify(payload) }),
  updateWorkOrder: (id: number, payload: WorkOrderUpdate) =>
    request<WorkOrder>(`/work-orders/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  assignWorkOrder: (id: number, assignedTo: number, note?: string) =>
    request<WorkOrder>(`/work-orders/${id}/assign`, {
      method: 'POST',
      body: JSON.stringify(note ? { assigned_to: assignedTo, note } : { assigned_to: assignedTo }),
    }),
  startWorkOrder: (id: number) => request<WorkOrder>(`/work-orders/${id}/start`, { method: 'POST' }),
  completeWorkOrder: (id: number, payload: WorkOrderComplete = {}) =>
    request<WorkOrder>(`/work-orders/${id}/complete`, { method: 'POST', body: JSON.stringify(payload) }),
  cancelWorkOrder: (id: number, reason?: string) =>
    request<WorkOrder>(`/work-orders/${id}/cancel`, {
      method: 'POST',
      body: JSON.stringify(reason ? { reason } : {}),
    }),

  listUsers: () => request<UserOut[]>('/users'),
  createUser: (payload: UserCreate) =>
    request<UserOut>('/users', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: UserUpdate) =>
    request<UserOut>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  listNotifications: (status?: string, channel?: NotificationChannel) => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (channel) params.set('channel', channel)
    const qs = params.toString()
    return request<NotificationOut[]>(`/notifications${qs ? `?${qs}` : ''}`)
  },

  // Web Push (design/2026-10-07-mobile-operator-pwa-design.md). Every role;
  // each call only ever touches the caller's own subscriptions. The body of
  // subscribePush is exactly PushSubscription.toJSON().
  getPushConfig: () => request<PushConfig>('/push/vapid-public-key'),
  subscribePush: (subscription: PushSubscriptionJSON) =>
    request<PushSubscriptionOut>('/push/subscribe', { method: 'POST', body: JSON.stringify(subscription) }),
  unsubscribePush: (endpoint: string) =>
    request<void>('/push/subscribe', { method: 'DELETE', body: JSON.stringify({ endpoint }) }),
  sendTestPush: () => request<PushTestResult>('/push/test', { method: 'POST' }),

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
  getFieldAccuracy: (opts: { modelVersion?: string } = {}) =>
    request<FieldAccuracy>(
      `/model/feedback-accuracy${opts.modelVersion ? `?model_version=${encodeURIComponent(opts.modelVersion)}` : ''}`,
    ),
  // The retraining CSV (admin/supervisor). Raw like downloadReport, plus the
  // number of failure episodes it holds (X-Episode-Count).
  downloadFeedbackExport: async (): Promise<{ filename: string; content: string; episodeCount: number }> => {
    const res = await fetch(`${BASE}/model/feedback/export`, { credentials: 'same-origin' })
    await throwIfError(res)
    const disposition = res.headers.get('Content-Disposition') ?? ''
    const match = /filename="?([^"]+)"?/.exec(disposition)
    const filename = match ? match[1] : 'maintainiq-feedback-features.csv'
    const episodeCount = Number(res.headers.get('X-Episode-Count') ?? 0) || 0
    const content = await res.text()
    return { filename, content, episodeCount }
  },

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

  // Live MQTT telemetry (design/M6_LIVE_TELEMETRY.md §8).
  getTelemetryStatus: () => request<TelemetryStatus>('/telemetry/status'),
  getTelemetryDevices: (opts: { machineId?: string } = {}) =>
    request<TelemetryDevice[]>(
      `/telemetry/devices${opts.machineId ? `?machine_id=${encodeURIComponent(opts.machineId)}` : ''}`,
    ),
  getTelemetryDevice: (id: string) => request<DeviceDetail>(`/telemetry/devices/${encodeURIComponent(id)}`),
  // Device-health incidents (design/2026-10-06-device-health-design.md).
  getDeviceIncidents: (opts: { status?: DeviceIncidentStatus; deviceId?: string; limit?: number } = {}) => {
    const params = new URLSearchParams()
    if (opts.status) params.set('status', opts.status)
    if (opts.deviceId) params.set('device_id', opts.deviceId)
    if (opts.limit != null) params.set('limit', String(opts.limit))
    const qs = params.toString()
    return request<DeviceIncident[]>(`/telemetry/incidents${qs ? `?${qs}` : ''}`)
  },
  acknowledgeDeviceIncident: (id: number) =>
    request<DeviceIncident>(`/telemetry/incidents/${id}/acknowledge`, { method: 'POST' }),

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
