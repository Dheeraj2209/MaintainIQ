"""KPI calculations (M4).

Pure functions over the M3 SQLite store (src/storage/db.py) plus the ML
benchmark artifact (models/evaluation_report.json). One function per SRS
section 7.4 KPI category. The API layer (src/api/routes/kpis.py) only
reshapes what these return.

Scope note (matches this repo's "implement what's real, flag what isn't"
convention — see src/prediction/router.py and src/root_cause/rule_based.py):
a dataset-only prototype cannot compute every SRS KPI. Categories that need
live-hardware telemetry or longitudinal real-world data are returned as
`{"status": "not_applicable", "reason": ...}` rather than omitted, so the
KPI response contract stays stable once M6 (live telemetry) lands. See
design/DESIGN_BASELINE.md for the full decision table.
"""
import json
from pathlib import Path

from src.maintenance.records import days_since_last_maintenance, last_maintenance_at

_EVAL_REPORT_PATH = Path(__file__).resolve().parents[2] / "models" / "evaluation_report.json"

# Ordered severity of the four health states; drives the risk score and the
# "most severe wins" aggregation used across KPIs.
HEALTH_RANK = {"healthy": 0, "degrading": 1, "faulty": 2, "critical": 3}
_RISK_BY_STATE = {"healthy": 0.0, "degrading": 40.0, "faulty": 70.0, "critical": 100.0}

# Heuristic single-reading severity thresholds, kept consistent with
# src/root_cause/rule_based.py so the dashboard and the classifier agree on
# what "high" means.
_HIGH_KURTOSIS = 5.0
_HIGH_BAND_RATIO = 0.3
_HIGH_TEMP_C = 65.0

_NOT_APPLICABLE = "not_applicable"


def _all_machine_ids(conn) -> list:
    cur = conn.execute("SELECT machine_id FROM machines ORDER BY machine_id")
    return [row["machine_id"] for row in cur.fetchall()]


