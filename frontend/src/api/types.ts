// TypeScript mirror of the Pydantic response models in src/api/schemas.py.
// The backend contract is locked by tests/test_api.py; these types keep the
// UI honest against it at compile time.

export type HealthState = 'healthy' | 'degrading' | 'faulty' | 'critical' | 'unknown'
export type Severity = 'low' | 'medium' | 'high' | 'unknown'

export interface MachineSummary {
  machine_id: string
  health_state: HealthState
  confidence: number | null
  prediction_source: string | null
  probable_cause: string | null
  last_reading_at: string | null
  vibration_severity: Severity
  risk_score: number
  abnormal_event_count: number
  open_alert_count: number
  predicted_rul_minutes: number | null
  rul_estimate_kind: string | null
  out_of_distribution: boolean | null
}

export interface TrendPoint {
  timestamp: string
  value: number | null
}

export interface Alert {
  id: number
  machine_id: string
  opened_at: string
  resolved_at: string | null
  severity: string
  health_state: string
  probable_cause: string | null
  message: string | null
  status: string
  source: string | null
  acknowledged_at: string | null
  acknowledged_by: number | null
}

export interface MaintenanceRecord {
  id: number
  machine_id: string
  performed_at: string
  description: string | null
  technician: string | null
  created_at: string
  alert_id?: number | null
  type?: 'preventive' | 'corrective' | null
}

export interface MaintenanceCreate {
  machine_id: string
  performed_at: string
  description?: string | null
  technician?: string | null
  alert_id?: number | null
  type?: 'preventive' | 'corrective' | null
}

export interface MaintenanceSummary {
  machine_id: string
  last_maintenance_at: string | null
  days_since_last_maintenance: number | null
  completed_maintenance_count: number
  unresolved_alert_count: number
  avg_alert_resolution_hours: number | null
  avg_alert_acknowledgement_hours: number | null
  due_for_inspection: boolean
}

export interface MachineDetail {
  health: MachineSummary
  maintenance: MaintenanceSummary
  alerts: Alert[]
  maintenance_history: MaintenanceRecord[]
}

export interface PredictionKpi {
  status: string
  winning_model?: string
  accuracy?: number
  false_alarm_count?: number
  missed_fault_count?: number
  mean_confidence?: number
  suggested_confidence_threshold?: number
  [key: string]: unknown
}

export interface KpiSummary {
  machine_count: number
  health_state_counts: Partial<Record<HealthState, number>>
  open_alert_count: number
  machines_due_for_inspection: number
  prediction: PredictionKpi
}

export type Role = 'admin' | 'supervisor' | 'operator'

export interface UserOut {
  id: number
  email: string
  name: string
  role: Role
  is_active: boolean
  created_at: string
}

export interface UserCreate {
  email: string
  name: string
  password: string
  role: Role
}

export interface UserUpdate {
  name?: string
  role?: Role
  is_active?: boolean
  password?: string
}

export interface NotificationOut {
  id: number
  alert_id: number | null
  recipient_email: string
  recipient_role: string | null
  subject: string
  body: string
  status: string
  created_at: string
}

// Demand-triggered fault simulation (src/api/routes/demo.py) — the "no
// hardware needed" realtime demo. `severity` maps to health_state directly,
// not to the Alert.severity (low/medium/high) scale.
export type DemoSeverity = 'healthy' | 'degrading' | 'faulty' | 'critical'

export interface SimulateFaultRequest {
  machine_id: string
  severity: DemoSeverity
}

export interface SimulateFaultResponse {
  machine_id: string
  health_state: string
  probable_cause: string | null
  alert: Alert | null
  emails_sent: number
}

// Wire shape broadcast over /api/ws (src/realtime/manager.py).
export type LiveEventType = 'alert_created' | 'alert_escalated' | 'alert_resolved' | 'alert_acknowledged'

export interface LiveEvent {
  type: LiveEventType
  machine_id: string
  alert: Alert
  at: string
}

// ---- Model observability (src/api/routes/model.py) ----
export interface ModelHealth {
  status: 'healthy' | 'stale'
  model_version: string | null
  last_inference_at: string | null
  seconds_since_last_inference: number | null
  active: boolean
}

export interface ModelTelemetry {
  window_minutes: number
  inference_count: number
  error_rate: number
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  ood_rate: number
  warming_up_rate: number
}

// ---- Ingestion replay control (src/api/routes/ingestion.py) ----
export interface ReplayMachineStatus {
  cycle: number | null
  running: boolean
  last_ts: string | null
  replayed: number
  error: string | null
}

export type ReplayStatus = Record<string, ReplayMachineStatus>

export interface ReplayStartRequest {
  machine_id: string
  speed_multiplier?: number
}

export interface ReplayStartResponse {
  machine_id: string
  status: string
  speed_multiplier: number
}

export interface ReplayStopResponse {
  machine_id: string
  status: string
}

// ---- Reports (src/api/routes/reports.py) ----
export type ReportType = 'machine_prognostic' | 'model_performance' | 'fleet_summary'
export type ReportFormat = 'markdown' | 'json'

export interface ReportCreateRequest {
  report_type: ReportType
  scope: string
  format?: ReportFormat
  period_start?: string | null
  period_end?: string | null
}

// list responses omit `content` (see src/reports/service.py:list_reports);
// create/get responses include it.
export interface Report {
  id: number
  report_type: ReportType
  scope: string
  format: ReportFormat
  period_start: string | null
  period_end: string | null
  generated_at: string
  generated_by: number | null
  summary: Record<string, unknown> | null
  content?: string
}
