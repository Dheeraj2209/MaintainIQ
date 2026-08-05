"""Pydantic response/request models for the API.

KPI responses deliberately use permissive dict/Any-typed fields: the KPI
module mixes "available" payloads with `{"status": "not_applicable", ...}`
markers (see src/kpi/calculations.py), and pinning every nested shape here
would duplicate that logic without adding safety for a demo dashboard.
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel

Role = Literal["admin", "supervisor", "operator"]


class MachineSummary(BaseModel):
    machine_id: str
    health_state: str
    confidence: Optional[float] = None
    prediction_source: Optional[str] = None
    probable_cause: Optional[str] = None
    last_reading_at: Optional[str] = None
    vibration_severity: str
    temperature_severity: str
    risk_score: float
    abnormal_event_count: int
    open_alert_count: int


class TrendPoint(BaseModel):
    timestamp: str
    value: Optional[float] = None


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


class MaintenanceRecord(BaseModel):
    id: int
    machine_id: str
    performed_at: str
    description: Optional[str] = None
    technician: Optional[str] = None
    created_at: str
    alert_id: Optional[int] = None
    type: Optional[Literal["preventive", "corrective"]] = None


class MaintenanceSummary(BaseModel):
    machine_id: str
    last_maintenance_at: Optional[str] = None
    days_since_last_maintenance: Optional[int] = None
    completed_maintenance_count: int
    unresolved_alert_count: int
    avg_alert_resolution_hours: Optional[float] = None
    avg_alert_acknowledgement_hours: Optional[float] = None
    due_for_inspection: bool


class MaintenanceCreate(BaseModel):
    machine_id: str
    performed_at: str
    description: Optional[str] = None
    technician: Optional[str] = None
    alert_id: Optional[int] = None
    type: Optional[Literal["preventive", "corrective"]] = None


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
    recipient_email: str
    recipient_role: Optional[str] = None
    subject: str
    body: str
    status: str
    created_at: str


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
