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
  // Health ratchet (docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md,
  // Task 12). Optional so older fixtures keep type-checking. health_state is
  // the held (ratcheted) level; instant_health_state the current reading's
  // own state; commissioning the baseline relearning after a reset.
  instant_health_state?: string | null
  health_state_held?: boolean
  held_since?: string | null
  commissioning?: Commissioning | null
  reset_pending_reading?: boolean
  // The latest prediction's commissioning:/condition_receded:/ood_not_latched: warnings.
  health_warnings?: string[]
}

// Baseline relearning after a health reset: `seen` of `of` readings.
export interface Commissioning {
  seen: number
  of: number
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
  // Paging ladder (design/2026-10-07-work-orders-escalation-design.md):
  // 0 = only the initial email; 1 = supervisors re-paged; 2 = admins paged.
  page_level: number
  last_paged_at: string | null
  // The open/assigned/in_progress work order raised from this alert. Set by
  // the list and machine-detail routes only; absent (or null) in broadcasts.
  active_work_order_id?: number | null
  // Prediction feedback (design/2026-10-07-prediction-feedback-design.md).
  // The prediction/reading that opened the alert and the model behind it
  // (written once, kept through escalation); who closed it by hand; and the
  // recorded outcome (list/detail/close responses only, absent in broadcasts).
  // Optional so alert literals elsewhere keep type-checking.
  prediction_id?: number | null
  reading_id?: number | null
  model_version?: string | null
  closed_by?: number | null
  feedback?: AlertFeedback | null
}

// ---- Prediction feedback (design/2026-10-07-prediction-feedback-design.md) ----
// What actually happened after an alert. `unknown` is stored but excluded
// from every accuracy number; maintenance_prevented counts as a true positive.
export type FeedbackOutcome = 'confirmed_failure' | 'maintenance_prevented' | 'false_alarm' | 'unknown'
// The rule-based root-cause labels plus `other`.
export type FeedbackCause = 'bearing_wear' | 'imbalance' | 'sensor_or_data_quality_issue' | 'unknown' | 'other'

export interface AlertFeedback {
  id: number
  alert_id: number
  outcome: FeedbackOutcome
  actual_cause: FeedbackCause | null
  // Only ever set with confirmed_failure (the server enforces it).
  actual_failure_at: string | null
  notes: string | null
  work_order_id: number | null
  recorded_by: number
  recorded_by_name: string | null
  recorded_at: string
  updated_by: number | null
  updated_at: string | null
}

// Body of POST /alerts/{id}/close and PUT /alerts/{id}/feedback.
export interface AlertFeedbackIn {
  outcome: FeedbackOutcome
  actual_cause?: FeedbackCause | null
  actual_failure_at?: string | null
  notes?: string | null
  work_order_id?: number | null
}

// `closed` is false when the alert was already resolved: the call then only
// recorded the outcome.
export interface AlertCloseResponse {
  alert: Alert
  feedback: AlertFeedback
  closed: boolean
}

// ---- "Why this alert?" (design/2026-10-07-alert-explanation-design.md) ----
// GET /alerts/{id}/explanation: the evidence snapshotted when the alert was
// created or escalated (or rebuilt from stored data for older alerts), plus
// similar past incidents computed at request time. Every section may be null
// (it degrades on its own); `notes` says why.

// One vibration reading of the trigger window. reading_id is null only for an
// unsaved trigger: the feature vector the model scored (locate
// 'snapshot_features'). Vibration channels only — never temperature.
export interface ExplanationReading {
  reading_id: number | null
  timestamp: string
  cycle: number | null
  is_trigger: boolean
  vibration_h_rms: number | null
  vibration_h_kurtosis: number | null
  vibration_v_rms: number | null
  vibration_v_kurtosis: number | null
  cross_axis_rms_ratio: number | null
  cross_axis_correlation: number | null
}

export type ExplanationChannelKey =
  | 'vibration_h_rms'
  | 'vibration_h_kurtosis'
  | 'vibration_v_rms'
  | 'vibration_v_kurtosis'
  | 'cross_axis_rms_ratio'
  | 'cross_axis_correlation'