def _latest_prediction(conn, machine_id: str):
    cur = conn.execute(
        """SELECT health_state, confidence, source, model_name, probable_cause, timestamp
           FROM predictions
           WHERE machine_id = ?
           ORDER BY timestamp DESC
           LIMIT 1""",
        (machine_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _latest_reading(conn, machine_id: str):
    cur = conn.execute(
        """SELECT vibration_h_rms, vibration_h_kurtosis,
                  vibration_h_high_band_energy_ratio, temperature_c, timestamp
           FROM readings
           WHERE machine_id = ?
           ORDER BY timestamp DESC
           LIMIT 1""",
        (machine_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _vibration_severity(reading) -> str:
    """Coarse severity of the latest vibration reading: low/medium/high."""
    if not reading:
        return "unknown"
    kurt = reading.get("vibration_h_kurtosis")
    band = reading.get("vibration_h_high_band_energy_ratio")
    if kurt is None or band is None:
        return "unknown"
    if kurt >= _HIGH_KURTOSIS and band >= _HIGH_BAND_RATIO:
        return "high"
    if kurt >= _HIGH_KURTOSIS or band >= _HIGH_BAND_RATIO:
        return "medium"
    return "low"


def _temperature_severity(reading) -> str:
    if not reading:
        return "unknown"
    temp = reading.get("temperature_c")
    if temp is None:
        return "unknown"
    if temp >= _HIGH_TEMP_C:
        return "high"
    if temp >= _HIGH_TEMP_C - 15:
        return "medium"
    return "low"


def _open_alert_count(conn, machine_id: str) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE machine_id = ? AND status = 'open'",
        (machine_id,),
    )
    return cur.fetchone()["n"]


def _abnormal_event_count(conn, machine_id: str) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) AS n FROM predictions WHERE machine_id = ? AND health_state != 'healthy'",
        (machine_id,),
    )
    return cur.fetchone()["n"]


def _machine_health(conn, machine_id: str) -> dict:
    latest_pred = _latest_prediction(conn, machine_id)
    latest_reading = _latest_reading(conn, machine_id)
    health_state = latest_pred["health_state"] if latest_pred else "unknown"

    # Combined risk: driven by the current health state, nudged up when an
    # alert is still open for the machine (unresolved issue = higher risk).
    risk = _RISK_BY_STATE.get(health_state, 0.0)
    open_alerts = _open_alert_count(conn, machine_id)
    if open_alerts and risk < 100.0:
        risk = min(100.0, risk + 10.0)

    return {
        "machine_id": machine_id,
        "health_state": health_state,
        "confidence": latest_pred.get("confidence") if latest_pred else None,
        "prediction_source": latest_pred.get("source") if latest_pred else None,
        "probable_cause": latest_pred.get("probable_cause") if latest_pred else None,
        "last_reading_at": latest_reading.get("timestamp") if latest_reading else None,
        "vibration_severity": _vibration_severity(latest_reading),
        "temperature_severity": _temperature_severity(latest_reading),
        "risk_score": round(risk, 1),
        "abnormal_event_count": _abnormal_event_count(conn, machine_id),
        "open_alert_count": open_alerts,
    }


def machine_health_kpis(conn, machine_id: str = None) -> list:
    """Per-machine current health snapshot. One machine if machine_id given,
    else all machines (sorted by descending risk so the worst float to top)."""
    ids = [machine_id] if machine_id else _all_machine_ids(conn)
    result = [_machine_health(conn, mid) for mid in ids]
    result.sort(key=lambda m: m["risk_score"], reverse=True)
    return result


def _maintenance_for_machine(conn, machine_id: str) -> dict:
    days_since = days_since_last_maintenance(conn, machine_id)
    cur = conn.execute(
        "SELECT COUNT(*) AS n FROM maintenance_records WHERE machine_id = ?",
        (machine_id,),
    )
    completed = cur.fetchone()["n"]

    cur = conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE machine_id = ? AND status = 'open'",
        (machine_id,),
    )
    unresolved = cur.fetchone()["n"]

    # Mean resolution time over resolved alerts, in hours.
    cur = conn.execute(
        """SELECT opened_at, resolved_at FROM alerts
           WHERE machine_id = ? AND status = 'resolved' AND resolved_at IS NOT NULL""",
        (machine_id,),
    )
    durations = []
    from datetime import datetime
    for row in cur.fetchall():
        try:
            opened = datetime.fromisoformat(row["opened_at"])
            resolved = datetime.fromisoformat(row["resolved_at"])
        except (ValueError, TypeError):
            continue
        durations.append((resolved - opened).total_seconds() / 3600.0)
    avg_resolution_hours = round(sum(durations) / len(durations), 2) if durations else None

    return {
        "machine_id": machine_id,
        "last_maintenance_at": last_maintenance_at(conn, machine_id),
        "days_since_last_maintenance": days_since,
        "completed_maintenance_count": completed,
        "unresolved_alert_count": unresolved,
        "avg_alert_resolution_hours": avg_resolution_hours,
        # "Due for inspection" is a demo heuristic: never serviced, or an
        # unresolved alert is open. A real deployment would use per-machine
        # maintenance intervals (not available in the dataset).
        "due_for_inspection": days_since is None or unresolved > 0,
    }


def maintenance_kpis(conn, machine_id: str = None) -> list:
    ids = [machine_id] if machine_id else _all_machine_ids(conn)
    return [_maintenance_for_machine(conn, mid) for mid in ids]


def prediction_kpis() -> dict:
    """Reshape models/evaluation_report.json into the KPI response shape.

    These are model-quality KPIs measured on the held-out benchmark set, not
    live production metrics. Root-cause accuracy is intentionally absent — it
    needs labeled ground truth this dataset lacks (see rule_based.py)."""
    if not _EVAL_REPORT_PATH.exists():
        return {"status": _NOT_APPLICABLE, "reason": "evaluation_report.json not found; run src/training/run_pipeline.py"}

    with open(_EVAL_REPORT_PATH) as fh:
        report = json.load(fh)

    winner = report.get("winner")
    stats = report.get("candidates", {}).get(winner, {})
    threshold = report.get("confidence_threshold_analysis", {})

    return {
        "status": "available",
        "winning_model": winner,
        "accuracy": stats.get("accuracy"),
        "false_alarm_count": stats.get("false_alarm_count"),
        "missed_fault_count": stats.get("missed_fault_count"),
        "mean_confidence": stats.get("mean_confidence"),
        "suggested_confidence_threshold": threshold.get("suggested_threshold"),
        "evaluated_at": report.get("exported_at"),
        "root_cause_accuracy": {
            "status": _NOT_APPLICABLE,
            "reason": "no labeled root-cause ground truth in the IMS dataset "
                      "(bearing-wear failures are documented but other causes are not)",
        },
    }


