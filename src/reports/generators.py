"""Report content generators + renderers (Phase 6).

Each builder reads ONLY real persisted rows and returns a machine-readable
`summary` dict. Renderers turn a summary into the stored `content` string. The
service (service.py) builds the summary once and derives both `content` and the
persisted `summary_json` from it, so the two always match.

Latency percentiles reuse src.observability.telemetry._percentile — no math is
re-implemented here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.observability.telemetry import _percentile

REPORT_TYPES = ("machine_prognostic", "model_performance", "fleet_summary")
FORMATS = ("markdown", "json")

_TRAJECTORY_LIMIT = 10
_RECENT_ALERTS_LIMIT = 10
_TOP_AT_RISK_LIMIT = 5


def _period_clause(column: str, period_start, period_end) -> tuple[str, dict]:
    """Build an ' AND <col> >= :period_start AND <col> <= :period_end' fragment.
    `column` is a fixed literal supplied by this module (never user input)."""
    clauses = []
    params: dict = {}
    if period_start is not None:
        clauses.append(f"{column} >= :period_start")
        params["period_start"] = period_start
    if period_end is not None:
        clauses.append(f"{column} <= :period_end")
        params["period_end"] = period_end
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _add_minutes(iso_ts: str, minutes: float) -> str:
    return (datetime.fromisoformat(iso_ts) + timedelta(minutes=minutes)).isoformat()


def machine_prognostic(conn, *, scope, period_start=None, period_end=None) -> dict:
    where, params = _period_clause("timestamp", period_start, period_end)
    params["machine_id"] = scope

    latest = conn.execute(
        f"""SELECT timestamp, health_state, predicted_rul_minutes, rul_estimate_kind,
                   prediction_interval_low, prediction_interval_high,
                   failure_within_horizon_probability, out_of_distribution,
                   probable_cause, model_version, confidence
            FROM predictions
            WHERE machine_id = :machine_id{where}
            ORDER BY id DESC LIMIT 1""",
        params,
    ).fetchone()

    count_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM predictions WHERE machine_id = :machine_id{where}",
        params,
    ).fetchone()

    trajectory_rows = conn.execute(
        f"""SELECT timestamp, health_state FROM predictions
            WHERE machine_id = :machine_id{where}
            ORDER BY id DESC LIMIT {_TRAJECTORY_LIMIT}""",
        params,
    ).fetchall()

    alert_where, alert_params = _period_clause("opened_at", period_start, period_end)
    alert_params["machine_id"] = scope
    alert_rows = conn.execute(
        f"""SELECT opened_at, severity, health_state, status, probable_cause, message
            FROM alerts
            WHERE machine_id = :machine_id{alert_where}
            ORDER BY opened_at DESC LIMIT {_RECENT_ALERTS_LIMIT}""",
        alert_params,
    ).fetchall()

    current = None
    recommended = {"within_minutes": None, "by_timestamp": None}
    if latest is not None:
        current = {
            "timestamp": latest["timestamp"],
            "health_state": latest["health_state"],
            "predicted_rul_minutes": latest["predicted_rul_minutes"],
            "rul_estimate_kind": latest["rul_estimate_kind"],
            "prediction_interval_low": latest["prediction_interval_low"],
            "prediction_interval_high": latest["prediction_interval_high"],
            "failure_within_horizon_probability": latest["failure_within_horizon_probability"],
            "out_of_distribution": bool(latest["out_of_distribution"]),
            "probable_cause": latest["probable_cause"],
            "model_version": latest["model_version"],
            "confidence": latest["confidence"],
        }
        rul = latest["predicted_rul_minutes"]
        if rul is not None:
            recommended = {
                "within_minutes": rul,
                "by_timestamp": _add_minutes(latest["timestamp"], rul),
            }

    return {
        "report_type": "machine_prognostic",
        "scope": scope,
        "period": {"start": period_start, "end": period_end},
        "prediction_count": count_row["n"],
        "current": current,
        "health_state_trajectory": [
            {"timestamp": r["timestamp"], "health_state": r["health_state"]}
            for r in reversed(trajectory_rows)
        ],
        "recent_alerts": [
            {
                "opened_at": r["opened_at"],
                "severity": r["severity"],
                "health_state": r["health_state"],
                "status": r["status"],
                "probable_cause": r["probable_cause"],
                "message": r["message"],
            }
            for r in alert_rows
        ],
        "recommended_maintenance": recommended,
    }


def model_performance(conn, *, scope="fleet", period_start=None, period_end=None) -> dict:
    where, params = _period_clause("timestamp", period_start, period_end)

    agg = conn.execute(
        f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                SUM(CASE WHEN status = 'ok' THEN out_of_distribution ELSE 0 END) AS ood_sum,
                SUM(CASE WHEN status = 'ok' THEN warming_up ELSE 0 END) AS warming_sum
            FROM model_inference_log
            WHERE 1 = 1{where}""",
        params,
    ).fetchone()
    total = agg["total"] or 0
    errors = agg["errors"] or 0
    ok_count = agg["ok_count"] or 0
    ood_sum = agg["ood_sum"] or 0
    warming_sum = agg["warming_sum"] or 0

    latencies = [
        r["latency_ms"]
        for r in conn.execute(
            f"""SELECT latency_ms FROM model_inference_log
                WHERE 1 = 1{where} ORDER BY latency_ms ASC""",
            params,
        ).fetchall()
    ]

    active = conn.execute(
        """SELECT model_version, algorithm, trained_at, metrics_json
           FROM model_registry WHERE is_active = 1
           ORDER BY deployed_at DESC LIMIT 1"""
    ).fetchone()
    active_model = None
    if active is not None:
        metrics = None
        if active["metrics_json"]:
            try:
                metrics = json.loads(active["metrics_json"])
            except (ValueError, TypeError):
                metrics = None
        active_model = {
            "model_version": active["model_version"],
            "algorithm": active["algorithm"],
            "trained_at": active["trained_at"],
            "metrics": metrics,
        }

    return {
        "report_type": "model_performance",
        "scope": "fleet",
        "period": {"start": period_start, "end": period_end},
        "inference_count": total,
        "error_count": errors,
        "error_rate": (errors / total) if total else 0.0,
        "latency_p50_ms": _percentile(latencies, 50.0),
        "latency_p95_ms": _percentile(latencies, 95.0),
        "ood_rate": (ood_sum / ok_count) if ok_count else 0.0,
        "warming_up_rate": (warming_sum / ok_count) if ok_count else 0.0,
        "active_model": active_model,
    }


