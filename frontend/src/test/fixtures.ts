// Fixture payloads mirroring the real API response shapes (see
// src/api/schemas.py). Kept in one place so component tests and MSW handlers
// agree on the contract the backend guarantees (locked by tests/test_api.py).
import type {
  Alert,
  AlertExplanation,
  AlertFeedback,
  Assignee,
  DeviceDetail,
  DeviceIncident,
  DeviceWatchdogStatus,
  ExplanationReading,
  FieldAccuracy,
  KpiDetail,
  KpiSummary,
  MachineDetail,
  MachineSummary,
  MaintenanceRecord,
  ModelHealth,
  ModelTelemetry,
  NotificationOut,
  ProbableCauseExplanation,
  PushConfig,
  PushSubscriptionOut,
  ReplayStatus,
  Report,
  SystemKpis,
  TelemetryDevice,
  TelemetryStatus,
  TrendPoint,
  TriggeringReadings,
  UserOut,
  WorkOrder,
  WorkOrderDetail,
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

// Health ratchet (plan Task 12): a machine held at critical while its
// current signal has receded, and one relearning its baseline after a reset.
export const heldMachine: MachineSummary = {
  ...machineSummaries[0],
  instant_health_state: 'healthy',
  health_state_held: true,
  held_since: '2003-10-22T12:00:00+00:00',
  commissioning: null,
  reset_pending_reading: false,
  health_warnings: ['condition_receded: instant state healthy; holding critical until maintenance resets health tracking'],
}

export const commissioningMachine: MachineSummary = {
  ...machineSummaries[1],
  instant_health_state: 'faulty',
  health_state_held: true,
  held_since: null,
  commissioning: { seen: 7, of: 20 },
  reset_pending_reading: false,
  health_warnings: ['commissioning: 7/20 snapshots; learning the baseline, instant state faulty is not held'],
}

export const kpiSummary: KpiSummary = {
  machine_count: 2,
  health_state_counts: { critical: 1, healthy: 1 },
  open_alert_count: 1,
  machines_due_for_inspection: 2,
  open_work_order_count: 1,
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
    page_level: 0,
    last_paged_at: null,
    active_work_order_id: null,
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
    open_work_order_count: 1,
    avg_work_order_completion_hours: 2.5,
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
    channel: 'email',
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
    channel: 'email',
  },
  {
    id: 3,
    alert_id: 2,
    recipient_email: 'operator@maintainiq.local',
    recipient_role: 'operator',
    subject: 'MaintainIQ CRITICAL: m1 needs attention',
    body: 'critical · probable cause bearing_wear',
    status: 'sent',
    created_at: '2003-10-22T13:00:06+00:00',
    channel: 'push',
  },
]

// Web Push (design/2026-10-07-mobile-operator-pwa-design.md). The key is a
// real-shaped (87-char, 65-byte) uncompressed P-256 point, so
// urlBase64ToUint8Array decodes it like a server key.
export const pushConfig: PushConfig = {
  enabled: true,
  public_key: 'BElwBC3hQ1wq5UvXpYvJxVaxF8m9sZp4p4gP3cX6wQ0lXN0ZcJ0wLoN9vOq3Q8e8k3HjPDs0kqzE5hVcR8m0G2Y',
}

export const pushConfigDisabled: PushConfig = { enabled: false, public_key: null }

export const pushSubscription: PushSubscriptionOut = {
  id: 3,
  endpoint: 'https://fcm.googleapis.com/fcm/send/test-device',
  is_active: true,
  created_at: '2026-10-07T09:00:00+00:00',
  updated_at: '2026-10-07T09:00:00+00:00',
  last_used_at: null,
}

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

export const modelHealth: ModelHealth = {
  status: 'healthy',
  model_version: 'xjtu_rul_v1',
  last_inference_at: '2003-10-22T13:00:00+00:00',
  seconds_since_last_inference: 12.5,
  active: true,
}

export const modelTelemetry: ModelTelemetry = {
  window_minutes: 60,
  inference_count: 128,
  error_rate: 0.0,
  latency_p50_ms: 8.4,
  latency_p95_ms: 21.7,
  ood_rate: 0.05,
  warming_up_rate: 0.1,
}

