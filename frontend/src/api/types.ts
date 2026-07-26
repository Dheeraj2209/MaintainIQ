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
  temperature_severity: Severity
  risk_score: number
  abnormal_event_count: number
  open_alert_count: number
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
}

export interface MaintenanceRecord {
  id: number
  machine_id: string
  performed_at: string
  description: string | null
  technician: string | null
  created_at: string
}

export interface MaintenanceCreate {
  machine_id: string
  performed_at: string
  description?: string | null
  technician?: string | null
}

export interface MaintenanceSummary {
  machine_id: string
  last_maintenance_at: string | null
  days_since_last_maintenance: number | null
  completed_maintenance_count: number
  unresolved_alert_count: number
  avg_alert_resolution_hours: number | null
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