export interface ExplanationChannel {
  key: string
  label: string
  unit: string | null
}

export type TriggerLocate = 'reading_id' | 'prediction_reading' | 'nearest_timestamp' | 'snapshot_features' | 'none'

export interface TriggeringReadings {
  locate: TriggerLocate
  trigger_reading_id: number | null
  trigger_timestamp: string | null
  channels: ExplanationChannel[]
  readings: ExplanationReading[]
}

export type FactorDirection = 'up' | 'down' | 'steady'

// A vibration feature ranked by deviation from the machine's own baseline,
// weighted by model importance. outside_training_bounds null = unknown.
export interface KeyFactor {
  feature: string
  label: string
  axis: 'horizontal' | 'vertical' | 'combined'
  unit: string | null
  value: number
  baseline: number
  ratio: number
  direction: FactorDirection
  deviation: number
  importance: number | null
  score: number
  outside_training_bounds: boolean | null
  out_of_bounds_features: string[]
}

// Shaft speed / radial load at the trigger: context, never ranked.
export interface OperatingCondition {
  feature: 'speed_rpm' | 'load_kn'
  label: string
  unit: string
  value: number | null
  outside_training_bounds: boolean | null
}

export interface KeyFactors {
  status: 'ok' | 'insufficient_history' | 'no_readings' | 'error'
  method: 'model_context' | 'promoted_columns' | null
  weighting: 'model_importance' | 'unweighted' | null
  baseline_readings: number
  factors: KeyFactor[]
  operating_conditions: OperatingCondition[]
}

export interface ExplanationPrediction {
  locate: 'snapshot' | 'prediction_id' | 'nearest_timestamp'
  prediction_id: number | null
  timestamp: string | null
  model_version: string | null
  health_state: string | null
  failure_within_horizon_probability: number | null
  predicted_rul_minutes: number | null
  rul_estimate_kind: string | null
  prediction_interval_low: number | null
  prediction_interval_high: number | null
  prognostic_horizon_minutes: number | null
  out_of_distribution: boolean
  outside_training_features: string[]
  warnings: string[]
  // Health ratchet (plan Task 12); absent from a predictor without it.
  instant_health_state?: string | null
  health_state_held?: boolean | null
  health_ratchet?: boolean | null
  commissioning?: Commissioning | null
  health_epoch?: number | null
  health_episode?: number | null
}

export interface RuleCheck {
  feature: string
  value: number | null
  operator: string
  threshold: number | null
  passed: boolean
}

// Always worded as a *probable* cause: `display` is "Probable cause: …".
export interface ProbableCauseExplanation {
  label: string | null
  display: string
  disclaimer: string
  rule: string | null
  summary: string | null
  checks: RuleCheck[]
  evaluated_label: string | null
  matches_alert: boolean | null
  inputs_source: 'snapshot_features' | 'trigger_reading' | null
}

export interface IncidentFeedback {
  outcome: string
  actual_cause: string | null
  actual_failure_at: string | null
  notes: string | null
}

export interface IncidentWorkOrder {
  id: number
  status: string
  title: string
  completed_at: string | null
}

export interface IncidentMaintenance {
  id: number
  performed_at: string
  type: string | null
  description: string | null
  technician: string | null
}

export type SimilarityReason = 'same_probable_cause' | 'actual_cause_matches' | 'same_machine' | 'same_severity'

// An earlier alert like this one, with what actually happened to it.
export interface SimilarIncident {
  alert_id: number
  machine_id: string
  opened_at: string
  resolved_at: string | null
  status: string
  severity: string
  health_state: string
  probable_cause: string | null
  synthetic: boolean
  similarity: number
  match_reasons: string[]
  feedback: IncidentFeedback | null
  cause_confirmed: boolean | null
  work_orders: IncidentWorkOrder[]
  maintenance: IncidentMaintenance[]
}

export type SnapshotKind = 'created' | 'escalated'

export interface SnapshotRef {
  kind: SnapshotKind
  created_at: string
}