export const replayStatus: ReplayStatus = {
  m1: { cycle: 3, running: true, last_ts: '2003-10-22T13:00:00+00:00', replayed: 1500, error: null },
  m2: { cycle: null, running: false, last_ts: null, replayed: 0, error: null },
}

export const reports: Report[] = [
  {
    id: 2,
    report_type: 'fleet_summary',
    scope: 'fleet',
    format: 'json',
    period_start: null,
    period_end: null,
    generated_at: '2026-08-06T10:00:00+00:00',
    generated_by: 1,
    summary: { machine_count: 2 },
  },
  {
    id: 1,
    report_type: 'machine_prognostic',
    scope: 'm1',
    format: 'markdown',
    period_start: null,
    period_end: null,
    generated_at: '2026-08-06T09:00:00+00:00',
    generated_by: 1,
    summary: { report_type: 'machine_prognostic' },
  },
]

export const reportDetail: Report = {
  ...reports[1],
  content: '# Machine Prognostic Report\n\nPredicted RUL: 42 minutes',
}

// ---- Live MQTT telemetry (design/M6_LIVE_TELEMETRY.md §8–§9) ----
// Silence alerting running and armed (design/2026-10-06-device-health-design.md).
export const deviceWatchdogArmed: DeviceWatchdogStatus = {
  enabled: true,
  interval_s: 5,
  grace_s: 30,
  armed: true,
  last_tick_at: '2026-10-06T11:59:55Z',
}

// MAINTAINIQ_SWEEP_INTERVAL_S unset: no scheduler, so no watchdog.
export const deviceWatchdogDisabled: DeviceWatchdogStatus = {
  enabled: false,
  interval_s: null,
  grace_s: 30,
  armed: false,
  last_tick_at: null,
}

export const telemetryStatusEnabled: TelemetryStatus = {
  enabled: true,
  connected: true,
  broker: 'mosquitto:1883',
  topic_prefix: 'maintainiq/v1',
  started_at: '2026-10-06T11:00:00Z',
  last_message_at: '2026-10-06T11:59:58Z',
  stats: {
    received: 412,
    accepted: 405,
    rejected: 3,
    duplicates: 4,
    errors: 0,
    queue_overflows: 0,
    queue_depth: 1,
    reconnects: 2,
  },
  device_watchdog: deviceWatchdogArmed,
}

export const telemetryStatusDisabled: TelemetryStatus = {
  enabled: false,
  connected: false,
  broker: null,
  topic_prefix: null,
  started_at: null,
  last_message_at: null,
  stats: {
    received: 0,
    accepted: 0,
    rejected: 0,
    duplicates: 0,
    errors: 0,
    queue_overflows: 0,
    queue_depth: 0,
    reconnects: 0,
  },
  device_watchdog: deviceWatchdogDisabled,
}

export const telemetryDevices: TelemetryDevice[] = [
  {
    device_id: 'simdev-01',
    machine_id: 'sim-01',
    online: true,
    last_seen_at: '2026-10-06T11:59:58Z',
    firmware: 'maintainiq-sim/0.1.0',
    uptime_s: 3600,
    buffer_depth: 0,
    buffer_capacity: 50,
    buffer_dropped_total: 0,
    publish_attempts_total: 1800,
    publish_failures_total: 3,
    wifi_rssi_dbm: -61,
    snapshot_interval_s: 2.0,
    heartbeat_interval_s: 10.0,
    state: 'online',
    silent_for_s: 2.0,
    expected_heartbeat_s: 10.0,
    open_incident_id: null,
  },
  {
    device_id: 'simdev-02',
    machine_id: 'sim-02',
    online: false,
    last_seen_at: '2026-10-06T11:50:00Z',
    firmware: 'maintainiq-sim/0.1.0',
    uptime_s: 1200,
    buffer_depth: 47,
    buffer_capacity: 50,
    buffer_dropped_total: 6,
    publish_attempts_total: 600,
    publish_failures_total: 40,
    wifi_rssi_dbm: -82,
    snapshot_interval_s: 2.0,
    heartbeat_interval_s: 10.0,
    state: 'offline',
    silent_for_s: 600.0,
    expected_heartbeat_s: 10.0,
    open_incident_id: 1,
  },
]

