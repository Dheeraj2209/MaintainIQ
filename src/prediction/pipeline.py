"""What happens *after* a model prediction: alerts, paging, dashboards.

This fan-out once lived inline in src/api/routes/demo.py, so the one path
that produced alerts and emails was the synthetic demo injection — the real
model's predictions dead-ended in the `predictions` table and no operator
ever heard about them. This module is that fan-out extracted, so the replay
ingestion path, live MQTT telemetry and (through fan_out) the demo routes all
drive the exact same alert state machine, the same emails, and the same
realtime events. On a create or severity escalation, fan_out also snapshots
the alert's explanation (src/root_cause/explain.py) before paging and
broadcasting, so "Why this alert?" survives restarts and the escalation
overwrite (design/2026-10-07-alert-explanation-design.md, decision 2).

Deliberately decoupled from persistence: callers write the prediction and
inference-log rows themselves (src/prediction/rul_store.py) and then call
handle_prediction for the downstream effects. Keeping them separate is what
lets a caller persist without paging — e.g. a historical backfill.
"""
import logging

import pandas as pd

from src.alerts.live import apply_reading
from src.notifications.dispatch import notify_alert
from src.root_cause import explain
from src.root_cause.rule_based import ABNORMAL_STATES, classify_probable_cause

logger = logging.getLogger(__name__)

# Events that mean "something got worse" and are worth an email (and a push).
# A resolution is good news; paging on it trains people to ignore the channel.
_PAGING_EVENTS = {"alert_created", "alert_escalated"}

# Events whose evidence is snapshotted, and the alert_explanations.kind each
# writes. Resolutions and no-ops are not explained.
_SNAPSHOT_EVENTS = {"alert_created": "created", "alert_escalated": "escalated"}

# readings.features_json uses the short channel names the feature extractor
# emits; classify_probable_cause reads the canonical column names. Same
# quantities, two naming conventions, so translate rather than duplicate the
# classifier.
_FEATURE_ALIASES = {
    "h_rms": "vibration_h_rms",
    "h_kurtosis": "vibration_h_kurtosis",
}


def _probable_cause(health_state: str, features: dict) -> str | None:
    """None for a healthy reading — there is no root cause for "fine"."""
    if health_state not in ABNORMAL_STATES:
        return None

    row = {canonical: features.get(short) for short, canonical in _FEATURE_ALIASES.items()}
    return classify_probable_cause(pd.Series(row))


def handle_prediction(
    conn,
    result: dict,
    *,
    source: str,
    features: dict | None = None,
    timestamp: str | None = None,
    broadcast=None,
    prediction_id: int | None = None,
    reading_id: int | None = None,
) -> dict:
    """Run one prediction through alerting, paging and the realtime feed.

    `result` is a RealTimeRULPredictor result dict; `features` is the raw
    feature vector behind it (used only for root-cause classification).
    `prediction_id` / `reading_id` are the rows rul_store.persist_prediction
    and the caller wrote; together with result["model_version"] they are
    stored on an alert this prediction opens, so feedback on the alert can be
    joined to the exact prediction (feedback design, decision 3). They stay
    off the broadcast `prediction`, whose shape is RULPredictionResponse.
    Returns {"event", "alert", "emails_sent"} — event is None when this
    prediction did not change the machine's alert state, which is the common
    case during steady operation.

    Email and broadcast failures are logged and swallowed: a mail server
    hiccup or a dropped websocket must not cost us the alert itself, which is
    already committed by the time either runs.
    """
    machine_id = result["machine_id"]
    health_state = result["health_state"]
    timestamp = timestamp or result.get("timestamp") or _now_iso()

    applied = apply_reading(
        conn,
        machine_id,
        health_state,
        _probable_cause(health_state, features or {}),
        source,
        timestamp,
        prediction_id=prediction_id,
        reading_id=reading_id,
        model_version=result.get("model_version"),
    )
    # Minus "input_features": the predictor carries the raw feature vector on
    # its result only so _probable_cause above can read it. It is not in
    # RULPredictionResponse, so it does not go on the wire.
    prediction = {k: v for k, v in result.items() if k != "input_features"}
    return fan_out(conn, applied, at=timestamp, prediction=prediction, broadcast=broadcast,
                   features=features, reading_id=reading_id, prediction_id=prediction_id)


def fan_out(conn, applied, *, at: str, prediction: dict | None = None, broadcast=None,
            features: dict | None = None, reading_id: int | None = None,
            prediction_id: int | None = None) -> dict:
    """Everything after the alert state machine moved: the explanation
    snapshot, the level-0 page, its last_paged_at stamp, and the realtime
    event.

    `applied` is apply_reading's return value (None ⇒ nothing to do). Shared
    by handle_prediction and the demo routes (work-orders design, decision
    9), so a change to the fan-out is made once. `broadcast` defaults to the
    realtime manager's thread-safe broadcast, so callers may run on any
    thread. `features` / `reading_id` / `prediction_id` are the evidence
    behind this transition, used by the snapshot only. Returns {"event",
    "alert", "emails_sent"}.
    """
    if applied is None:
        return {"event": None, "alert": None, "emails_sent": 0}
    if broadcast is None:
        from src.realtime.manager import manager

        broadcast = manager.broadcast_threadsafe

    event_type, alert = applied
    machine_id = alert["machine_id"]

    # Before paging and broadcast, so "Why?" from the toast finds it. Its own
    # insert, never under live._TRANSITION_LOCK; a failure never undoes the
    # committed alert nor stops the page.
    if event_type in _SNAPSHOT_EVENTS:
        try:
            explain.record_snapshot(
                conn, alert, _SNAPSHOT_EVENTS[event_type], reading_id=reading_id,
                prediction_id=prediction_id, prediction=prediction, features=features, at=at,
                predictor=explain.loaded_predictor(),
            )
        except Exception:
            logger.exception("Failed to snapshot the explanation of alert %s", alert.get("id"))

    emails_sent = 0
    if event_type in _PAGING_EVENTS:
        try:
            emails_sent = notify_alert(conn, alert)
        except Exception:
            logger.exception("Failed to send alert email for %s", machine_id)
        # Level-0 pages are stamped too (decision 8), so "last paged" covers
        # every level. A write-only UPDATE: failing it never undoes the alert.
        paged_at = _now_iso()
        try:
            _stamp_paged(conn, alert["id"], paged_at)
            alert["last_paged_at"] = paged_at
        except Exception:
            logger.exception("Failed to record the page time of alert %s", alert.get("id"))

    event = {
        "type": event_type,
        "machine_id": machine_id,
        # "at", not "timestamp": this is the wire contract the dashboard
        # reads (frontend/src/api/types.ts LiveEvent).
        "at": at,
        "alert": alert,
    }
    if prediction is not None:
        event["prediction"] = prediction
    try:
        broadcast(event)
    except Exception:
        logger.exception("Failed to broadcast %s for %s", event_type, machine_id)

    return {"event": event_type, "alert": alert, "emails_sent": emails_sent}


def _stamp_paged(conn, alert_id: int, at: str) -> None:
    conn.execute("UPDATE alerts SET last_paged_at = ? WHERE id = ?", (at, alert_id))
    conn.commit()


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
