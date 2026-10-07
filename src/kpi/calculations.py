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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.maintenance.records import days_since_last_maintenance, last_maintenance_at
from src.observability.telemetry import _percentile
from src.prediction import health_epoch
from src.storage.db import table_exists
from src.telemetry import device_health

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

_NOT_APPLICABLE = "not_applicable"


def _all_machine_ids(conn) -> list:
    cur = conn.execute("SELECT machine_id FROM machines ORDER BY machine_id")
    return [row["machine_id"] for row in cur.fetchall()]


def _latest_prediction(conn, machine_id: str):
    cur = conn.execute(
        """SELECT health_state, confidence, source, model_name, probable_cause, timestamp,
                  predicted_rul_minutes, rul_estimate_kind,
                  failure_within_horizon_probability, out_of_distribution,
                  instant_health_state, health_episode, warnings_json
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
        """SELECT vibration_h_rms, vibration_h_kurtosis, features_json, timestamp
           FROM readings
           WHERE machine_id = ?
           ORDER BY timestamp DESC
           LIMIT 1""",
        (machine_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _vibration_severity(reading) -> str:
    """Coarse severity of the latest vibration reading: low/medium/high.
    Kurtosis is a promoted column; the high-band-energy ratio is read from the
    JSON feature vector (it is no longer a promoted column in the canonical
    schema)."""
    if not reading:
        return "unknown"
    kurt = reading.get("vibration_h_kurtosis")
    try:
        features = json.loads(reading.get("features_json") or "{}")
    except (TypeError, ValueError):
        features = {}
    band = features.get("vibration_h_high_band_energy_ratio")
    if kurt is None or band is None:
        return "unknown"
    if kurt >= _HIGH_KURTOSIS and band >= _HIGH_BAND_RATIO:
        return "high"
    if kurt >= _HIGH_KURTOSIS or band >= _HIGH_BAND_RATIO:
        return "medium"
    return "low"


def _open_alert_count(conn, machine_id: str) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE machine_id = ? AND status = 'open'",
        (machine_id,),
    )
    return cur.fetchone()["n"]


def _abnormal_event_count(conn, machine_id: str) -> int:
    # Counted on the instant state: a held (ratcheted) row repeats an earlier
    # abnormal reading, it is not a new event (review R2-7). Pre-ratchet rows
    # have no instant state and count on their own state.
    cur = conn.execute(
        """SELECT COUNT(*) AS n FROM predictions
           WHERE machine_id = ? AND COALESCE(instant_health_state, health_state) != 'healthy'""",
        (machine_id,),
    )
    return cur.fetchone()["n"]


_COMMISSIONING = re.compile(r"^commissioning: (\d+)/(\d+) ")


def _commissioning(latest_pred) -> dict | None:
    """{"seen", "of"} from the latest prediction's commissioning warning
    (rul_realtime, plan D3), or None."""
    try:
        warnings = json.loads(latest_pred.get("warnings_json") or "[]")
    except (TypeError, ValueError):
        return None
    for warning in warnings if isinstance(warnings, list) else []:
        match = _COMMISSIONING.match(str(warning))
        if match:
            return {"seen": int(match.group(1)), "of": int(match.group(2))}
    return None


_HEALTH_WARNING_PREFIXES = ("commissioning:", "condition_receded:", "ood_not_latched:")


def _health_warnings(latest_pred) -> list:
    """The latest prediction's ratchet warnings (rul_realtime), for the
    machine views (plan Task 12)."""
    try:
        warnings = json.loads(latest_pred.get("warnings_json") or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(warnings, list):
        return []
    return [str(w) for w in warnings if str(w).startswith(_HEALTH_WARNING_PREFIXES)]


def _held_since(conn, machine_id: str, latest_pred) -> str | None:
    """When the held level was first reached in the latest prediction's
    episode: the earliest prediction of that episode at that state."""
    row = conn.execute(
        """SELECT MIN(timestamp) AS since FROM predictions
           WHERE machine_id = ? AND health_episode IS ? AND health_state = ?""",
        (machine_id, latest_pred.get("health_episode"), latest_pred.get("health_state")),
    ).fetchone()
    return row["since"] if row else None


def _machine_health(conn, machine_id: str) -> dict:
    latest_pred = _latest_prediction(conn, machine_id)
    latest_reading = _latest_reading(conn, machine_id)
    # The latest prediction can predate a reset or re-arm (plan D6): the
    # current state is then the DB held level, flagged reset_pending_reading.
    effective = health_epoch.effective_state(conn, machine_id, latest_pred)
    pending = effective["reset_pending_reading"]
    health_state = effective["health_state"] or "unknown"
    instant = None if pending or not latest_pred else latest_pred.get("instant_health_state")
    held = instant is not None and instant != health_state

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
        "risk_score": round(risk, 1),
        "abnormal_event_count": _abnormal_event_count(conn, machine_id),
        "open_alert_count": open_alerts,
        "predicted_rul_minutes": latest_pred.get("predicted_rul_minutes") if latest_pred else None,
        "rul_estimate_kind": latest_pred.get("rul_estimate_kind") if latest_pred else None,
        "out_of_distribution": bool(latest_pred["out_of_distribution"]) if latest_pred and latest_pred.get("out_of_distribution") is not None else None,
        "instant_health_state": instant,
        "health_state_held": held,
        "held_since": _held_since(conn, machine_id, latest_pred) if held else None,
        "commissioning": None if pending or not latest_pred else _commissioning(latest_pred),
        "reset_pending_reading": pending,
        "health_warnings": [] if pending or not latest_pred else _health_warnings(latest_pred),
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

    cur = conn.execute(
        """SELECT opened_at, acknowledged_at FROM alerts
           WHERE machine_id = ? AND acknowledged_at IS NOT NULL""",
        (machine_id,),
    )
    ack_durations = []
    for row in cur.fetchall():
        try:
            opened = datetime.fromisoformat(row["opened_at"])
            acknowledged = datetime.fromisoformat(row["acknowledged_at"])
        except (ValueError, TypeError):
            continue
        ack_durations.append((acknowledged - opened).total_seconds() / 3600.0)
    avg_acknowledgement_hours = round(sum(ack_durations) / len(ack_durations), 2) if ack_durations else None

    open_work_orders, avg_work_order_hours = _work_order_kpis(conn, machine_id)

    return {
        "machine_id": machine_id,
        "last_maintenance_at": last_maintenance_at(conn, machine_id),
        "days_since_last_maintenance": days_since,
        "completed_maintenance_count": completed,
        "unresolved_alert_count": unresolved,
        "avg_alert_resolution_hours": avg_resolution_hours,
        "avg_alert_acknowledgement_hours": avg_acknowledgement_hours,
        # "Due for inspection" is a demo heuristic: never serviced, or an
        # unresolved alert is open. A real deployment would use per-machine
        # maintenance intervals (not available in the dataset).
        "due_for_inspection": days_since is None or unresolved > 0,
        "open_work_order_count": open_work_orders,
        "avg_work_order_completion_hours": avg_work_order_hours,
    }


_ACTIVE_WORK_ORDER_STATUSES = "('open','assigned','in_progress')"


def _work_order_kpis(conn, machine_id: str) -> tuple:
    """(active work orders, mean created->completed hours over done orders or
    None) for one machine (work-orders design, decision 14). (0, None) on a
    DB without the work_orders table, so reports on an old file still work."""
    if not _table_exists(conn, "work_orders"):
        return 0, None
    open_count = conn.execute(
        f"SELECT COUNT(*) AS n FROM work_orders WHERE machine_id = ? "
        f"AND status IN {_ACTIVE_WORK_ORDER_STATUSES}",
        (machine_id,),
    ).fetchone()["n"]
    hours = []
    for row in conn.execute(
        """SELECT created_at, completed_at FROM work_orders
           WHERE machine_id = ? AND status = 'done' AND completed_at IS NOT NULL""",
        (machine_id,),
    ).fetchall():
        created = _parse_ts(row["created_at"])
        completed = _parse_ts(row["completed_at"])
        if created is None or completed is None:
            continue
        hours.append((completed - created).total_seconds() / 3600.0)
    return open_count, (round(sum(hours) / len(hours), 2) if hours else None)


def maintenance_kpis(conn, machine_id: str = None) -> list:
    ids = [machine_id] if machine_id else _all_machine_ids(conn)
    return [_maintenance_for_machine(conn, mid) for mid in ids]


def prediction_kpis(conn=None) -> dict:
    """Reshape models/evaluation_report.json into the KPI response shape.

    These are model-quality KPIs measured on the held-out benchmark set, not
    live production metrics. With `conn`, two field numbers from operator
    feedback are added (design/2026-10-07-prediction-feedback-design.md,
    decision 12): a compact `real_world` block, and root_cause_accuracy once
    someone has labelled a cause — the XJTU-SY dataset itself has no
    root-cause ground truth (see rule_based.py). Keys are only ever added, so
    the response shape is stable."""
    from src.feedback import accuracy as field_accuracy

    field = field_accuracy.real_world_accuracy(conn) if conn is not None else None
    real_world = (field_accuracy.compact_kpi(field) if field is not None
                  else field_accuracy.not_applicable_kpi())

    if not _EVAL_REPORT_PATH.exists():
        return {"status": _NOT_APPLICABLE,
                "reason": "evaluation_report.json not found; run src/training/run_pipeline.py",
                "real_world": real_world}

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
        "root_cause_accuracy": (field_accuracy.root_cause_block(field) if field is not None else None) or {
            "status": _NOT_APPLICABLE,
            "reason": "no labeled root-cause ground truth in the XJTU-SY dataset "
                      "(run-to-failure bearing wear is documented, but specific "
                      "root causes are not labeled); no operator feedback has "
                      "labelled a root cause yet",
        },
        "real_world": real_world,
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


# ---------------------------------------------------------------------------
# System KPIs (M6) — design/M6_LIVE_TELEMETRY.md §9.
#
# Sourced ONLY from telemetry_messages (one row per MQTT snapshot the ingest
# service saw) and device_status (latest retained status per device). Nothing
# here touches readings/predictions, so these KPIs describe the transport
# path (device -> broker -> ingest), not the model.
#
# Timestamps: the contract stores UTC ISO-8601 strings, but writers may emit
# a 'Z' or '+00:00' suffix, with or without milliseconds. String comparison
# across those variants is only safe to the second, so SQL does a coarse
# prefilter (one second of slack on the shared 'YYYY-MM-DDTHH:MM:SS' prefix)
# and the exact window test is done in Python on parsed datetimes.
# ---------------------------------------------------------------------------

_SYSTEM_KPI_KEYS = (
    "sensor_collection_rate",
    "transmission_success_rate",
    "edge_buffer_health",
    "cloud_sync_health",
)
_NO_TELEMETRY_REASON = (
    "no live telemetry received yet — start the MQTT simulator (just simulate) "
    "or connect an ESP32 node"
)
# §4 online rule: src/telemetry/device_health.py (one definition shared with
# ingest, the devices route and the silence watchdog).
# §9 thresholds.
_BUFFER_CRITICAL_UTILIZATION = 0.9
_BUFFER_DEGRADED_UTILIZATION = 0.5
_SYNC_LAG_P95_DEGRADED_S = 30.0
_REJECTED_RATE_DEGRADED = 0.05
_BUFFER_STATE_RANK = {"ok": 0, "degraded": 1, "critical": 2}

# How far a device clock may run AHEAD of ours and still have its in-window
# capture times found. The SQL prefilter is on received_at alone — the only
# indexed timestamp (idx_telemetry_received) — because an `OR sampled_at`
# arm forces a full scan of a table that grows forever (one row per snapshot)
# and is polled every few seconds by the dashboard. That is safe because a
# snapshot is received after it is captured: sampled_at >= cutoff implies
# received_at >= cutoff, except for a device clock that is fast, which this
# slack absorbs. A clock more than 10 minutes fast is broken, not skewed.
_CLOCK_AHEAD_SLACK = timedelta(minutes=10)

_WINDOW_MESSAGES_SQL = """
    SELECT device_id, boot_id, seq, sampled_at, received_at, buffered,
           time_synced, status, reading_id
    FROM telemetry_messages
    WHERE received_at >= :lo
"""


def _parse_ts(value):
    """Stored ISO-8601 string -> aware UTC datetime (naive is read as UTC,
    matching src/telemetry/protocol.py). None for missing/unparseable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ratio(num, den):
    return (num / den) if den else None


def _round(value, digits=4):
    return None if value is None else round(value, digits)


def _delivered(m) -> bool:
    """Did this snapshot make it from the device into the database?

    `accepted`, obviously — but also `error` WITH a stored reading: ingest
    keeps the reading when only the model failed (no model file during a
    retrain, a predictor exception). These KPIs describe the transport path,
    so a model outage must not read as lost or uncollected snapshots. An
    error without a reading (feature extraction, DB failure) did not land
    and still counts against the path."""
    return m["status"] == "accepted" or (m["status"] == "error" and m["reading_id"] is not None)


def _load_window_messages(conn, cutoff, now) -> list:
    """Telemetry rows whose received_at OR sampled_at falls in [cutoff, now],
    with both timestamps parsed. The sampled_at test exists only for the
    collection rate (which windows on capture time); every message-based
    figure re-filters on in_recv_window. See _CLOCK_AHEAD_SLACK for why the
    SQL prefilter is on received_at only."""
    lo = (cutoff - _CLOCK_AHEAD_SLACK - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    for row in conn.execute(_WINDOW_MESSAGES_SQL, {"lo": lo}).fetchall():
        received = _parse_ts(row["received_at"])
        sampled = _parse_ts(row["sampled_at"])
        in_recv = received is not None and cutoff <= received <= now
        in_sampled = sampled is not None and cutoff <= sampled <= now
        if not (in_recv or in_sampled):
            continue
        rows.append({
            "device_id": row["device_id"],
            "boot_id": row["boot_id"],
            "seq": row["seq"],
            "received": received,
            "sampled": sampled,
            "in_recv_window": in_recv,
            "in_sampled_window": in_sampled,
            "buffered": bool(row["buffered"]),
            "time_synced": bool(row["time_synced"]),
            "status": row["status"],
            "reading_id": row["reading_id"],
        })
    return rows


def _load_device_status(conn) -> list:
    rows = conn.execute(
        """SELECT device_id, online, last_seen_at, snapshot_interval_s,
                  heartbeat_interval_s, buffer_depth, buffer_capacity,
                  buffer_dropped_total, publish_attempts_total,
                  publish_failures_total
           FROM device_status ORDER BY device_id"""
    ).fetchall()
    return [dict(r) for r in rows]


def _collection_rate(messages, statuses, *, cutoff, now, window_minutes) -> dict:
    """§9 sensor_collection_rate: captured snapshots that reached us vs. how
    many the device should have captured at its configured interval.

    A device is included if it sent anything in the window or its last status
    is in the window — the latter keeps a node whose sampling stalled (heartbeat
    alive, no snapshots) visible at rate 0 instead of silently dropping out of
    the fleet mean."""
    interval_by_device = {s["device_id"]: s["snapshot_interval_s"] for s in statuses}
    first_seen: dict = {}
    collected_keys: dict = {}
    for m in messages:
        dev = m["device_id"]
        if dev == "?":
            continue
        # "First message in window" = earliest in-window evidence the device
        # was capturing. A buffered flush arrives late, but its sampled_at
        # proves the device was already running, so both timestamps count —
        # otherwise a device that was mid-outage at window start would get a
        # shortened window and an inflated (then clamped) rate.
        candidates = []
        if m["in_recv_window"]:
            candidates.append(m["received"])
        if m["in_sampled_window"]:
            candidates.append(m["sampled"])
        earliest = min(candidates)
        if dev not in first_seen or earliest < first_seen[dev]:
            first_seen[dev] = earliest
        keys = collected_keys.setdefault(dev, set())
        if _delivered(m) and m["in_sampled_window"]:
            keys.add((m["boot_id"], m["seq"]))
    for s in statuses:
        last_seen = _parse_ts(s["last_seen_at"])
        if last_seen is not None and cutoff <= last_seen <= now:
            collected_keys.setdefault(s["device_id"], set())

    devices = []
    rates = []
    for dev in sorted(collected_keys):
        collected = len(collected_keys[dev])
        interval = interval_by_device.get(dev)
        start = max(cutoff, first_seen.get(dev, cutoff))
        effective_s = max((now - start).total_seconds(), 0.0)
        expected = rate = None
        if interval and interval > 0:
            expected = effective_s / interval
            if expected > 0:
                rate = min(max(collected / expected, 0.0), 1.0)
        if rate is not None:
            rates.append(rate)
        devices.append({
            "device_id": dev,
            "collected": collected,
            "expected": _round(expected, 2),
            "rate": _round(rate),
        })
    fleet = (sum(rates) / len(rates)) if rates else None
    return {"status": "available", "window_minutes": window_minutes,
            "rate": _round(fleet), "devices": devices}


def _transmission_rate(messages, statuses, *, window_minutes) -> dict:
    """§9 transmission_success_rate from seq gaps. seq is consumed at capture
    time, so a gap inside one (device_id, boot_id) run is a snapshot that was
    captured but never arrived. Runs are kept per boot because seq restarts
    on every reboot — pooling boots would invent huge fake gaps."""
    runs: dict = {}
    for m in messages:
        if not _delivered(m) or not m["in_recv_window"] or m["seq"] is None:
            continue
        runs.setdefault((m["device_id"], m["boot_id"]), set()).add(m["seq"])

    per_device: dict = {}
    for (dev, _boot), seqs in runs.items():
        agg = per_device.setdefault(dev, {"received": 0, "expected": 0, "boots": 0})
        agg["received"] += len(seqs)
        agg["expected"] += max(seqs) - min(seqs) + 1
        agg["boots"] += 1

    # Device-side view (cumulative since boot, from the latest status): counts
    # publishes the device itself saw fail, which seq gaps cannot distinguish
    # from edge-buffer drops.
    status_by_device = {s["device_id"]: s for s in statuses}
    attempts_total = sum(s["publish_attempts_total"] or 0 for s in statuses)
    failures_total = sum(s["publish_failures_total"] or 0 for s in statuses
                         if s["publish_attempts_total"])

    devices = []
    for dev in sorted(set(per_device) | set(status_by_device)):
        agg = per_device.get(dev, {"received": 0, "expected": 0, "boots": 0})
        status = status_by_device.get(dev) or {}
        devices.append({
            "device_id": dev,
            "boots": agg["boots"],
            "received": agg["received"],
            "expected": agg["expected"],
            "lost": agg["expected"] - agg["received"],
            "rate": _round(_ratio(agg["received"], agg["expected"])),
            "device_reported_failure_rate": _round(_ratio(
                status.get("publish_failures_total") or 0,
                status.get("publish_attempts_total") or 0,
            )),
        })

    received = sum(a["received"] for a in per_device.values())
    expected = sum(a["expected"] for a in per_device.values())
    return {
        "status": "available",
        "window_minutes": window_minutes,
        "rate": _round(_ratio(received, expected)),
        "received": received,
        "expected": expected,
        "lost": expected - received,
        "device_reported_failure_rate": _round(_ratio(failures_total, attempts_total)),
        "devices": devices,
    }


def _buffer_state(utilization, dropped_total, *, observable: bool = True) -> str:
    if not observable:
        return "unknown"
    if utilization is not None and utilization >= _BUFFER_CRITICAL_UTILIZATION:
        return "critical"
    if (utilization is not None and utilization >= _BUFFER_DEGRADED_UTILIZATION) or dropped_total > 0:
        return "degraded"
    return "ok"


def _edge_buffer_health(messages, statuses, *, window_minutes) -> dict:
    """§9 edge_buffer_health: the device's own view of its outage buffer
    (latest status) plus how much of what we accepted arrived late."""
    devices = []
    worst = None
    dropped_sum = 0
    for s in statuses:
        depth = s["buffer_depth"]
        capacity = s["buffer_capacity"]
        dropped = s["buffer_dropped_total"] or 0
        utilization = (depth / capacity) if (depth is not None and capacity) else None
        # Ingest creates a device_status row on a device's first SNAPSHOT
        # (buffer columns NULL) — so "has a row" does not mean "reported its
        # buffer". A device that never published buffer figures is
        # unobservable: "unknown", and kept out of the fleet's worst state
        # rather than counted as a healthy "ok".
        observable = capacity is not None or depth is not None or s["buffer_dropped_total"] is not None
        state = _buffer_state(utilization, dropped, observable=observable)
        if observable and (worst is None or _BUFFER_STATE_RANK[state] > _BUFFER_STATE_RANK[worst]):
            worst = state
        dropped_sum += dropped
        devices.append({
            "device_id": s["device_id"],
            "depth": depth,
            "capacity": capacity,
            "utilization": _round(utilization),
            "dropped_total": dropped,
            "state": state,
        })

    delivered = [m for m in messages if _delivered(m) and m["in_recv_window"]]
    buffered = sum(1 for m in delivered if m["buffered"])
    return {
        "status": "available",
        "window_minutes": window_minutes,
        # Telemetry but no device ever reported its buffer means the buffer
        # is unobservable — say so rather than claiming "ok".
        "state": worst or "unknown",
        "buffered_share": _round(_ratio(buffered, len(delivered))),
        "dropped_total": dropped_sum,
        "devices": devices,
    }


def _device_online(status, last_message_at, now) -> bool:
    """§4 online boolean via the shared rule (device_health): latest status
    says online AND the device has been heard from (any message) within 3
    heartbeats, i.e. state online or stale. last_seen_at is the ingest's
    record of "any message"; the newest in-window telemetry row is also
    passed so a lagging status upsert cannot mark a live device offline."""
    return device_health.is_heard(device_health.device_state(
        status["online"], status["last_seen_at"], status["heartbeat_interval_s"],
        reported=True, now=now, last_message_at=last_message_at,
    ))


def _cloud_sync_health(conn, messages, statuses, *, now, window_minutes) -> dict:
    """§9 cloud_sync_health: lag of live (non-buffered, clock-synced)
    snapshots, device liveness, staleness and rejection rate. Buffered and
    unsynced messages are excluded from lag on purpose: a buffered flush is
    late by design (edge_buffer_health covers it), and an unsynced device's
    sampled_at is just its receive time (lag would read as a fake 0)."""
    in_window = [m for m in messages if m["in_recv_window"]]
    lags = sorted(
        (m["received"] - m["sampled"]).total_seconds()
        for m in in_window
        if _delivered(m) and not m["buffered"] and m["time_synced"]
        and m["sampled"] is not None
    )
    lag_p50 = _percentile(lags, 50.0)
    lag_p95 = _percentile(lags, 95.0)

    last_by_device: dict = {}
    for m in in_window:
        prev = last_by_device.get(m["device_id"])
        if prev is None or m["received"] > prev:
            last_by_device[m["device_id"]] = m["received"]
    online = sum(
        1 for s in statuses if _device_online(s, last_by_device.get(s["device_id"]), now)
    )
    total = len(statuses)

    # Staleness is over ALL time, not the window: "silent for 3 hours" must
    # still be reportable with a 60-minute window. A few top rows are parsed
    # so a mixed 'Z'/'+00:00' suffix cannot pick the wrong maximum.
    last_message_at = None
    for row in conn.execute(
        "SELECT received_at FROM telemetry_messages ORDER BY received_at DESC LIMIT 5"
    ).fetchall():
        parsed = _parse_ts(row["received_at"])
        if parsed is not None and (last_message_at is None or parsed > last_message_at):
            last_message_at = parsed
    last_age = (now - last_message_at).total_seconds() if last_message_at else None

    rejected = sum(1 for m in in_window if m["status"] == "rejected")
    rejected_rate = (rejected / len(in_window)) if in_window else 0.0

    if online == 0:
        state = "down"
    elif (online < total
          or (lag_p95 is not None and lag_p95 > _SYNC_LAG_P95_DEGRADED_S)
          or rejected_rate > _REJECTED_RATE_DEGRADED):
        state = "degraded"
    else:
        state = "ok"

    return {
        "status": "available",
        "window_minutes": window_minutes,
        "state": state,
        "lag_p50_s": _round(lag_p50, 3),
        "lag_p95_s": _round(lag_p95, 3),
        "devices_online": online,
        "devices_total": total,
        "last_message_age_s": _round(last_age, 3),
        "rejected_rate": _round(rejected_rate),
    }


# Shared with src/feedback/accuracy.py; kept under the old name so existing
# callers here are untouched.
_table_exists = table_exists


def system_kpis(conn, *, now=None, window_minutes: float = 60.0) -> dict:
    """System-performance KPIs over live MQTT telemetry (contract §9).

    not_applicable until the first telemetry message or device status has
    ever been stored — a dataset-only deployment keeps reporting honestly that
    there is no live source rather than showing zeros. The response keys are
    the same in both cases so the KPI contract never changes shape."""
    # A pre-M6 (schema v1) DB has no telemetry tables until live ingest first
    # migrates it — that is still "no live telemetry", not a server error.
    if not (_table_exists(conn, "telemetry_messages") and _table_exists(conn, "device_status")):
        return {key: {"status": _NOT_APPLICABLE, "reason": _NO_TELEMETRY_REASON}
                for key in _SYSTEM_KPI_KEYS}
    has_messages = conn.execute("SELECT 1 FROM telemetry_messages LIMIT 1").fetchone()
    has_status = conn.execute("SELECT 1 FROM device_status LIMIT 1").fetchone()
    if not has_messages and not has_status:
        return {key: {"status": _NOT_APPLICABLE, "reason": _NO_TELEMETRY_REASON}
                for key in _SYSTEM_KPI_KEYS}

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(minutes=window_minutes)

    messages = _load_window_messages(conn, cutoff, now)
    statuses = _load_device_status(conn)
    return {
        "sensor_collection_rate": _collection_rate(
            messages, statuses, cutoff=cutoff, now=now, window_minutes=window_minutes),
        "transmission_success_rate": _transmission_rate(
            messages, statuses, window_minutes=window_minutes),
        "edge_buffer_health": _edge_buffer_health(
            messages, statuses, window_minutes=window_minutes),
        "cloud_sync_health": _cloud_sync_health(
            conn, messages, statuses, now=now, window_minutes=window_minutes),
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

    open_work_orders = 0
    if _table_exists(conn, "work_orders"):
        open_work_orders = conn.execute(
            f"SELECT COUNT(*) AS n FROM work_orders WHERE status IN {_ACTIVE_WORK_ORDER_STATUSES}"
        ).fetchone()["n"]

    return {
        "machine_count": total,
        "health_state_counts": by_state,
        "open_alert_count": open_alerts,
        "machines_due_for_inspection": due,
        "prediction": prediction_kpis(conn),
        "open_work_order_count": open_work_orders,
    }