// GET /api/telemetry/incidents: simdev-02's current silence (open, not yet
// acknowledged) and an earlier, resolved and acknowledged LWT episode.
export const deviceIncidents: DeviceIncident[] = [
  {
    id: 1,
    device_id: 'simdev-02',
    machine_id: 'sim-02',
    kind: 'silent',
    status: 'open',
    opened_at: '2026-10-06T11:50:30Z',
    last_seen_at: '2026-10-06T11:50:00Z',
    resolved_at: null,
    acknowledged_at: null,
    acknowledged_by: null,
  },
  {
    id: 2,
    device_id: 'simdev-01',
    machine_id: 'sim-01',
    kind: 'lwt',
    status: 'resolved',
    opened_at: '2026-10-06T09:00:00Z',
    last_seen_at: '2026-10-06T08:59:50Z',
    resolved_at: '2026-10-06T09:04:00Z',
    acknowledged_at: '2026-10-06T09:02:00Z',
    acknowledged_by: 2,
  },
]

export function deviceDetailFor(deviceId: string): DeviceDetail {
  const device = telemetryDevices.find((d) => d.device_id === deviceId) ?? telemetryDevices[0]
  return {
    ...structuredClone(device),
    recent_incidents: structuredClone(deviceIncidents.filter((i) => i.device_id === device.device_id)),
  }
}

const NO_TELEMETRY_REASON =
  'no live telemetry received yet — start the MQTT simulator (just simulate) or connect an ESP32 node'

export const systemKpisNotApplicable: SystemKpis = {
  sensor_collection_rate: { status: 'not_applicable', reason: NO_TELEMETRY_REASON },
  transmission_success_rate: { status: 'not_applicable', reason: NO_TELEMETRY_REASON },
  edge_buffer_health: { status: 'not_applicable', reason: NO_TELEMETRY_REASON },
  cloud_sync_health: { status: 'not_applicable', reason: NO_TELEMETRY_REASON },
}

export const systemKpisAvailable: SystemKpis = {
  sensor_collection_rate: {
    status: 'available',
    window_minutes: 60,
    rate: 0.973,
    devices: [
      { device_id: 'simdev-01', collected: 1752, expected: 1800, rate: 0.973 },
      { device_id: 'simdev-02', collected: 560, expected: 600, rate: 0.933 },
    ],
  },
  transmission_success_rate: {
    status: 'available',
    window_minutes: 60,
    rate: 0.9,
    received: 900,
    expected: 1000,
    lost: 100,
    device_reported_failure_rate: 0.0179,
    devices: [],
  },
  edge_buffer_health: {
    status: 'available',
    window_minutes: 60,
    state: 'critical',
    buffered_share: 0.125,
    dropped_total: 6,
    devices: [
      { device_id: 'simdev-01', depth: 0, capacity: 50, utilization: 0, dropped_total: 0, state: 'ok' },
      { device_id: 'simdev-02', depth: 47, capacity: 50, utilization: 0.94, dropped_total: 6, state: 'critical' },
    ],
  },
  cloud_sync_health: {
    status: 'available',
    window_minutes: 60,
    state: 'degraded',
    lag_p50_s: 0.42,
    lag_p95_s: 1.8,
    devices_online: 1,
    devices_total: 2,
    last_message_age_s: 2.0,
    rejected_rate: 0.007,
  },
}

export function kpiDetailWith(system: SystemKpis): KpiDetail {
  return { machine_health: [], maintenance: [], prediction: { status: 'not_applicable' }, operational: {}, system }
}

