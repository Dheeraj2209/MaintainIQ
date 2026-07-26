"""Pydantic response/request models for the API.

KPI responses deliberately use permissive dict/Any-typed fields: the KPI
module mixes "available" payloads with `{"status": "not_applicable", ...}`
markers (see src/kpi/calculations.py), and pinning every nested shape here
would duplicate that logic without adding safety for a demo dashboard.
"""
from typing import Any, Optional

from pydantic import BaseModel


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


class MaintenanceRecord(BaseModel):
    id: int
    machine_id: str
    performed_at: str
    description: Optional[str] = None
    technician: Optional[str] = None
    created_at: str


class MaintenanceCreate(BaseModel):
    machine_id: str
    performed_at: str
    description: Optional[str] = None
    technician: Optional[str] = None


class MachineDetail(BaseModel):
    health: MachineSummary
    maintenance: dict
    alerts: list[Alert]
    maintenance_history: list[MaintenanceRecord]


class KpiSummary(BaseModel):
    machine_count: int
    health_state_counts: dict
    open_alert_count: int
    machines_due_for_inspection: int
    prediction: dict
