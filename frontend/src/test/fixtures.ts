// Fixture payloads mirroring the real API response shapes (see
// src/api/schemas.py). Kept in one place so component tests and MSW handlers
// agree on the contract the backend guarantees (locked by tests/test_api.py).
import type {
  Alert,
  KpiSummary,
  MachineDetail,
  MachineSummary,
  MaintenanceRecord,
  NotificationOut,
  TrendPoint,
  UserOut,
} from '../api/types'

export const machineSummaries: MachineSummary[] = [
  {
    machine_id: 'm1',
    health_state: 'critical',
    confidence: 0.95,
    prediction_source: 'ml',
    probable_cause: 'bearing_wear',
    last_reading_at: '2003-10-22T13:00:00+00:00',
    vibration_severity: 'high',
    risk_score: 100,
    abnormal_event_count: 2,
    open_alert_count: 1,
    predicted_rul_minutes: 42.5,
    rul_estimate_kind: 'point_estimate',
    out_of_distribution: false,
  },
  {
    machine_id: 'm2',
    health_state: 'healthy',
    confidence: null,
    prediction_source: 'rule_based',
    probable_cause: null,
    last_reading_at: '2003-10-22T13:00:00+00:00',
    vibration_severity: 'low',
    risk_score: 0,
    abnormal_event_count: 0,
    open_alert_count: 0,
    predicted_rul_minutes: 1200.0,
    rul_estimate_kind: 'point_estimate',
    out_of_distribution: false,
  },
]

export const kpiSummary: KpiSummary = {
  machine_count: 2,
  health_state_counts: { critical: 1, healthy: 1 },
  open_alert_count: 1,
  machines_due_for_inspection: 2,
  prediction: {
    status: 'available',
    winning_model: 'logistic_regression',
    accuracy: 0.7959,
    false_alarm_count: 99,
    missed_fault_count: 273,
    mean_confidence: 0.9257,
    suggested_confidence_threshold: 0.985,
  },
}

export const openAlerts: Alert[] = [
  {
    id: 2,
    machine_id: 'm1',
    opened_at: '2003-10-22T13:00:00+00:00',
    resolved_at: null,
    severity: 'high',
    health_state: 'critical',
    probable_cause: 'bearing_wear',
    message: 'm1 critical',
    status: 'open',
    source: 'ml',
    acknowledged_at: null,
    acknowledged_by: null,
  },
]

export const trendPoints: TrendPoint[] = [
  { timestamp: '2003-10-22T12:00:00+00:00', value: 0.1 },
  { timestamp: '2003-10-22T13:00:00+00:00', value: 0.9 },
]

export const machineDetail: MachineDetail = {
  health: machineSummaries[0],
  maintenance: {
    machine_id: 'm1',
    last_maintenance_at: null,
    days_since_last_maintenance: null,
    completed_maintenance_count: 0,
    unresolved_alert_count: 1,
    avg_alert_resolution_hours: 0.08,
    avg_alert_acknowledgement_hours: null,
    due_for_inspection: true,
  },
  alerts: openAlerts,
  maintenance_history: [],
}

export const adminUser: UserOut = {
  id: 1,
  email: 'admin@maintainiq.local',
  name: 'Ada Admin',
  role: 'admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00+00:00',
}

export const supervisorUser: UserOut = {
  id: 2,
  email: 'supervisor@maintainiq.local',
  name: 'Sam Supervisor',
  role: 'supervisor',
  is_active: true,
  created_at: '2026-01-01T00:00:00+00:00',
}

export const operatorUser: UserOut = {
  id: 3,
  email: 'operator@maintainiq.local',
  name: 'Ollie Operator',
  role: 'operator',
  is_active: true,
  created_at: '2026-01-01T00:00:00+00:00',
}

export const users: UserOut[] = [adminUser, supervisorUser, operatorUser]

export const notifications: NotificationOut[] = [
  {
    id: 1,
    alert_id: 2,
    recipient_email: 'admin@maintainiq.local',
    recipient_role: 'admin',
    subject: 'MaintainIQ CRITICAL: m1 needs attention',
    body: 'Machine: m1\nHealth state: critical\n',
    status: 'sent',
    created_at: '2003-10-22T13:00:05+00:00',
  },
  {
    id: 2,
    alert_id: 2,
    recipient_email: 'supervisor@maintainiq.local',
    recipient_role: 'supervisor',
    subject: 'MaintainIQ CRITICAL: m1 needs attention',
    body: 'Machine: m1\nHealth state: critical\n',
    status: 'failed',
    created_at: '2003-10-22T13:00:05+00:00',
  },
]

export const maintenanceHistoryPage1: MaintenanceRecord[] = Array.from({ length: 10 }, (_, i): MaintenanceRecord => ({
  id: i + 1,
  machine_id: 'm1',
  performed_at: `2026-07-${String(i + 1).padStart(2, '0')}T10:00:00+00:00`,
  description: `service ${i + 1}`,
  technician: 'tech1',
  alert_id: null,
  type: i % 2 === 0 ? 'preventive' : 'corrective',
  created_at: '2026-07-01T00:00:00+00:00',
}))

export const maintenanceHistoryPage2: MaintenanceRecord[] = [
  {
    id: 11,
    machine_id: 'm1',
    performed_at: '2026-06-01T10:00:00+00:00',
    description: 'older service',
    technician: 'tech1',
    alert_id: null,
    type: 'preventive',
    created_at: '2026-06-01T00:00:00+00:00',
  },
]

export const machineDetailWithFullHistory: MachineDetail = {
  ...machineDetail,
  maintenance_history: maintenanceHistoryPage1,
}