// ---- Work orders (design/2026-10-07-work-orders-escalation-design.md) ----
// One order per status the UI cares about. #2 is assigned to the operator
// fixture user, so the operator "Mine" and Start paths have something to act on.
export const workOrders: WorkOrder[] = [
  {
    id: 1,
    alert_id: 1,
    machine_id: 'm1',
    status: 'open',
    priority: 'high',
    title: 'm1 critical',
    description: null,
    assigned_to: null,
    assigned_to_name: null,
    created_by: 2,
    created_by_name: 'Sam Supervisor',
    due_at: null,
    created_at: '2026-10-07T09:00:00+00:00',
    updated_at: '2026-10-07T09:00:00+00:00',
    started_at: null,
    completed_at: null,
    cancelled_at: null,
    maintenance_record_id: null,
    notes: null,
  },
  {
    id: 2,
    alert_id: null,
    machine_id: 'm2',
    status: 'assigned',
    priority: 'medium',
    title: 'Inspect m2 coupling',
    description: 'Routine check after re-alignment',
    assigned_to: 3,
    assigned_to_name: 'Ollie Operator',
    created_by: 1,
    created_by_name: 'Ada Admin',
    due_at: '2026-10-09T17:00:00+00:00',
    created_at: '2026-10-07T08:00:00+00:00',
    updated_at: '2026-10-07T08:30:00+00:00',
    started_at: null,
    completed_at: null,
    cancelled_at: null,
    maintenance_record_id: null,
    notes: null,
  },
  {
    id: 3,
    alert_id: 2,
    machine_id: 'm1',
    status: 'in_progress',
    priority: 'high',
    title: 'Replace m1 bearing',
    description: null,
    assigned_to: 2,
    assigned_to_name: 'Sam Supervisor',
    created_by: 1,
    created_by_name: 'Ada Admin',
    due_at: null,
    created_at: '2026-10-07T07:00:00+00:00',
    updated_at: '2026-10-07T07:20:00+00:00',
    started_at: '2026-10-07T07:20:00+00:00',
    completed_at: null,
    cancelled_at: null,
    maintenance_record_id: null,
    notes: null,
  },
  {
    id: 4,
    alert_id: null,
    machine_id: 'm2',
    status: 'done',
    priority: 'low',
    title: 'Grease m2 bearings',
    description: null,
    assigned_to: 3,
    assigned_to_name: 'Ollie Operator',
    created_by: 2,
    created_by_name: 'Sam Supervisor',
    due_at: null,
    created_at: '2026-10-06T07:00:00+00:00',
    updated_at: '2026-10-06T10:00:00+00:00',
    started_at: '2026-10-06T08:00:00+00:00',
    completed_at: '2026-10-06T10:00:00+00:00',
    cancelled_at: null,
    maintenance_record_id: 12,
    notes: 'Greased and run-tested',
  },
]

// GET /api/work-orders/3: the in-progress order with its three-event trail.
export const workOrderDetail: WorkOrderDetail = {
  ...workOrders[2],
  events: [
    {
      id: 1,
      work_order_id: 3,
      event: 'created',
      from_status: null,
      to_status: 'open',
      user_id: 1,
      user_name: 'Ada Admin',
      assigned_to: null,
      assigned_to_name: null,
      note: 'from alert #2',
      created_at: '2026-10-07T07:00:00+00:00',
    },
    {
      id: 2,
      work_order_id: 3,
      event: 'assigned',
      from_status: 'open',
      to_status: 'assigned',
      user_id: 1,
      user_name: 'Ada Admin',
      assigned_to: 2,
      assigned_to_name: 'Sam Supervisor',
      note: null,
      created_at: '2026-10-07T07:10:00+00:00',
    },
    {
      id: 3,
      work_order_id: 3,
      event: 'started',
      from_status: 'assigned',
      to_status: 'in_progress',
      user_id: 2,
      user_name: 'Sam Supervisor',
      assigned_to: null,
      assigned_to_name: null,
      note: null,
      created_at: '2026-10-07T07:20:00+00:00',
    },
  ],
  alert: openAlerts[0],
}

// A detail for any list entry, for tests that need a specific status or
// assignee in the drawer (the default handler always serves #3's trail).
export function workOrderDetailFor(order: WorkOrder): WorkOrderDetail {
  return { ...structuredClone(workOrderDetail), ...structuredClone(order), alert: null }
}