export interface AlertExplanation {
  alert: Alert
  explanation_version: number
  source: 'snapshot' | 'reconstructed'
  snapshot_kind: SnapshotKind | null
  snapshot_at: string | null
  snapshots: SnapshotRef[]
  generated_at: string
  synthetic: boolean
  triggering_readings: TriggeringReadings | null
  key_factors: KeyFactors | null
  prediction: ExplanationPrediction | null
  probable_cause: ProbableCauseExplanation | null
  similar_incidents: SimilarIncident[]
  notes: string[]
}

export interface LeadTimeStats {
  count: number
  mean_minutes: number | null
  median_minutes: number | null
  min_minutes: number | null
  max_minutes: number | null
  within_horizon_count: number
  early_count: number
  late_count: number
}

// Point estimates only; censored lower bounds are counted separately.
export interface RulErrorStats {
  point_estimate_count: number
  mae_minutes: number | null
  median_abs_error_minutes: number | null
  lower_bound_count: number
  lower_bound_respected_count: number
}

export interface FieldAccuracyByVersion {
  model_version: string | null
  feedback_count: number
  labelled_count: number
  precision: number | null
  false_alarm_rate: number | null
  median_lead_minutes: number | null
  rul_mae_minutes: number | null
  root_cause_accuracy: number | null
}

// The offline benchmark from model_registry; often null (no metrics recorded).
export interface OfflineMetrics {
  model_version: string | null
  precision: number | null
  recall: number | null
  false_alarm_count: number | null
  missed_failure_window_count: number | null
}

// GET /api/model/feedback-accuracy. Every key is always present; when
// status is not_applicable the counts are zero and the rates null.
export interface FieldAccuracy {
  status: 'available' | 'not_applicable'
  reason: string | null
  model_version: string | null
  period: { start: string | null; end: string | null }
  horizon_minutes: number
  feedback_count: number
  labelled_count: number
  outcome_counts: Record<FeedbackOutcome, number>
  precision: number | null
  false_alarm_rate: number | null
  lead_time: LeadTimeStats
  rul_error: RulErrorStats
  root_cause: { labelled_count: number; correct_count: number; accuracy: number | null }
  missed_failures: { status: string; reason?: string | null }
  by_model_version: FieldAccuracyByVersion[]
  offline: OfflineMetrics | null
  exportable_episode_count: number
}

// The compact KPI `prediction.real_world` block.
export interface RealWorldKpi {
  status: 'available' | 'not_applicable'
  reason: string | null
  labelled_count: number
  precision: number | null
  false_alarm_rate: number | null
  median_lead_minutes: number | null
  rul_mae_minutes: number | null
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
  // The record restarted the machine's health tracking (a repair).
  resets_health?: boolean
}

export interface MaintenanceCreate {
  machine_id: string
  performed_at: string
  description?: string | null
  technician?: string | null
  alert_id?: number | null
  type?: 'preventive' | 'corrective' | null
  // Restart health tracking. Only true resets; admin/supervisor only.
  reset_health?: boolean | null
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
  open_work_order_count: number
  avg_work_order_completion_hours: number | null
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
  // Operator-feedback accuracy (design/2026-10-07-prediction-feedback-design.md).
  real_world?: RealWorldKpi
  root_cause_accuracy?: { status: string; accuracy?: number; labelled_count?: number; reason?: string }
  [key: string]: unknown
}

export interface KpiSummary {
  machine_count: number
  health_state_counts: Partial<Record<HealthState, number>>
  open_alert_count: number
  machines_due_for_inspection: number
  open_work_order_count: number
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
  // Set on device-silence pages (alert_id is null on those rows). Optional so
  // a pre-device-health backend still type-checks.
  device_incident_id?: number | null
  // Which channel the page went out on (design/2026-10-07-mobile-operator-pwa-design.md).
  // Optional so a pre-push backend still type-checks; absent means email.
  channel?: NotificationChannel
}

export type NotificationChannel = 'email' | 'push'

// ---- Web Push (design/2026-10-07-mobile-operator-pwa-design.md) ----
// GET /push/vapid-public-key. `enabled: false` (no VAPID keys on the server)
// comes with a null key.
export interface PushConfig {
  enabled: boolean
  public_key: string | null
}

