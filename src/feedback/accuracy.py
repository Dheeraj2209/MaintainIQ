"""Real-world model accuracy from operator feedback
(design/2026-10-07-prediction-feedback-design.md, decisions 9-12).

Every number here is about alerts a person labelled, not about the offline
benchmark:

* labelled       — feedback whose outcome is not 'unknown'
* precision      — (confirmed_failure + maintenance_prevented) / labelled
* false-alarm rate — false_alarm / labelled
* lead time      — actual_failure_at - opened_at for confirmed failures,
                   bucketed against the opening prediction's horizon
* RUL error      — |predicted - actual| for point estimates only; a
                   lower_bound only claimed "RUL > horizon", so it is censored
                   and reported as respected / not respected instead
* root cause     — alerts.probable_cause == actual_cause over feedback with a
                   specific cause ('other' always misses: the classifier
                   can't emit it)

Demo alerts are never measured. Missed failures can't be measured from
alert feedback at all: a failure that raised no alert has nothing to label.

The opening prediction (alerts.prediction_id) and opened_at describe the same
instant (decision 3), so the actual RUL at that prediction is the lead time.
Response shapes are stable: every key is always present, with nulls and
zeros when nothing is labelled.
"""
import json
from statistics import mean, median

from src.storage.db import table_exists
from src.telemetry.device_health import parse_iso

# == src.training.xjtu_rul.PROGNOSTIC_HORIZON_MINUTES (pinned by a test). Not
# imported: that module pulls in sklearn, and this one serves API requests.
DEFAULT_HORIZON_MINUTES = 120.0

OUTCOMES = ("confirmed_failure", "maintenance_prevented", "false_alarm", "unknown")
_TRUE_POSITIVES = ("confirmed_failure", "maintenance_prevented")
NOT_APPLICABLE = "not_applicable"
NO_LABELS_REASON = "no labelled alerts yet"
NO_TABLE_REASON = "alert_feedback table not present"
MISSED_FAILURES = {
    "status": NOT_APPLICABLE,
    "reason": "a failure that raised no alert has no alert to label; recall needs failures "
              "recorded independently of alerts",
}

_ROWS_SQL = """
    SELECT f.outcome, f.actual_cause, f.actual_failure_at,
           a.opened_at, a.created_at, a.probable_cause, a.model_version,
           p.rul_estimate_kind, p.predicted_rul_minutes, p.prognostic_horizon_minutes
    FROM alert_feedback f
    JOIN alerts a ON a.id = f.alert_id
    LEFT JOIN predictions p ON p.id = a.prediction_id
    WHERE COALESCE(a.source, '') != 'demo'
"""


def _rate(numerator: int, denominator: int):
    return round(numerator / denominator, 4) if denominator else None


def _minutes(value):
    return round(value, 1) if value is not None else None


def _period_bound(value, name: str):
    if value is None:
        return None
    parsed = parse_iso(value)
    if parsed is None:
        raise ValueError(f"{name} is not a valid ISO-8601 timestamp: {value!r}")
    return parsed


def _stats(rows: list[dict]) -> dict:
    """Every measured number for one set of feedback rows."""
    counts = {outcome: 0 for outcome in OUTCOMES}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    labelled = len(rows) - counts["unknown"]
    true_positives = sum(counts[o] for o in _TRUE_POSITIVES)

    leads, buckets = [], {"within_horizon_count": 0, "early_count": 0, "late_count": 0}
    abs_errors, lower_bound, lower_bound_respected = [], 0, 0
    for r in rows:
        if r["outcome"] != "confirmed_failure":
            continue
        failed, opened = parse_iso(r["actual_failure_at"]), parse_iso(r["opened_at"])
        if failed is None or opened is None:
            continue
        lead = (failed - opened).total_seconds() / 60.0
        leads.append(lead)
        horizon = r["prognostic_horizon_minutes"] or DEFAULT_HORIZON_MINUTES
        if lead < 0:
            buckets["late_count"] += 1
        elif lead <= horizon:
            buckets["within_horizon_count"] += 1
        else:
            buckets["early_count"] += 1

        predicted = r["predicted_rul_minutes"]
        if predicted is None:
            continue
        if r["rul_estimate_kind"] == "point_estimate":
            abs_errors.append(abs(predicted - lead))
        elif r["rul_estimate_kind"] == "lower_bound":
            lower_bound += 1
            lower_bound_respected += int(lead >= predicted)

    cause_rows = [r for r in rows if r["actual_cause"] not in (None, "unknown")]
    correct = sum(1 for r in cause_rows if r["probable_cause"] == r["actual_cause"])

    return {
        "feedback_count": len(rows),
        "labelled_count": labelled,
        "outcome_counts": counts,
        "precision": _rate(true_positives, labelled),
        "false_alarm_rate": _rate(counts["false_alarm"], labelled),
        "lead_time": {
            "count": len(leads),
            "mean_minutes": _minutes(mean(leads)) if leads else None,
            "median_minutes": _minutes(median(leads)) if leads else None,
            "min_minutes": _minutes(min(leads)) if leads else None,
            "max_minutes": _minutes(max(leads)) if leads else None,
            **buckets,
        },
        "rul_error": {
            "point_estimate_count": len(abs_errors),
            "mae_minutes": _minutes(mean(abs_errors)) if abs_errors else None,
            "median_abs_error_minutes": _minutes(median(abs_errors)) if abs_errors else None,
            "lower_bound_count": lower_bound,
            "lower_bound_respected_count": lower_bound_respected,
        },
        "root_cause": {
            "labelled_count": len(cause_rows),
            "correct_count": correct,
            "accuracy": _rate(correct, len(cause_rows)),
        },
    }