def fleet_summary(conn, *, scope="fleet", period_start=None, period_end=None) -> dict:
    pred_where, pred_params = _period_clause("timestamp", period_start, period_end)

    machine_count = conn.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"]

    latest_rows = conn.execute(
        f"""SELECT p.machine_id, p.health_state, p.predicted_rul_minutes
            FROM predictions p
            JOIN (SELECT machine_id, MAX(id) AS max_id FROM predictions
                  WHERE 1 = 1{pred_where} GROUP BY machine_id) l
              ON p.id = l.max_id
            ORDER BY p.machine_id""",
        pred_params,
    ).fetchall()

    health_distribution: dict = {}
    at_risk = []
    for r in latest_rows:
        state = r["health_state"]
        health_distribution[state] = health_distribution.get(state, 0) + 1
        at_risk.append({
            "machine_id": r["machine_id"],
            "predicted_rul_minutes": r["predicted_rul_minutes"],
            "health_state": r["health_state"],
        })

    # Lowest predicted RUL = most at risk; machines with no RUL sort last.
    at_risk.sort(key=lambda x: (
        x["predicted_rul_minutes"] is None,
        x["predicted_rul_minutes"] if x["predicted_rul_minutes"] is not None else 0.0,
    ))
    top_at_risk = at_risk[:_TOP_AT_RISK_LIMIT]

    alert_where, alert_params = _period_clause("opened_at", period_start, period_end)
    alert_agg = conn.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved_count
            FROM alerts WHERE 1 = 1{alert_where}""",
        alert_params,
    ).fetchone()

    maint_where, maint_params = _period_clause("performed_at", period_start, period_end)
    maint_agg = conn.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN type = 'preventive' THEN 1 ELSE 0 END) AS preventive,
                   SUM(CASE WHEN type = 'corrective' THEN 1 ELSE 0 END) AS corrective
            FROM maintenance_records WHERE 1 = 1{maint_where}""",
        maint_params,
    ).fetchone()

    return {
        "report_type": "fleet_summary",
        "scope": "fleet",
        "period": {"start": period_start, "end": period_end},
        "machine_count": machine_count,
        "health_distribution": health_distribution,
        "top_at_risk": top_at_risk,
        "alert_counts": {
            "total": alert_agg["total"] or 0,
            "open": alert_agg["open_count"] or 0,
            "resolved": alert_agg["resolved_count"] or 0,
        },
        "maintenance_counts": {
            "total": maint_agg["total"] or 0,
            "preventive": maint_agg["preventive"] or 0,
            "corrective": maint_agg["corrective"] or 0,
        },
    }


_BUILDERS = {
    "machine_prognostic": machine_prognostic,
    "model_performance": model_performance,
    "fleet_summary": fleet_summary,
}


def build_summary(conn, *, report_type, scope, period_start=None, period_end=None) -> dict:
    if report_type not in _BUILDERS:
        raise ValueError(f"unknown report_type: {report_type}")
    return _BUILDERS[report_type](
        conn, scope=scope, period_start=period_start, period_end=period_end
    )


# ---- rendering ----

def _md_cell(value) -> str:
    """Escape a value for a Markdown table cell: pipes/newlines break tables."""
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def render(fmt: str, summary: dict) -> str:
    if fmt == "json":
        return render_json(summary)
    if fmt == "markdown":
        return render_markdown(summary)
    raise ValueError(f"unknown format: {fmt}")


def render_json(summary: dict) -> str:
    return json.dumps(summary, indent=2)


def render_markdown(summary: dict) -> str:
    rt = summary.get("report_type")
    if rt == "machine_prognostic":
        return _md_machine_prognostic(summary)
    if rt == "model_performance":
        return _md_model_performance(summary)
    if rt == "fleet_summary":
        return _md_fleet_summary(summary)
    raise ValueError(f"unknown report_type: {rt}")


