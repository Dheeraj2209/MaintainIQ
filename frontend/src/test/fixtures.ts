// Fixture payloads mirroring the real API response shapes (see
// src/api/schemas.py). Kept in one place so component tests and MSW handlers
// agree on the contract the backend guarantees (locked by tests/test_api.py).
import type {
  Alert,
  KpiSummary,
  MachineDetail,
  MachineSummary,
  TrendPoint,
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
    temperature_severity: 'high',
    risk_score: 100,
    abnormal_event_count: 2,
    open_alert_count: 1,
  },
  {
    machine_id: 'm2',
    health_state: 'healthy',
    confidence: null,
    prediction_source: 'rule_based',
    probable_cause: null,
    last_reading_at: '2003-10-22T13:00:00+00:00',
    vibration_severity: 'low',
    temperature_severity: 'low',
    risk_score: 0,
    abnormal_event_count: 0,
    open_alert_count: 0,
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
    due_for_inspection: true,
  },
  alerts: openAlerts,
  maintenance_history: [],
}