export const assignees: Assignee[] = [
  { id: 1, name: 'Ada Admin', role: 'admin' },
  { id: 2, name: 'Sam Supervisor', role: 'supervisor' },
  { id: 3, name: 'Ollie Operator', role: 'operator' },
]

// ---- Prediction feedback (design/2026-10-07-prediction-feedback-design.md) ----
// Recorded by the supervisor fixture user, so an operator may not edit it.
export const alertFeedback: AlertFeedback = {
  id: 3,
  alert_id: 5,
  outcome: 'maintenance_prevented',
  actual_cause: 'bearing_wear',
  actual_failure_at: null,
  notes: 'Outer race pitting found and replaced',
  work_order_id: null,
  recorded_by: 2,
  recorded_by_name: 'Sam Supervisor',
  recorded_at: '2026-10-07T11:00:00+00:00',
  updated_by: null,
  updated_at: null,
}

// A closed alert with its outcome, as the list and machine-detail routes return it.
export const resolvedAlertWithFeedback: Alert = {
  id: 5,
  machine_id: 'm1',
  opened_at: '2026-10-07T09:00:00+00:00',
  resolved_at: '2026-10-07T11:00:00+00:00',
  severity: 'medium',
  health_state: 'faulty',
  probable_cause: 'bearing_wear',
  message: 'm1 faulty',
  status: 'resolved',
  source: 'ml',
  acknowledged_at: '2026-10-07T11:00:00+00:00',
  acknowledged_by: 2,
  page_level: 0,
  last_paged_at: null,
  active_work_order_id: null,
  prediction_id: 41,
  reading_id: 900,
  model_version: 'xjtu_rul_20260930',
  closed_by: 2,
  feedback: alertFeedback,
}

// GET /api/model/feedback-accuracy with labels: two model versions (the null
// one is pre-link alerts) and an offline benchmark to compare against.
export const fieldAccuracy: FieldAccuracy = {
  status: 'available',
  reason: null,
  model_version: null,
  period: { start: null, end: null },
  horizon_minutes: 120,
  feedback_count: 7,
  labelled_count: 6,
  outcome_counts: { confirmed_failure: 2, maintenance_prevented: 2, false_alarm: 2, unknown: 1 },
  precision: 0.6667,
  false_alarm_rate: 0.3333,
  lead_time: {
    count: 2,
    mean_minutes: 95.0,
    median_minutes: 95.0,
    min_minutes: 70.0,
    max_minutes: 120.0,
    within_horizon_count: 2,
    early_count: 0,
    late_count: 0,
  },
  rul_error: {
    point_estimate_count: 1,
    mae_minutes: 12.5,
    median_abs_error_minutes: 12.5,
    lower_bound_count: 1,
    lower_bound_respected_count: 1,
  },
  root_cause: { labelled_count: 4, correct_count: 3, accuracy: 0.75 },
  missed_failures: { status: 'not_applicable', reason: 'missed failures are not observable from alert feedback' },
  by_model_version: [
    {
      model_version: 'xjtu_rul_20260930',
      feedback_count: 6,
      labelled_count: 5,
      precision: 0.6,
      false_alarm_rate: 0.4,
      median_lead_minutes: 95.0,
      rul_mae_minutes: 12.5,
      root_cause_accuracy: 0.75,
    },
    {
      model_version: null,
      feedback_count: 1,
      labelled_count: 1,
      precision: 1.0,
      false_alarm_rate: 0.0,
      median_lead_minutes: null,
      rul_mae_minutes: null,
      root_cause_accuracy: null,
    },
  ],
  offline: {
    model_version: 'xjtu_rul_20260930',
    precision: 0.71,
    recall: 0.83,
    false_alarm_count: 400,
    missed_failure_window_count: 120,
  },
  exportable_episode_count: 2,
}