def _md_machine_prognostic(s: dict) -> str:
    p = s["period"]
    lines = [
        f"# Machine Prognostic Report — {_md_cell(s['scope'])}",
        "",
        f"- Period: {_md_cell(p['start'])} → {_md_cell(p['end'])}",
        f"- Predictions in scope: {s['prediction_count']}",
        "",
        "## Current Prognosis",
        "",
    ]
    cur = s["current"]
    if cur is None:
        lines.append("_No predictions available for this machine in the selected period._")
    else:
        rec = s["recommended_maintenance"]
        lines += [
            f"- Health state: {_md_cell(cur['health_state'])}",
            f"- Predicted RUL (minutes): {_md_cell(cur['predicted_rul_minutes'])}",
            f"- 90% interval (minutes): {_md_cell(cur['prediction_interval_low'])} "
            f"– {_md_cell(cur['prediction_interval_high'])}",
            f"- Failure-within-horizon probability: "
            f"{_md_cell(cur['failure_within_horizon_probability'])}",
            f"- Confidence: {_md_cell(cur['confidence'])}",
            f"- Out of distribution: {_md_cell(cur['out_of_distribution'])}",
            f"- Probable cause: {_md_cell(cur['probable_cause'])}",
            f"- Model version: {_md_cell(cur['model_version'])}",
            f"- Recommended maintenance by: {_md_cell(rec['by_timestamp'])}",
        ]
    lines += [
        "",
        "## Health-State Trajectory",
        "",
        "| Timestamp | Health state |",
        "| --- | --- |",
    ]
    for pt in s["health_state_trajectory"]:
        lines.append(f"| {_md_cell(pt['timestamp'])} | {_md_cell(pt['health_state'])} |")
    lines += [
        "",
        "## Recent Alerts",
        "",
        "| Opened | Severity | Health | Status | Cause | Message |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for a in s["recent_alerts"]:
        lines.append(
            f"| {_md_cell(a['opened_at'])} | {_md_cell(a['severity'])} "
            f"| {_md_cell(a['health_state'])} | {_md_cell(a['status'])} "
            f"| {_md_cell(a['probable_cause'])} | {_md_cell(a['message'])} |"
        )
    return "\n".join(lines) + "\n"


def _md_model_performance(s: dict) -> str:
    p = s["period"]
    lines = [
        "# Model Performance Report",
        "",
        f"- Period: {_md_cell(p['start'])} → {_md_cell(p['end'])}",
        "",
        "## Inference Telemetry",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Inference count | {s['inference_count']} |",
        f"| Error count | {s['error_count']} |",
        f"| Error rate | {s['error_rate']} |",
        f"| Latency p50 (ms) | {_md_cell(s['latency_p50_ms'])} |",
        f"| Latency p95 (ms) | {_md_cell(s['latency_p95_ms'])} |",
        f"| OOD rate | {s['ood_rate']} |",
        f"| Warming-up rate | {s['warming_up_rate']} |",
        "",
        "## Active Model",
        "",
    ]
    am = s["active_model"]
    if am is None:
        lines.append("_No active model registered._")
    else:
        metrics = am["metrics"]
        lines += [
            f"- Version: {_md_cell(am['model_version'])}",
            f"- Algorithm: {_md_cell(am['algorithm'])}",
            f"- Trained at: {_md_cell(am['trained_at'])}",
            f"- Metrics: {_md_cell(json.dumps(metrics) if metrics is not None else None)}",
        ]
    return "\n".join(lines) + "\n"


def _md_fleet_summary(s: dict) -> str:
    p = s["period"]
    lines = [
        "# Fleet Summary Report",
        "",
        f"- Period: {_md_cell(p['start'])} → {_md_cell(p['end'])}",
        f"- Machines: {s['machine_count']}",
        "",
        "## Health Distribution",
        "",
        "| Health state | Machines |",
        "| --- | --- |",
    ]
    for state, n in s["health_distribution"].items():
        lines.append(f"| {_md_cell(state)} | {n} |")
    lines += [
        "",
        "## Top At-Risk Machines",
        "",
        "| Machine | Predicted RUL (min) | Health state |",
        "| --- | --- | --- |",
    ]
    for m in s["top_at_risk"]:
        lines.append(
            f"| {_md_cell(m['machine_id'])} | {_md_cell(m['predicted_rul_minutes'])} "
            f"| {_md_cell(m['health_state'])} |"
        )
    ac = s["alert_counts"]
    mc = s["maintenance_counts"]
    lines += [
        "",
        "## Activity In Period",
        "",
        "| Category | Count |",
        "| --- | --- |",
        f"| Alerts total | {ac['total']} |",
        f"| Alerts open | {ac['open']} |",
        f"| Alerts resolved | {ac['resolved']} |",
        f"| Maintenance total | {mc['total']} |",
        f"| Maintenance preventive | {mc['preventive']} |",
        f"| Maintenance corrective | {mc['corrective']} |",
    ]
    return "\n".join(lines) + "\n"