// POST /push/subscribe: this device's row, bound to the signed-in user.
export interface PushSubscriptionOut {
  id: number
  endpoint: string
  is_active: boolean
  created_at: string
  updated_at: string
  last_used_at: string | null
}

// POST /push/test: one attempt per active subscription of the caller.
export interface PushTestResult {
  sent: number
  failed: number
  expired: number
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

// One RUL inference (src/api/schemas.py RULPredictionResponse). Returned by
// POST /predictions/rul and attached to the live events that prediction raises.
export interface RULPrediction {
  machine_id: string
  predicted_rul_minutes: number
  predicted_rul_hours: number
  rul_estimate_kind: string
  prognostic_horizon_minutes: number
  failure_within_horizon_probability: number
  raw_failure_within_horizon_probability: number
  warning_persistence_snapshots: number
  prediction_interval_90_minutes: (number | null)[]
  health_state: string
  model_version: string
  history_snapshots: number
  out_of_distribution: boolean
  outside_training_features: string[]
  warnings: string[]
}

// Wire shapes broadcast over /api/ws (src/realtime/manager.py). A
// discriminated union on `type`: alert events (and alert_paged) carry an
// `alert`, device-health events (design/2026-10-06-device-health-design.md)
// carry an `incident`, work-order events carry a `work_order`, and feedback
// events (alert_closed, alert_feedback_recorded) carry an `alert` and its
// `feedback`. Consumers must narrow on `type` (or isDeviceEvent /
// isWorkOrderEvent / isFeedbackEvent) before touching any.
export type AlertEventType = 'alert_created' | 'alert_escalated' | 'alert_resolved' | 'alert_acknowledged'

export interface AlertLiveEvent {
  type: AlertEventType
  machine_id: string
  alert: Alert
  at: string
  // Present when the event came from a model prediction
  // (src/prediction/pipeline.py); absent for demo-injected and
  // acknowledge/resolve events, which have no prediction behind them.
  prediction?: RULPrediction
}

export type DeviceEventType = 'device_offline' | 'device_online'

export interface DeviceLiveEvent {
  type: DeviceEventType
  device_id: string
  // null for a node not mapped to a machine.
  machine_id: string | null
  incident: DeviceIncident
  at: string
}

export type WorkOrderEventType = 'work_order_created' | 'work_order_updated'

// Present on work_order_updated only: which action changed the order.
export type WorkOrderChange = 'assigned' | 'started' | 'completed' | 'cancelled' | 'edited'

export interface WorkOrderLiveEvent {
  type: WorkOrderEventType
  machine_id: string
  work_order: WorkOrder
  change?: WorkOrderChange
  at: string
}

// An unacknowledged alert re-paged up the ladder (src/alerts/paging.py).
// Distinct from alert_escalated, which means a severity increase.
export interface AlertPagedLiveEvent {
  type: 'alert_paged'
  machine_id: string
  alert: Alert
  page_level: number
  at: string
}

// A person closed an alert or recorded/edited its outcome
// (design/2026-10-07-prediction-feedback-design.md). Realtime only: neither
// pages anyone.
export type AlertFeedbackEventType = 'alert_closed' | 'alert_feedback_recorded'

export interface AlertFeedbackLiveEvent {
  type: AlertFeedbackEventType
  machine_id: string
  alert: Alert
  feedback: AlertFeedback
  at: string
}

export type LiveEvent =
  | AlertLiveEvent
  | DeviceLiveEvent
  | WorkOrderLiveEvent
  | AlertPagedLiveEvent
  | AlertFeedbackLiveEvent
export type LiveEventType = LiveEvent['type']

// Every `type` this client understands. Frames with any other type (a newer
// server's events) are dropped by LiveEventsProvider rather than mis-handled.
export const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set<LiveEventType>([
  'alert_created',
  'alert_escalated',
  'alert_resolved',
  'alert_acknowledged',
  'device_offline',
  'device_online',
  'work_order_created',
  'work_order_updated',
  'alert_paged',
  'alert_closed',
  'alert_feedback_recorded',
])

export function isDeviceEvent(e: LiveEvent): e is DeviceLiveEvent {
  return e.type === 'device_offline' || e.type === 'device_online'
}

export function isWorkOrderEvent(e: LiveEvent): e is WorkOrderLiveEvent {
  return e.type === 'work_order_created' || e.type === 'work_order_updated'
}

export function isFeedbackEvent(e: LiveEvent): e is AlertFeedbackLiveEvent {
  return e.type === 'alert_closed' || e.type === 'alert_feedback_recorded'
}

// ---- Work orders (design/2026-10-07-work-orders-escalation-design.md) ----
// open → assigned → in_progress → done; cancel from any non-terminal state.
export type WorkOrderStatus = 'open' | 'assigned' | 'in_progress' | 'done' | 'cancelled'
export type WorkOrderPriority = 'low' | 'medium' | 'high'

export interface WorkOrder {
  id: number
  alert_id: number | null
  machine_id: string
  status: WorkOrderStatus
  priority: WorkOrderPriority
  title: string
  description: string | null
  assigned_to: number | null
  assigned_to_name: string | null
  created_by: number | null
  created_by_name: string | null
  due_at: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
  cancelled_at: string | null
  // The corrective/preventive maintenance_records row written on completion.
  maintenance_record_id: number | null
  notes: string | null
}

export type WorkOrderEventKind = 'created' | 'edited' | 'assigned' | 'started' | 'completed' | 'cancelled'

// One row of the append-only audit trail. A reassignment is an `assigned`
// event whose from_status equals to_status.
export interface WorkOrderEvent {
  id: number
  work_order_id: number
  event: WorkOrderEventKind
  from_status: string | null
  to_status: string | null
  user_id: number | null
  user_name: string | null
  assigned_to: number | null
  assigned_to_name: string | null
  note: string | null
  created_at: string
}

// GET /api/work-orders/{id}: events oldest first, plus the linked alert.
export interface WorkOrderDetail extends WorkOrder {
  events: WorkOrderEvent[]
  alert: Alert | null
  // Whether completing it as corrective work resets the machine's health by
  // default (plan D2: its alert is a real alert of the current episode).
  resets_health_by_default: boolean
}

export interface WorkOrderCreate {
  machine_id: string
  title: string
  description?: string | null
  priority?: WorkOrderPriority
  assigned_to?: number | null
  due_at?: string | null
}

// POST /api/alerts/{id}/work-order. Every field is optional: the title and
// priority default from the alert on the server.
export interface WorkOrderFromAlert {
  title?: string | null
  description?: string | null
  priority?: WorkOrderPriority | null
  assigned_to?: number | null
  due_at?: string | null
}

export interface WorkOrderUpdate {
  title?: string
  description?: string | null
  priority?: WorkOrderPriority
  due_at?: string | null
}

export interface WorkOrderComplete {
  notes?: string | null
  performed_at?: string | null
  maintenance_type?: 'preventive' | 'corrective'
  // Restart health tracking. The drawer always sends it explicitly.
  reset_health?: boolean | null
}

// GET /api/work-orders/assignees (admin/supervisor): active users, no email.
export interface Assignee {
  id: number
  name: string
  role: Role
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

// ---- Live telemetry over MQTT (design/M6_LIVE_TELEMETRY.md §8) ----
export interface TelemetryStats {
  received: number
  accepted: number
  rejected: number
  duplicates: number
  errors: number
  queue_overflows: number
  queue_depth: number
  reconnects: number
}

// GET /api/telemetry/status. When MQTT_BROKER_HOST is unset the backend still
// answers 200 with enabled=false, broker=null and zeroed stats.
export interface TelemetryStatus {
  enabled: boolean
  connected: boolean
  broker: string | null
  topic_prefix: string | null
  started_at: string | null
  last_message_at: string | null
  stats: TelemetryStats
  device_watchdog: DeviceWatchdogStatus
}

// GET /api/telemetry/status .device_watchdog — whether silence alerting is
// actually running (src/api/routes/telemetry.py:_watchdog_status). `enabled`
// is false unless MAINTAINIQ_SWEEP_INTERVAL_S > 0; `armed` stays false until
// the broker connection has been up for `grace_s`.
export interface DeviceWatchdogStatus {
  enabled: boolean
  interval_s: number | null
  grace_s: number
  armed: boolean
  last_tick_at: string | null
}

// ---- Device health (design/2026-10-06-device-health-design.md) ----
// One shared rule (src/telemetry/device_health.py): online ≤ 1.5× heartbeat,
// stale ≤ 3×, offline beyond that or on LWT, never_reported for a node that
// only ever sent an LWT.
export type DeviceState = 'online' | 'stale' | 'offline' | 'never_reported'
export type DeviceIncidentStatus = 'open' | 'resolved'
export type DeviceIncidentKind = 'silent' | 'lwt'

export interface DeviceIncident {
  id: number
  device_id: string
  machine_id: string | null
  kind: DeviceIncidentKind
  status: DeviceIncidentStatus
  opened_at: string
  last_seen_at: string | null
  resolved_at: string | null
  acknowledged_at: string | null
  acknowledged_by: number | null
}

// One row of GET /api/telemetry/devices (latest retained status per device).
// Every M6 field after last_seen_at may be null: an LWT-only device has
// reported nothing but `online: false`. The device-health fields at the end are
// always present.
export interface TelemetryDevice {
  device_id: string
  machine_id: string | null
  online: boolean
  last_seen_at: string
  firmware: string | null
  uptime_s: number | null
  buffer_depth: number | null
  buffer_capacity: number | null
  buffer_dropped_total: number | null
  publish_attempts_total: number | null
  publish_failures_total: number | null
  wifi_rssi_dbm: number | null
  snapshot_interval_s: number | null
  heartbeat_interval_s: number | null
  // Device-health fields (src/api/schemas.py DeviceHealth).
  state: DeviceState
  silent_for_s: number | null
  expected_heartbeat_s: number
  open_incident_id: number | null
}

// GET /api/telemetry/devices/{id}
export interface DeviceDetail extends TelemetryDevice {
  recent_incidents: DeviceIncident[]
}

// ---- System KPIs (GET /api/kpis/detail .system, contract §9) ----
export interface KpiNotApplicable {
  status: 'not_applicable'
  reason: string
}

interface KpiAvailableBase {
  status: 'available'
  window_minutes: number
}

// `unknown`: edge_buffer_health when telemetry arrived but no device has sent
// a status message yet, so its buffer is unobservable.
export type HealthTriState = 'ok' | 'degraded' | 'critical' | 'down' | 'unknown'

export interface SensorCollectionKpi extends KpiAvailableBase {
  rate: number | null
  devices: { device_id: string; collected: number; expected: number | null; rate: number | null }[]
}

export interface TransmissionSuccessKpi extends KpiAvailableBase {
  rate: number | null
  received: number
  expected: number
  lost: number
  device_reported_failure_rate: number | null
  devices: Record<string, unknown>[]
}

export interface EdgeBufferKpi extends KpiAvailableBase {
  state: HealthTriState
  buffered_share: number | null
  dropped_total: number
  devices: {
    device_id: string
    depth: number | null
    capacity: number | null
    utilization: number | null
    dropped_total: number | null
    state: HealthTriState
  }[]
}

export interface CloudSyncKpi extends KpiAvailableBase {
  state: HealthTriState
  lag_p50_s: number | null
  lag_p95_s: number | null
  devices_online: number
  devices_total: number
  last_message_age_s: number | null
  rejected_rate: number | null
}

export type KpiOr<T> = T | KpiNotApplicable

export interface SystemKpis {
  sensor_collection_rate: KpiOr<SensorCollectionKpi>
  transmission_success_rate: KpiOr<TransmissionSuccessKpi>
  edge_buffer_health: KpiOr<EdgeBufferKpi>
  cloud_sync_health: KpiOr<CloudSyncKpi>
}

// Only the slice of /api/kpis/detail the UI reads today; the other sections
// (machine_health, maintenance, prediction, operational) are left untyped.
export interface KpiDetail {
  system: SystemKpis
  [section: string]: unknown
}