def operational_kpis(conn) -> dict:
    """Operational-value KPIs derivable from one static dataset run.

    Computable: faults detected before the documented failure, and a
    maintenance-priority score per machine. Breakdown / emergency-maintenance
    reduction KPIs need longitudinal real-world data and are reported as
    not_applicable (SRS Section 10 flags this same limitation)."""
    machines = machine_health_kpis(conn)
    health_by_id = {m["machine_id"]: m for m in machines}

    # Faults detected before failure: for machines with a documented failure,
    # did the model flag an abnormal state at any point before the run ended?
    cur = conn.execute(
        "SELECT machine_id FROM machines WHERE is_documented_failure = 1"
    )
    documented = [row["machine_id"] for row in cur.fetchall()]
    detected = 0
    for mid in documented:
        if health_by_id.get(mid, {}).get("abnormal_event_count", 0) > 0:
            detected += 1

    # Maintenance priority score: risk + a bump for time since last service.
    priorities = []
    for mid in health_by_id:
        days_since = days_since_last_maintenance(conn, mid)
        overdue_bump = min(30.0, (days_since or 0) * 0.5) if days_since is not None else 15.0
        score = min(100.0, health_by_id[mid]["risk_score"] + overdue_bump)
        priorities.append({"machine_id": mid, "priority_score": round(score, 1)})
    priorities.sort(key=lambda p: p["priority_score"], reverse=True)

    return {
        "faults_detected_before_failure": {
            "status": "available",
            "documented_failure_machines": len(documented),
            "detected_early": detected,
        },
        "maintenance_priority": {"status": "available", "machines": priorities},
        "breakdown_reduction": {
            "status": _NOT_APPLICABLE,
            "reason": "requires longitudinal before/after real-world data; not available in a single static dataset run",
        },
        "emergency_maintenance_reduction": {
            "status": _NOT_APPLICABLE,
            "reason": "requires longitudinal before/after real-world data; not available in a single static dataset run",
        },
    }


def system_kpis() -> dict:
    """System-performance KPIs (sensor collection rate, transmission success,
    edge buffer, cloud sync). All describe live-hardware telemetry health that
    does not exist in a dataset-only prototype — reported not_applicable until
    M6 introduces a live telemetry source."""
    reason = "describes live-hardware telemetry health; no live sensor source until M6"
    return {
        "sensor_collection_rate": {"status": _NOT_APPLICABLE, "reason": reason},
        "transmission_success_rate": {"status": _NOT_APPLICABLE, "reason": reason},
        "edge_buffer_health": {"status": _NOT_APPLICABLE, "reason": reason},
        "cloud_sync_health": {"status": _NOT_APPLICABLE, "reason": reason},
    }


def summary(conn) -> dict:
    """Fleet-wide roll-up for the dashboard's KPI cards."""
    health = machine_health_kpis(conn)
    total = len(health)
    by_state = {}
    for m in health:
        by_state[m["health_state"]] = by_state.get(m["health_state"], 0) + 1

    cur = conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE status = 'open'")
    open_alerts = cur.fetchone()["n"]

    maintenance = maintenance_kpis(conn)
    due = sum(1 for m in maintenance if m["due_for_inspection"])

    return {
        "machine_count": total,
        "health_state_counts": by_state,
        "open_alert_count": open_alerts,
        "machines_due_for_inspection": due,
        "prediction": prediction_kpis(),
    }