export const fieldAccuracyNotApplicable: FieldAccuracy = {
  ...fieldAccuracy,
  status: 'not_applicable',
  reason: 'no labelled alerts yet',
  feedback_count: 0,
  labelled_count: 0,
  outcome_counts: { confirmed_failure: 0, maintenance_prevented: 0, false_alarm: 0, unknown: 0 },
  precision: null,
  false_alarm_rate: null,
  lead_time: {
    count: 0,
    mean_minutes: null,
    median_minutes: null,
    min_minutes: null,
    max_minutes: null,
    within_horizon_count: 0,
    early_count: 0,
    late_count: 0,
  },
  rul_error: {
    point_estimate_count: 0,
    mae_minutes: null,
    median_abs_error_minutes: null,
    lower_bound_count: 0,
    lower_bound_respected_count: 0,
  },
  root_cause: { labelled_count: 0, correct_count: 0, accuracy: null },
  by_model_version: [],
  offline: null,
  exportable_episode_count: 0,
}

// GET /api/alerts/:id/explanation ("Why this alert?",
// design/2026-10-07-alert-explanation-design.md). A captured snapshot of
// open alert 2 on m1: an escalation snapshot, an out-of-distribution
// prediction, one key factor outside the training range and one similar past
// incident (alert 1) whose outcome, work order and maintenance are known.
function explanationReading(i: number, isTrigger = false): ExplanationReading {
  return {
    reading_id: 9177 + i,
    timestamp: `2003-10-22T12:5${i}:00+00:00`,
    cycle: 407 + i,
    is_trigger: isTrigger,
    vibration_h_rms: 0.4 + i * 0.1,
    vibration_h_kurtosis: isTrigger ? 9.4 : 3 + i * 0.2,
    vibration_v_rms: 0.5 - i * 0.02,
    vibration_v_kurtosis: 3.1,
    cross_axis_rms_ratio: 1.1,
    cross_axis_correlation: 0.2,
  }
}

const explanationTriggering: TriggeringReadings = {
  locate: 'reading_id',
  trigger_reading_id: 9182,
  trigger_timestamp: '2003-10-22T12:55:00+00:00',
  channels: [
    { key: 'vibration_h_rms', label: 'Horizontal RMS', unit: 'g' },
    { key: 'vibration_h_kurtosis', label: 'Horizontal kurtosis', unit: null },
    { key: 'vibration_v_rms', label: 'Vertical RMS', unit: 'g' },
    { key: 'vibration_v_kurtosis', label: 'Vertical kurtosis', unit: null },
    { key: 'cross_axis_rms_ratio', label: 'Cross-axis RMS ratio (h/v)', unit: null },
    { key: 'cross_axis_correlation', label: 'Cross-axis correlation', unit: null },
  ],
  readings: [0, 1, 2, 3, 4].map((i) => explanationReading(i)).concat(explanationReading(5, true)),
}

const explanationCause: ProbableCauseExplanation = {
  label: 'bearing_wear',
  display: 'Probable cause: bearing wear',
  disclaimer: 'Heuristic rule on vibration features — a probable cause, not a diagnosis. Confirm on inspection.',
  rule: 'high_kurtosis',
  summary: 'Horizontal kurtosis 9.4 >= 5, so probable bearing wear.',
  checks: [{ feature: 'vibration_h_kurtosis', value: 9.4, operator: '>=', threshold: 5, passed: true }],
  evaluated_label: 'bearing_wear',
  matches_alert: true,
  inputs_source: 'snapshot_features',
}

