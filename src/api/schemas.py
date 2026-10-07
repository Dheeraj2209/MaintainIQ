"""Pydantic response/request models for the API.

KPI responses deliberately use permissive dict/Any-typed fields: the KPI
module mixes "available" payloads with `{"status": "not_applicable", ...}`
markers (see src/kpi/calculations.py), and pinning every nested shape here
would duplicate that logic without adding safety for a demo dashboard.
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["admin", "supervisor", "operator"]


class MachineSummary(BaseModel):
    machine_id: str
    health_state: str
    confidence: Optional[float] = None
    prediction_source: Optional[str] = None
    probable_cause: Optional[str] = None
    last_reading_at: Optional[str] = None
    vibration_severity: str
    risk_score: float
    abnormal_event_count: int
    open_alert_count: int
    predicted_rul_minutes: Optional[float] = None
    rul_estimate_kind: Optional[str] = None
    out_of_distribution: Optional[bool] = None


class TrendPoint(BaseModel):
    timestamp: str
    value: Optional[float] = None


# Prediction feedback (design/2026-10-07-prediction-feedback-design.md).
FeedbackOutcome = Literal["confirmed_failure", "maintenance_prevented", "false_alarm", "unknown"]
FeedbackCause = Literal["bearing_wear", "imbalance", "sensor_or_data_quality_issue", "unknown", "other"]


class AlertFeedback(BaseModel):
    id: int
    alert_id: int
    outcome: FeedbackOutcome
    actual_cause: Optional[FeedbackCause] = None
    actual_failure_at: Optional[str] = None
    notes: Optional[str] = None
    work_order_id: Optional[int] = None
    recorded_by: int
    recorded_by_name: Optional[str] = None
    recorded_at: str
    updated_by: Optional[int] = None
    updated_at: Optional[str] = None


class AlertFeedbackIn(BaseModel):
    """Body of PUT /api/alerts/{id}/feedback and POST /api/alerts/{id}/close.
    A full replacement: an omitted optional field is stored as NULL."""
    outcome: FeedbackOutcome
    actual_cause: Optional[FeedbackCause] = None
    actual_failure_at: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=2000)
    work_order_id: Optional[int] = None


class AlertCloseRequest(AlertFeedbackIn):
    pass


class Alert(BaseModel):
    id: int
    machine_id: str
    opened_at: str
    resolved_at: Optional[str] = None
    severity: str
    health_state: str
    probable_cause: Optional[str] = None
    message: Optional[str] = None
    status: str
    source: Optional[str] = None
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[int] = None
    # Paging ladder (design/2026-10-07-work-orders-escalation-design.md).
    page_level: int = 0
    last_paged_at: Optional[str] = None
    # List/detail routes only; None in broadcasts.
    active_work_order_id: Optional[int] = None
    # The prediction/reading/model that opened the alert, and who closed it.
    prediction_id: Optional[int] = None
    reading_id: Optional[int] = None
    model_version: Optional[str] = None
    closed_by: Optional[int] = None
    # List/detail routes only; None in broadcasts (the feedback events carry
    # the feedback beside the alert instead).
    feedback: Optional[AlertFeedback] = None


class AlertCloseResponse(BaseModel):
    alert: Alert
    feedback: AlertFeedback
    closed: bool  # False when the alert was already resolved


# "Why this alert?" (design/2026-10-07-alert-explanation-design.md, "API").

class ExplanationReading(BaseModel):
    reading_id: Optional[int] = None  # None only for an unsaved trigger (decision 5)
    timestamp: str
    cycle: Optional[int] = None
    is_trigger: bool = False
    vibration_h_rms: Optional[float] = None
    vibration_h_kurtosis: Optional[float] = None
    vibration_v_rms: Optional[float] = None
    vibration_v_kurtosis: Optional[float] = None
    cross_axis_rms_ratio: Optional[float] = None
    cross_axis_correlation: Optional[float] = None


class ExplanationChannel(BaseModel):
    key: str
    label: str
    unit: Optional[str] = None


class TriggeringReadings(BaseModel):
    # snapshot_features: a reading-less snapshot whose trigger point is the
    # feature vector the model scored (POST /api/predictions/rul).
    locate: Literal["reading_id", "prediction_reading", "nearest_timestamp", "snapshot_features", "none"]
    trigger_reading_id: Optional[int] = None
    trigger_timestamp: Optional[str] = None
    channels: list[ExplanationChannel]
    readings: list[ExplanationReading]


class KeyFactor(BaseModel):
    feature: str  # e.g. "h_kurtosis"
    label: str  # "Horizontal kurtosis"
    axis: Literal["horizontal", "vertical", "combined"]
    unit: Optional[str] = None  # "g" | "fraction" | None
    value: float
    baseline: float
    ratio: float
    direction: Literal["up", "down", "steady"]
    deviation: float  # |ln ratio|
    importance: Optional[float] = None  # 0..1, None when unweighted
    score: float
    outside_training_bounds: Optional[bool] = None  # None = unknown
    out_of_bounds_features: list[str] = []


class OperatingCondition(BaseModel):
    feature: Literal["speed_rpm", "load_kn"]
    label: str
    unit: str
    value: Optional[float] = None
    outside_training_bounds: Optional[bool] = None


class KeyFactors(BaseModel):
    status: Literal["ok", "insufficient_history", "no_readings", "error"]
    method: Optional[Literal["model_context", "promoted_columns"]] = None
    weighting: Optional[Literal["model_importance", "unweighted"]] = None
    baseline_readings: int = 0
    factors: list[KeyFactor] = []
    operating_conditions: list[OperatingCondition] = []


class ExplanationPrediction(BaseModel):
    locate: Literal["snapshot", "prediction_id", "nearest_timestamp"]
    prediction_id: Optional[int] = None
    timestamp: Optional[str] = None
    model_version: Optional[str] = None
    health_state: Optional[str] = None
    failure_within_horizon_probability: Optional[float] = None
    predicted_rul_minutes: Optional[float] = None
    rul_estimate_kind: Optional[str] = None
    prediction_interval_low: Optional[float] = None
    prediction_interval_high: Optional[float] = None
    prognostic_horizon_minutes: Optional[float] = None
    out_of_distribution: bool = False
    outside_training_features: list[str] = []
    warnings: list[str] = []


class RuleCheck(BaseModel):
    feature: str
    value: Optional[float] = None
    operator: str
    threshold: Optional[float] = None
    passed: bool


class ProbableCauseExplanation(BaseModel):
    label: Optional[str] = None  # the alert's stored probable_cause
    display: str  # "Probable cause: bearing wear"
    disclaimer: str
    rule: Optional[str] = None  # rule id from rule_based.explain_probable_cause
    summary: Optional[str] = None
    checks: list[RuleCheck] = []
    evaluated_label: Optional[str] = None
    matches_alert: Optional[bool] = None
    inputs_source: Optional[Literal["snapshot_features", "trigger_reading"]] = None


class IncidentFeedback(BaseModel):
    outcome: str
    actual_cause: Optional[str] = None
    actual_failure_at: Optional[str] = None
    notes: Optional[str] = None


class IncidentWorkOrder(BaseModel):
    id: int
    status: str
    title: str
    completed_at: Optional[str] = None


class IncidentMaintenance(BaseModel):
    id: int
    performed_at: str
    type: Optional[str] = None
    description: Optional[str] = None
    technician: Optional[str] = None


class SimilarIncident(BaseModel):
    alert_id: int
    machine_id: str
    opened_at: str
    resolved_at: Optional[str] = None
    status: str
    severity: str
    health_state: str
    probable_cause: Optional[str] = None
    synthetic: bool
    similarity: float
    match_reasons: list[str]
    feedback: Optional[IncidentFeedback] = None
    cause_confirmed: Optional[bool] = None
    work_orders: list[IncidentWorkOrder] = []
    maintenance: list[IncidentMaintenance] = []


class SnapshotRef(BaseModel):
    kind: Literal["created", "escalated"]
    created_at: str


class AlertExplanation(BaseModel):
    alert: Alert  # API_ALERT_COLUMNS shape, incl. feedback
    explanation_version: int
    source: Literal["snapshot", "reconstructed"]
    snapshot_kind: Optional[Literal["created", "escalated"]] = None
    snapshot_at: Optional[str] = None
    snapshots: list[SnapshotRef] = []
    generated_at: str  # when the body was computed (snapshot time, or now)
    synthetic: bool
    triggering_readings: Optional[TriggeringReadings] = None
    key_factors: Optional[KeyFactors] = None
    prediction: Optional[ExplanationPrediction] = None
    probable_cause: Optional[ProbableCauseExplanation] = None
    similar_incidents: list[SimilarIncident] = []
    notes: list[str] = []


class MaintenanceRecord(BaseModel):
    id: int
    machine_id: str
    performed_at: str
    description: Optional[str] = None
    technician: Optional[str] = None
    created_at: str
    alert_id: Optional[int] = None
    type: Optional[Literal["preventive", "corrective"]] = None
    resets_health: bool = False


class MaintenanceSummary(BaseModel):
    machine_id: str
    last_maintenance_at: Optional[str] = None
    days_since_last_maintenance: Optional[int] = None
    completed_maintenance_count: int
    unresolved_alert_count: int
    avg_alert_resolution_hours: Optional[float] = None
    avg_alert_acknowledgement_hours: Optional[float] = None
    due_for_inspection: bool
    open_work_order_count: int = 0
    avg_work_order_completion_hours: Optional[float] = None


class MaintenanceCreate(BaseModel):
    # Capped like the work-order fields: the history is embedded in every
    # GET /api/machines/{id}, so one huge row would bloat it for every user.
    machine_id: str = Field(max_length=128)
    performed_at: str = Field(max_length=64)
    description: Optional[str] = Field(None, max_length=2000)
    technician: Optional[str] = Field(None, max_length=200)
    alert_id: Optional[int] = None
    type: Optional[Literal["preventive", "corrective"]] = None
    # Restart health tracking (a repair). Only True resets; admin/supervisor only.
    reset_health: Optional[bool] = None


class MachineDetail(BaseModel):
    health: MachineSummary
    maintenance: MaintenanceSummary
    alerts: list[Alert]
    maintenance_history: list[MaintenanceRecord]


class KpiSummary(BaseModel):
    machine_count: int
    health_state_counts: dict
    open_alert_count: int
    machines_due_for_inspection: int
    prediction: dict
    open_work_order_count: int = 0


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreate(BaseModel):
    email: str
    name: str
    password: str
    role: Role


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[Role] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class NotificationOut(BaseModel):
    id: int
    alert_id: Optional[int] = None
    device_incident_id: Optional[int] = None
    recipient_email: str
    recipient_role: Optional[str] = None
    subject: str
    body: str
    status: str
    channel: Literal["email", "push"] = "email"
    created_at: str


# --- Push (design/2026-10-07-mobile-operator-pwa-design.md) ---------------------------

class PushConfig(BaseModel):
    enabled: bool
    public_key: Optional[str] = None


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSubscribeRequest(BaseModel):
    """Exactly PushSubscription.toJSON(). expirationTime is accepted and
    ignored: browsers send null, and expiry is handled by a 404/410."""
    endpoint: str = Field(max_length=2048)
    expirationTime: Optional[float] = None
    keys: PushKeys


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(max_length=2048)


class PushSubscriptionOut(BaseModel):
    id: int
    endpoint: str
    is_active: bool
    created_at: str
    updated_at: str
    last_used_at: Optional[str] = None


class PushTestResult(BaseModel):
    sent: int
    failed: int
    expired: int


# --- Device health (design/2026-10-06-device-health-design.md) ---------------------

DeviceState = Literal["online", "stale", "offline", "never_reported"]


class DeviceIncident(BaseModel):
    id: int
    device_id: str
    machine_id: Optional[str] = None
    kind: Literal["silent", "lwt"]
    status: Literal["open", "resolved"]
    opened_at: str
    last_seen_at: Optional[str] = None
    resolved_at: Optional[str] = None
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[int] = None


class DeviceHealth(BaseModel):
    # Every M6 §8 device_status field, unchanged; `online` keeps its meaning
    # (state is online or stale).
    device_id: str
    machine_id: Optional[str] = None
    online: bool
    last_seen_at: str
    firmware: Optional[str] = None
    uptime_s: Optional[float] = None
    buffer_depth: Optional[int] = None
    buffer_capacity: Optional[int] = None
    buffer_dropped_total: Optional[int] = None
    publish_attempts_total: Optional[int] = None
    publish_failures_total: Optional[int] = None
    wifi_rssi_dbm: Optional[float] = None
    snapshot_interval_s: Optional[float] = None
    heartbeat_interval_s: Optional[float] = None
    state: DeviceState
    silent_for_s: Optional[float] = None  # now - last heard, 1 decimal
    expected_heartbeat_s: float           # effective heartbeat (10.0 when unreported)
    open_incident_id: Optional[int] = None


class DeviceDetail(DeviceHealth):
    recent_incidents: list[DeviceIncident]


# --- Work orders (design/2026-10-07-work-orders-escalation-design.md) ---------------

WorkOrderStatus = Literal["open", "assigned", "in_progress", "done", "cancelled"]
Priority = Literal["low", "medium", "high"]


class WorkOrder(BaseModel):
    id: int
    alert_id: Optional[int] = None
    machine_id: str
    status: WorkOrderStatus
    priority: Priority
    title: str
    description: Optional[str] = None
    assigned_to: Optional[int] = None
    assigned_to_name: Optional[str] = None
    created_by: Optional[int] = None
    created_by_name: Optional[str] = None
    due_at: Optional[str] = None
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    maintenance_record_id: Optional[int] = None
    notes: Optional[str] = None


class WorkOrderEvent(BaseModel):
    id: int
    work_order_id: int
    event: Literal["created", "edited", "assigned", "started", "completed", "cancelled"]
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    assigned_to: Optional[int] = None
    assigned_to_name: Optional[str] = None
    note: Optional[str] = None
    created_at: str


class WorkOrderDetail(WorkOrder):
    events: list[WorkOrderEvent]  # oldest first
    alert: Optional[Alert] = None  # the linked alert, if any


class WorkOrderCreate(BaseModel):
    machine_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    priority: Priority = "medium"
    assigned_to: Optional[int] = None
    due_at: Optional[str] = None


class WorkOrderFromAlert(BaseModel):
    # All optional: title and priority default from the alert (decision 13).
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    priority: Optional[Priority] = None
    assigned_to: Optional[int] = None
    due_at: Optional[str] = None


class WorkOrderUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    priority: Optional[Priority] = None
    due_at: Optional[str] = None


class WorkOrderAssign(BaseModel):
    assigned_to: int
    note: Optional[str] = Field(None, max_length=500)


class WorkOrderComplete(BaseModel):
    notes: Optional[str] = Field(None, max_length=2000)
    performed_at: Optional[str] = None
    maintenance_type: Literal["preventive", "corrective"] = "corrective"
    # None: reset only when completing the machine's current alert (plan D2).
    reset_health: Optional[bool] = None


class WorkOrderCancel(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class Assignee(BaseModel):
    id: int
    name: str
    role: Role


Severity = Literal["healthy", "degrading", "faulty", "critical"]


class SimulateFaultRequest(BaseModel):
    machine_id: str
    severity: Severity


class SimulateFaultResponse(BaseModel):
    machine_id: str
    health_state: str
    probable_cause: Optional[str] = None
    alert: Optional[Alert] = None
    emails_sent: int


class RULPredictionRequest(BaseModel):
    machine_id: str = Field(min_length=1, max_length=128)
    horizontal: list[float] = Field(min_length=32)
    vertical: list[float] = Field(min_length=32)
    sample_rate_hz: float = Field(default=25_600.0, gt=0)
    speed_rpm: float = Field(gt=0)
    load_kn: float = Field(ge=0)


class RULPredictionResponse(BaseModel):
    machine_id: str
    predicted_rul_minutes: float
    predicted_rul_hours: float
    rul_estimate_kind: str
    prognostic_horizon_minutes: float
    failure_within_horizon_probability: float
    raw_failure_within_horizon_probability: float
    warning_persistence_snapshots: int
    prediction_interval_90_minutes: list[Optional[float]]
    health_state: str
    model_version: str
    history_snapshots: int
    out_of_distribution: bool
    outside_training_features: list[str]
    warnings: list[str]


class ReplayStartRequest(BaseModel):
    machine_id: str = Field(min_length=1, max_length=128)
    speed_multiplier: float = Field(default=1.0, gt=0)


class ReplayStopRequest(BaseModel):
    machine_id: str = Field(min_length=1, max_length=128)


class ReportCreateRequest(BaseModel):
    report_type: Literal["machine_prognostic", "model_performance", "fleet_summary"]
    scope: str
    format: Literal["markdown", "json"] = "json"
    period_start: Optional[str] = None
    period_end: Optional[str] = None
