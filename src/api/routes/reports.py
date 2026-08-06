"""Report generation + retrieval endpoints (Phase 6).

Per-type RBAC (design spec §7): operators may generate/read machine_prognostic;
model_performance and fleet_summary require admin/supervisor. The router is
mounted under Depends(get_current_user) in app.py, so every endpoint already
requires a session; the per-type check runs inside each handler.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.api.deps import get_db
from src.api.schemas import ReportCreateRequest
from src.auth.deps import get_current_user
from src.reports import service

router = APIRouter(prefix="/reports", tags=["reports"])

_ELEVATED_TYPES = ("model_performance", "fleet_summary")
_MEDIA_TYPES = {"markdown": "text/markdown", "json": "application/json"}
_EXTENSIONS = {"markdown": "md", "json": "json"}


def _authorize_type(user: dict, report_type: str) -> None:
    if report_type in _ELEVATED_TYPES and user["role"] not in ("admin", "supervisor"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Insufficient permissions for this report type"
        )


def _present(row: dict) -> dict:
    """Parse summary_json into a `summary` object; drop the raw JSON string."""
    out = dict(row)
    raw = out.pop("summary_json", None)
    out["summary"] = json.loads(raw) if raw else None
    return out


@router.post("")
def create_report(
    payload: ReportCreateRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    _authorize_type(user, payload.report_type)
    if payload.report_type == "machine_prognostic":
        exists = db.execute(
            "SELECT 1 FROM machines WHERE machine_id = ?", (payload.scope,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown machine: {payload.scope}")
    report = service.generate(
        db,
        report_type=payload.report_type,
        scope=payload.scope,
        format=payload.format,
        period_start=payload.period_start,
        period_end=payload.period_end,
        generated_by=user["id"],
    )
    return _present(report)


@router.get("")
def list_reports_endpoint(
    db=Depends(get_db),
    user=Depends(get_current_user),
    scope: str | None = Query(default=None),
):
    report_types = ["machine_prognostic"] if user["role"] == "operator" else None
    rows = service.list_reports(db, report_types=report_types, scope=scope)
    return [_present(r) for r in rows]


@router.get("/{report_id}")
def get_report_endpoint(
    report_id: int,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    report = service.get_report(db, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    _authorize_type(user, report["report_type"])
    return _present(report)


@router.get("/{report_id}/download")
def download_report_endpoint(
    report_id: int,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    report = service.get_report(db, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    _authorize_type(user, report["report_type"])
    fmt = report["format"]
    media_type = _MEDIA_TYPES.get(fmt, "text/plain")
    ext = _EXTENSIONS.get(fmt, "txt")
    return Response(
        content=report["content"],
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="report-{report_id}.{ext}"'},
    )