export const alertExplanation: AlertExplanation = {
  // A copy: the acknowledge handler mutates openAlerts in place.
  alert: { ...openAlerts[0] },
  explanation_version: 1,
  source: 'snapshot',
  snapshot_kind: 'escalated',
  snapshot_at: '2003-10-22T13:00:03+00:00',
  snapshots: [
    { kind: 'created', created_at: '2003-10-22T12:40:11+00:00' },
    { kind: 'escalated', created_at: '2003-10-22T13:00:03+00:00' },
  ],
  generated_at: '2003-10-22T13:00:03+00:00',
  synthetic: false,
  triggering_readings: explanationTriggering,
  key_factors: {
    status: 'ok',
    method: 'model_context',
    weighting: 'model_importance',
    baseline_readings: 20,
    factors: [
      {
        feature: 'h_kurtosis',
        label: 'Horizontal kurtosis',
        axis: 'horizontal',
        unit: null,
        value: 9.4,
        baseline: 3.0,
        ratio: 3.13,
        direction: 'up',
        deviation: 1.14,
        importance: 0.21,
        score: 0.24,
        outside_training_bounds: true,
        out_of_bounds_features: ['h_kurtosis_baseline_ratio'],
      },
      {
        feature: 'v_rms',
        label: 'Vertical RMS vibration',
        axis: 'vertical',
        unit: 'g',
        value: 0.25,
        baseline: 0.5,
        ratio: 0.5,
        direction: 'down',
        deviation: 0.69,
        importance: 0.12,
        score: 0.08,
        outside_training_bounds: false,
        out_of_bounds_features: [],
      },
    ],
    operating_conditions: [
      { feature: 'speed_rpm', label: 'Shaft speed', unit: 'rpm', value: 2100, outside_training_bounds: false },
      { feature: 'load_kn', label: 'Radial load', unit: 'kN', value: 12, outside_training_bounds: false },
    ],
  },
  prediction: {
    locate: 'snapshot',
    prediction_id: 77,
    timestamp: '2003-10-22T12:55:00+00:00',
    model_version: 'xjtu_rul_20260930',
    health_state: 'critical',
    failure_within_horizon_probability: 0.83,
    predicted_rul_minutes: 42,
    rul_estimate_kind: 'point_estimate',
    prediction_interval_low: 12,
    prediction_interval_high: 72,
    prognostic_horizon_minutes: 120,
    out_of_distribution: true,
    outside_training_features: ['h_kurtosis_baseline_ratio'],
    warnings: [
      'warning_persistence: probability held above threshold for 3 snapshots',
      'out_of_distribution: input differs materially from XJTU-SY; treat the estimate with caution',
    ],
  },
  probable_cause: explanationCause,
  similar_incidents: [
    {
      alert_id: 1,
      machine_id: 'm1',
      opened_at: '2003-10-20T09:00:00+00:00',
      resolved_at: '2003-10-20T15:00:00+00:00',
      status: 'resolved',
      severity: 'high',
      health_state: 'critical',
      probable_cause: 'bearing_wear',
      synthetic: false,
      similarity: 0.86,
      match_reasons: ['same_probable_cause', 'same_machine', 'same_severity'],
      feedback: {
        outcome: 'maintenance_prevented',
        actual_cause: 'bearing_wear',
        actual_failure_at: null,
        notes: 'Outer race pitting found and replaced',
      },
      cause_confirmed: true,
      work_orders: [{ id: 7, status: 'done', title: 'Replace bearing', completed_at: '2003-10-20T14:30:00+00:00' }],
      maintenance: [
        {
          id: 12,
          performed_at: '2003-10-20T14:30:00+00:00',
          type: 'corrective',
          description: 'Replaced drive-end bearing',
          technician: 'tech1',
        },
      ],
    },
  ],
  notes: [],
}

// A legacy alert with no snapshot: rebuilt from stored data, no prediction
// recorded and too little history to rank key factors.
export const alertExplanationReconstructed: AlertExplanation = {
  ...alertExplanation,
  source: 'reconstructed',
  snapshot_kind: null,
  snapshot_at: null,
  snapshots: [],
  generated_at: '2026-10-07T12:00:00+00:00',
  triggering_readings: {
    ...explanationTriggering,
    locate: 'nearest_timestamp',
    trigger_reading_id: 9182,
    readings: [explanationReading(4), explanationReading(5, true)],
  },
  key_factors: {
    status: 'insufficient_history',
    method: null,
    weighting: null,
    baseline_readings: 0,
    factors: [],
    operating_conditions: [],
  },
  prediction: null,
  probable_cause: { ...explanationCause, inputs_source: 'trigger_reading' },
  similar_incidents: [],
  notes: ['No snapshot was captured for this alert; the explanation was rebuilt from stored data.'],
}