def _by_model_version(rows: list[dict]) -> list[dict]:
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["model_version"], []).append(r)

    def newest(version):
        stamps = [s for s in (parse_iso(r["created_at"]) for r in groups[version]) if s is not None]
        return max(stamps).timestamp() if stamps else float("-inf")

    # Newest first; alerts opened before migration 5 (NULL) last.
    ordered = sorted((v for v in groups if v is not None), key=newest, reverse=True)
    if None in groups:
        ordered.append(None)
    result = []
    for version in ordered:
        s = _stats(groups[version])
        result.append({
            "model_version": version,
            "feedback_count": s["feedback_count"],
            "labelled_count": s["labelled_count"],
            "precision": s["precision"],
            "false_alarm_rate": s["false_alarm_rate"],
            "median_lead_minutes": s["lead_time"]["median_minutes"],
            "rul_mae_minutes": s["rul_error"]["mae_minutes"],
            "root_cause_accuracy": s["root_cause"]["accuracy"],
        })
    return result


def offline_metrics(conn, model_version=None) -> dict | None:
    """The offline benchmark's failure_detection block for model_version
    (else the active model), or None when there is none. None is a normal
    state: POST /api/predictions/rul re-registers the model without metrics."""
    if not table_exists(conn, "model_registry"):
        return None
    if model_version is not None:
        row = conn.execute(
            "SELECT model_version, metrics_json FROM model_registry WHERE model_version = ?",
            (model_version,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT model_version, metrics_json FROM model_registry WHERE is_active = 1
               ORDER BY deployed_at DESC LIMIT 1"""
        ).fetchone()
    if row is None or not row["metrics_json"]:
        return None
    try:
        metrics = json.loads(row["metrics_json"])
    except (TypeError, ValueError):
        return None
    detection = metrics.get("failure_detection") if isinstance(metrics, dict) else None
    if not isinstance(detection, dict):
        return None
    return {
        "model_version": row["model_version"],
        "precision": detection.get("precision"),
        "recall": detection.get("recall"),
        "false_alarm_count": detection.get("false_alarm_count"),
        "missed_failure_window_count": detection.get("missed_failure_window_count"),
    }


def real_world_accuracy(conn, *, model_version=None, period_start=None, period_end=None) -> dict:
    """The GET /api/model/feedback-accuracy body (shape in the design doc).

    period_start / period_end filter on alerts.created_at (the wall clock;
    opened_at carries 2003 dates for replayed data). ValueError for an
    unparseable bound."""
    start = _period_bound(period_start, "period_start")
    end = _period_bound(period_end, "period_end")
    from src.feedback import export

    rows: list[dict] = []
    reason = NO_LABELS_REASON
    if table_exists(conn, "alert_feedback"):
        sql, params = _ROWS_SQL, []
        if model_version is not None:
            sql += " AND a.model_version = ?"
            params.append(model_version)
        for row in conn.execute(sql, params).fetchall():
            row = dict(row)
            if start is not None or end is not None:
                created = parse_iso(row["created_at"])
                if created is None or (start is not None and created < start) \
                        or (end is not None and created > end):
                    continue
            rows.append(row)
        exportable = export.count_episodes(conn, model_version=model_version)
    else:
        reason = NO_TABLE_REASON
        exportable = 0

    stats = _stats(rows)
    available = stats["labelled_count"] > 0
    return {
        "status": "available" if available else NOT_APPLICABLE,
        "reason": None if available else reason,
        "model_version": model_version,
        "period": {"start": period_start, "end": period_end},
        "horizon_minutes": DEFAULT_HORIZON_MINUTES,
        **stats,
        "missed_failures": dict(MISSED_FAILURES),
        "by_model_version": _by_model_version(rows),
        "offline": offline_metrics(conn, model_version),
        "exportable_episode_count": exportable,
    }


def compact_kpi(result: dict) -> dict:
    """The KPI `prediction.real_world` block from a real_world_accuracy result."""
    return {
        "status": result["status"],
        "reason": result["reason"],
        "labelled_count": result["labelled_count"],
        "precision": result["precision"],
        "false_alarm_rate": result["false_alarm_rate"],
        "median_lead_minutes": result["lead_time"]["median_minutes"],
        "rul_mae_minutes": result["rul_error"]["mae_minutes"],
    }


def root_cause_block(result: dict) -> dict | None:
    """The KPI root_cause_accuracy block from a real_world_accuracy result,
    or None when no feedback names a specific cause (the caller then keeps
    its offline not_applicable marker)."""
    rc = result["root_cause"]
    if not rc["labelled_count"]:
        return None
    return {
        "status": "available",
        "accuracy": rc["accuracy"],
        "labelled_count": rc["labelled_count"],
        "correct_count": rc["correct_count"],
        "source": "operator_feedback",
    }


def real_world_kpi(conn) -> dict:
    return compact_kpi(real_world_accuracy(conn))


def root_cause_kpi(conn) -> dict | None:
    return root_cause_block(real_world_accuracy(conn))


def not_applicable_kpi(reason: str = "no database connection") -> dict:
    """The real_world block for callers without a connection."""
    return {
        "status": NOT_APPLICABLE,
        "reason": reason,
        "labelled_count": 0,
        "precision": None,
        "false_alarm_rate": None,
        "median_lead_minutes": None,
        "rul_mae_minutes": None,
    }


def unfiltered_not_applicable(conn, *, reason: str, period_start=None, period_end=None) -> dict:
    """The not_applicable shape for a period that can't be applied (a report
    with free-text bounds). offline / exportable_episode_count are still
    filled, as for any not_applicable result."""
    result = real_world_accuracy(conn)
    result.update(_stats([]), status=NOT_APPLICABLE, reason=reason,
                  period={"start": period_start, "end": period_end}, by_model_version=[])
    return result
