""""Why this alert?" — the evidence behind an alert
(design/2026-10-07-alert-explanation-design.md).

An explanation has five sections: the triggering readings, the key factors
(vibration features furthest from the machine's own baseline, weighted by how
much the model relies on them), the model prediction, the probable cause with
the rule trace that produced it, and similar past incidents with what
actually happened.

The first four are snapshotted into alert_explanations when an alert is
created and at each severity escalation (record_snapshot, called from
src/prediction/pipeline.fan_out): the predictor's rolling context lives in
memory only, and an escalation overwrites the alert's probable_cause.
Similar incidents are never stored — feedback and work orders keep arriving —
so get_explanation always computes them at read time, and rebuilds the whole
thing from stored data (source "reconstructed") for alerts with no usable
snapshot. A GET never writes.

Every section is built in its own try/except: the explanation is advisory, so
one malformed features_json must not hide the probable cause or the history.

The ML modules are only read here: the training constants and
add_past_context from src.training.xjtu_rul, and a loaded predictor's
artifact / classifiers / classifier_feature_columns. The predictor is never
loaded by this module (loaded_predictor); without it, key factors are
unweighted and training bounds are unknown.

Wording is always "probable cause", never a diagnosis (SRS FR-25/NFR-13).
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import weakref
from datetime import datetime, timezone

import pandas as pd

from src.alerts import live
from src.root_cause import rule_based
from src.storage.db import table_exists
from src.training.xjtu_rul import (
    BASELINE_WINDOW,
    ROLLING_SOURCE_COLUMNS,
    ROLLING_WINDOWS,
    add_past_context,
)

logger = logging.getLogger(__name__)

EXPLANATION_VERSION = 1
# Readings shown in the trigger chart, ending at (and including) the trigger.
TRIGGER_WINDOW_READINGS = 30
# Fewer earlier readings than this and there is no baseline worth comparing to.
MIN_BASELINE_READINGS = 5
KEY_FACTOR_LIMIT = 6
SIMILAR_DEFAULT_LIMIT, SIMILAR_MAX_LIMIT = 5, 20
# The recent slice fed to add_past_context next to the commissioning baseline.
# It must cover the longest rolling window, or the trigger row's rolling
# statistics would differ from what the model computed.
RECENT_CONTEXT_READINGS = max(ROLLING_WINDOWS)
# Similar-incident cap before scoring: the most recent candidates only.
SIMILAR_CANDIDATE_CAP = 500

# Similarity weights (decision 9); they sum to 1.
_W_CAUSE, _W_MACHINE, _W_SEVERITY, _W_RECENCY = 0.5, 0.3, 0.1, 0.1
_RECENCY_DAYS = 90.0
_NOTES_LIMIT = 280

DISCLAIMER = ("Heuristic rule on vibration features — a probable cause, not a diagnosis. "
              "Confirm on inspection.")

# The six promoted vibration columns, present for every dataset (demo
# included). No temperature, and no operating conditions in the chart.
_CHANNELS = (
    ("vibration_h_rms", "Horizontal RMS", "g"),
    ("vibration_h_kurtosis", "Horizontal kurtosis", None),
    ("vibration_v_rms", "Vertical RMS", "g"),
    ("vibration_v_kurtosis", "Vertical kurtosis", None),
    ("cross_axis_rms_ratio", "Cross-axis RMS ratio (h/v)", None),
    ("cross_axis_correlation", "Cross-axis correlation", None),
)
_CHANNEL_KEYS = tuple(key for key, _, _ in _CHANNELS)
# Promoted column -> features_json short key (same table as
# src/telemetry/ingest.py's _SUMMARY_COLUMNS).
_PROMOTED_SHORT = {
    "vibration_h_rms": "h_rms",
    "vibration_h_kurtosis": "h_kurtosis",
    "vibration_v_rms": "v_rms",
    "vibration_v_kurtosis": "v_kurtosis",
    "cross_axis_rms_ratio": "cross_axis_rms_ratio",
    "cross_axis_correlation": "cross_axis_correlation",
}
# Fallback candidates when features_json is empty or incomplete.
_PROMOTED_CANDIDATES = ("h_rms", "h_kurtosis", "v_rms", "v_kurtosis")

_AXES = {"h": ("Horizontal", "horizontal"), "v": ("Vertical", "vertical"),
         "m": ("Combined (h+v magnitude)", "combined")}
_METRICS = {
    "rms": ("RMS vibration", "g"),
    "peak": ("Peak vibration", "g"),
    "kurtosis": ("Kurtosis", None),
    "envelope_rms": ("Envelope RMS", "g"),
    "envelope_kurtosis": ("Envelope kurtosis", None),
    "spectral_entropy": ("Spectral entropy", None),
    "envelope_energy_100_200_hz_ratio": ("Envelope energy share, 100–200 Hz", "fraction"),
}
_OPERATING_CONDITIONS = (("speed_rpm", "Shaft speed", "rpm"), ("load_kn", "Radial load", "kN"))

_READING_COLUMNS = ("id, timestamp, cycle, speed_rpm, load_kn, features_json, "
                    + ", ".join(_CHANNEL_KEYS))


# --- predictor access ------------------------------------------------------------------

def loaded_predictor():
    """The API's cached RealTimeRULPredictor if something already loaded it,
    else None. Never loads it: that costs seconds and raises when no model is
    trained, and an explanation must stay fast and never fail (decision 3)."""
    try:
        from src.api.routes.predictions import _cached_predictor
    except Exception:
        logger.debug("Predictor cache unavailable", exc_info=True)
        return None
    try:
        if _cached_predictor.cache_info().currsize > 0:
            return _cached_predictor()
    except Exception:
        logger.debug("Predictor cache unreadable", exc_info=True)
    return None


def _source_of(feature: str, candidates=ROLLING_SOURCE_COLUMNS):
    """The longest candidate that `feature` is or derives from, else None."""
    best = None
    for source in candidates:
        if feature == source or feature.startswith(source + "_"):
            if best is None or len(source) > len(best):
                best = source
    return best


_importance_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_importance_lock = threading.Lock()


def _source_importances(predictor) -> dict | None:
    """Mean feature_importances_ across the predictor's classifiers, summed
    per ROLLING_SOURCE_COLUMNS source; None when they can't be read. Cached
    per predictor and model version: each access averages hundreds of trees."""
    if predictor is None:
        return None
    version = (getattr(predictor, "artifact", None) or {}).get("model_version")
    with _importance_lock:
        try:
            cached = _importance_cache.get(predictor)
        except TypeError:
            cached = None
        if cached is not None and cached[0] == version:
            return cached[1]
        sums = _compute_source_importances(predictor)
        try:
            _importance_cache[predictor] = (version, sums)
        except TypeError:
            pass  # not weak-referenceable: just don't cache
        return sums


def _compute_source_importances(predictor) -> dict | None:
    columns = list(getattr(predictor, "classifier_feature_columns", None) or [])
    classifiers = list(getattr(predictor, "classifiers", None) or [])
    if not columns or not classifiers:
        return None
    vectors = []
    for classifier in classifiers:
        estimator = getattr(classifier, "named_steps", {}).get("classifier", classifier)
        importances = getattr(estimator, "feature_importances_", None)
        if importances is None or len(importances) != len(columns):
            return None
        vectors.append([float(v) for v in importances])
    mean = [sum(values) / len(vectors) for values in zip(*vectors)]
    sums: dict[str, float] = {}
    for feature, importance in zip(columns, mean):
        source = _source_of(feature)
        if source is not None:
            sums[source] = sums.get(source, 0.0) + importance
    return sums


def _bounds(predictor) -> dict | None:
    if predictor is None:
        return None
    bounds = (getattr(predictor, "artifact", None) or {}).get("feature_bounds_99pct")
    return bounds or None


def _outside(value, bound) -> bool | None:
    if value is None or bound is None:
        return None
    low, high = bound
    return bool(value < low or value > high)


# --- helpers ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value):
    """A JSON-safe float, or None for missing/NaN/inf."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean(obj):
    """Recursively turn numpy scalars and non-finite floats into JSON-safe values."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    if hasattr(obj, "item"):  # numpy scalar
        return _clean(obj.item())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def _features_of(row) -> dict:
    try:
        parsed = json.loads(row["features_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _complete(features: dict) -> bool:
    return all(_finite(features.get(c)) is not None for c in ROLLING_SOURCE_COLUMNS)


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- trigger ---------------------------------------------------------------------------

class _Trigger:
    """The reading behind a snapshot. `row` is a stored readings row (a dict),
    or a synthetic dict for an unsaved trigger built from in-memory features;
    `locate` says how it was found."""

    def __init__(self, row: dict | None, locate: str, unsaved: bool = False):
        self.row = row
        self.locate = locate
        self.unsaved = unsaved


def _reading(conn, reading_id):
    row = conn.execute(f"SELECT {_READING_COLUMNS} FROM readings WHERE id = ?", (reading_id,)).fetchone()
    return dict(row) if row is not None else None


def _linked_reading(conn, candidate, notes, missing: set):
    """The stored reading `candidate`, or None (noting a dangling link once)."""
    if candidate is None or candidate in missing:
        return None
    row = _reading(conn, candidate)
    if row is not None and row.get("timestamp") is not None:
        return row
    missing.add(candidate)
    notes.append(f"Linked reading #{candidate} no longer exists; the nearest reading is shown.")
    return None


def _prediction_reading_id(conn, alert: dict, prediction_id, when):
    """reading_id of the prediction behind the alert: the linked one, else the
    machine's latest at or before `when` (the row _prediction_block shows).
    Replay predictions carry the exact reading even when the alert does not,
    and their wall-clock timestamps match opened_at where readings' do not."""
    for candidate in dict.fromkeys((prediction_id, alert.get("prediction_id"))):
        if candidate is None:
            continue
        row = conn.execute("SELECT reading_id FROM predictions WHERE id = ?", (candidate,)).fetchone()
        if row is not None:
            return row["reading_id"]
    row = conn.execute(
        """SELECT reading_id FROM predictions WHERE machine_id = ? AND timestamp <= ?
           ORDER BY timestamp DESC, id DESC LIMIT 1""", (alert["machine_id"], when)).fetchone()
    return row["reading_id"] if row is not None else None


def _locate_trigger(conn, alert: dict, reading_id, features, at, notes,
                    prediction_id=None) -> _Trigger:
    """The snapshot's own reading, else the features the model scored (an
    escalation via POST /api/predictions/rul must not borrow the opening
    reading), else alerts.reading_id, else the prediction's reading, else the
    nearest reading by timestamp."""
    machine_id = alert["machine_id"]
    missing: set = set()
    row = _linked_reading(conn, reading_id, notes, missing)
    if row is not None:
        return _Trigger(row, "reading_id")

    when = at or alert["opened_at"]
    if features:
        # A reading-less snapshot (POST /api/predictions/rul stores no
        # reading): what the model saw becomes the trigger point.
        promoted = {column: _finite(features.get(short)) for column, short in _PROMOTED_SHORT.items()}
        last = conn.execute("SELECT MAX(cycle) AS c FROM readings WHERE machine_id = ?",
                            (machine_id,)).fetchone()
        cycle = (last["c"] + 1) if last is not None and last["c"] is not None else None
        row = {"id": None, "timestamp": when, "cycle": cycle, "speed_rpm": None, "load_kn": None,
               "features": dict(features), **promoted}
        return _Trigger(row, "snapshot_features", unsaved=True)

    row = _linked_reading(conn, alert.get("reading_id"), notes, missing)
    if row is not None:
        return _Trigger(row, "reading_id")

    row = _linked_reading(conn, _prediction_reading_id(conn, alert, prediction_id, when), notes, missing)
    if row is not None:
        return _Trigger(row, "prediction_reading")

    row = conn.execute(
        f"""SELECT {_READING_COLUMNS} FROM readings WHERE machine_id = ? AND timestamp <= ?
            ORDER BY timestamp DESC, id DESC LIMIT 1""", (machine_id, when)).fetchone()
    if row is None:
        row = conn.execute(
            f"""SELECT {_READING_COLUMNS} FROM readings WHERE machine_id = ?
                ORDER BY timestamp ASC, id ASC LIMIT 1""", (machine_id,)).fetchone()
    if row is None:
        notes.append("No readings are stored for this machine.")
        return _Trigger(None, "none")
    notes.append("No reading is linked to this alert; the nearest reading to the alert time is shown.")
    return _Trigger(dict(row), "nearest_timestamp")


def _earlier_rows(conn, machine_id: str, trigger: _Trigger, limit: int | None = None,
                  oldest_first: bool = False) -> list[dict]:
    """Stored readings strictly before the trigger in (timestamp, id) order.
    Newest `limit` by default, or the oldest `limit` with oldest_first."""
    row = trigger.row
    if trigger.unsaved:
        where, params = "machine_id = ? AND timestamp <= ?", [machine_id, row["timestamp"]]
    else:
        where = "machine_id = ? AND (timestamp < ? OR (timestamp = ? AND id < ?))"
        params = [machine_id, row["timestamp"], row["timestamp"], row["id"]]
    order = "timestamp ASC, id ASC" if oldest_first else "timestamp DESC, id DESC"
    sql = f"SELECT {_READING_COLUMNS} FROM readings WHERE {where} ORDER BY {order}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params)]
    return rows if oldest_first else list(reversed(rows))


def _count_earlier(conn, machine_id: str, trigger: _Trigger) -> int:
    row = trigger.row
    if trigger.unsaved:
        sql = "SELECT COUNT(*) FROM readings WHERE machine_id = ? AND timestamp <= ?"
        params = (machine_id, row["timestamp"])
    else:
        sql = ("SELECT COUNT(*) FROM readings WHERE machine_id = ? "
               "AND (timestamp < ? OR (timestamp = ? AND id < ?))")
        params = (machine_id, row["timestamp"], row["timestamp"], row["id"])
    return int(conn.execute(sql, params).fetchone()[0])


# --- sections --------------------------------------------------------------------------

def _chart_point(row: dict, is_trigger: bool) -> dict:
    point = {"reading_id": row.get("id"), "timestamp": row["timestamp"], "cycle": row.get("cycle"),
             "is_trigger": is_trigger}
    for key in _CHANNEL_KEYS:
        point[key] = _finite(row.get(key))
    return point


def _triggering_readings(conn, alert: dict, trigger: _Trigger) -> dict:
    section = {
        "locate": trigger.locate,
        "trigger_reading_id": None,
        "trigger_timestamp": None,
        "channels": [{"key": key, "label": label, "unit": unit} for key, label, unit in _CHANNELS],
        "readings": [],
    }
    if trigger.row is None:
        return section
    earlier = _earlier_rows(conn, alert["machine_id"], trigger, limit=TRIGGER_WINDOW_READINGS - 1)
    section["trigger_reading_id"] = trigger.row.get("id")
    section["trigger_timestamp"] = trigger.row["timestamp"]
    section["readings"] = [_chart_point(r, False) for r in earlier] + [_chart_point(trigger.row, True)]
    return section


def _factor_label(source: str):
    axis_key, metric_key = source.split("_", 1)
    axis_label, axis = _AXES[axis_key]
    metric_label, unit = _METRICS[metric_key]
    if not metric_label.split()[0].isupper():
        metric_label = metric_label[0].lower() + metric_label[1:]
    return f"{axis_label} {metric_label}", axis, unit


def _direction(ratio: float) -> str:
    if ratio >= 1.05:
        return "up"
    if ratio <= 0.95:
        return "down"
    return "steady"


def _factor(source: str, value, baseline, importances: dict | None) -> dict | None:
    value, baseline = _finite(value), _finite(baseline)
    if value is None or baseline is None:
        return None
    ratio = value / max(abs(baseline), 1e-12)  # add_past_context's ratio
    deviation = abs(math.log(min(max(ratio, 1e-6), 1e6)))
    importance = None if importances is None else importances.get(source, 0.0)
    label, axis, unit = _factor_label(source)
    return {
        "feature": source, "label": label, "axis": axis, "unit": unit,
        "value": value, "baseline": baseline, "ratio": ratio, "direction": _direction(ratio),
        "deviation": deviation, "importance": importance,
        "score": deviation if importance is None else deviation * importance,
        "outside_training_bounds": None, "out_of_bounds_features": [],
    }


def _normalised(sums: dict | None, candidates) -> dict | None:
    if sums is None:
        return None
    total = sum(sums.get(c, 0.0) for c in candidates)
    if total <= 0:
        return None
    return {c: sums.get(c, 0.0) / total for c in candidates}


def _rank(factors: list[dict]) -> list[dict]:
    factors = [f for f in factors if f is not None]
    factors.sort(key=lambda f: (-f["score"], -f["deviation"], f["feature"]))
    return factors[:KEY_FACTOR_LIMIT]


def _operating_conditions(trigger_row: dict, bounds: dict | None) -> list[dict]:
    conditions = []
    for feature, label, unit in _OPERATING_CONDITIONS:
        value = _finite(trigger_row.get(feature))
        conditions.append({
            "feature": feature, "label": label, "unit": unit, "value": value,
            "outside_training_bounds": _outside(value, (bounds or {}).get(feature)) if bounds else None,
        })
    return conditions


def _key_factors(conn, alert: dict, trigger: _Trigger, predictor) -> dict:
    empty = {"status": "no_readings", "method": None, "weighting": None, "baseline_readings": 0,
             "factors": [], "operating_conditions": []}
    if trigger.row is None:
        return empty
    machine_id = alert["machine_id"]
    trigger_features = trigger.row.get("features") if trigger.unsaved else _features_of(trigger.row)
    bounds = _bounds(predictor)
    conditions = _operating_conditions(trigger.row, bounds)
    earlier_count = _count_earlier(conn, machine_id, trigger)

    if _complete(trigger_features or {}) and earlier_count >= BASELINE_WINDOW:
        section = _model_context_factors(conn, machine_id, trigger, trigger_features, predictor, bounds)
        if section is not None:
            section["operating_conditions"] = conditions
            return section

    if earlier_count < MIN_BASELINE_READINGS:
        return {**empty, "status": "insufficient_history", "baseline_readings": earlier_count,
                "operating_conditions": conditions}

    # promoted_columns: the four promoted channels against the median of the
    # machine's first min(20, n) readings — add_past_context's baseline
    # definition, frozen at BASELINE_WINDOW.
    first = _earlier_rows(conn, machine_id, trigger, limit=BASELINE_WINDOW, oldest_first=True)
    importances = _normalised(_source_importances(predictor), _PROMOTED_CANDIDATES)
    factors = []
    for column, short in _PROMOTED_SHORT.items():
        if short not in _PROMOTED_CANDIDATES:
            continue
        history = [v for v in (_finite(r.get(column)) for r in first) if v is not None]
        if not history:
            continue
        baseline = float(pd.Series(history).median())
        factors.append(_factor(short, trigger.row.get(column), baseline, importances))
    return {
        "status": "ok", "method": "promoted_columns",
        "weighting": "unweighted" if importances is None else "model_importance",
        "baseline_readings": len(first), "factors": _rank(factors),
        "operating_conditions": conditions,
    }


def _model_context_factors(conn, machine_id, trigger, trigger_features, predictor, bounds):
    """Rebuild the model's context frame — commissioning baseline plus the
    recent rolling window — and read each source's baseline ratio off the
    trigger row, exactly as the predictor computed it. None when fewer than
    BASELINE_WINDOW earlier readings carry a full feature vector."""
    first = [r for r in _earlier_rows(conn, machine_id, trigger, oldest_first=True)
             if _complete(_features_of(r))][:BASELINE_WINDOW]
    if len(first) < BASELINE_WINDOW:
        return None
    recent = [r for r in _earlier_rows(conn, machine_id, trigger, limit=RECENT_CONTEXT_READINGS - 1)
              if _complete(_features_of(r))]
    trigger_cycle = trigger.row.get("cycle")
    if trigger_cycle is None:
        trigger_cycle = max(r["cycle"] for r in first + recent) + 1

    def frame_row(row, features, cycle):
        return {"bearing_id": machine_id, "cycle": cycle, "speed_rpm": row.get("speed_rpm"),
                "load_kn": row.get("load_kn"),
                **{c: float(features[c]) for c in ROLLING_SOURCE_COLUMNS}}

    rows = {r["cycle"]: frame_row(r, _features_of(r), r["cycle"]) for r in [*first, *recent]}
    rows[trigger_cycle] = frame_row(trigger.row, trigger_features, trigger_cycle)
    context = add_past_context(pd.DataFrame(list(rows.values())))
    latest = context.loc[context["cycle"] == trigger_cycle].iloc[0]

    importances = _normalised(_source_importances(predictor), ROLLING_SOURCE_COLUMNS)
    by_source: dict[str, list[str]] = {}
    for feature in (bounds or {}):
        source = _source_of(feature)
        if source is not None and feature in latest.index:
            by_source.setdefault(source, []).append(feature)

    factors = []
    for source in ROLLING_SOURCE_COLUMNS:
        value = latest[source]
        baseline = value - latest[f"{source}_baseline_delta"]
        factor = _factor(source, value, baseline, importances)
        if factor is None:
            continue
        if bounds is not None:
            outside = [f for f in sorted(by_source.get(source, []))
                       if _outside(_finite(latest[f]), bounds[f])]
            factor["outside_training_bounds"] = bool(outside)
            factor["out_of_bounds_features"] = outside
        factors.append(factor)
    return {
        "status": "ok", "method": "model_context",
        "weighting": "unweighted" if importances is None else "model_importance",
        "baseline_readings": BASELINE_WINDOW, "factors": _rank(factors),
    }


_PREDICTION_FIELDS = ("model_version", "health_state", "failure_within_horizon_probability",
                      "predicted_rul_minutes", "rul_estimate_kind", "prognostic_horizon_minutes")


def _decode_warnings(raw) -> list[str]:
    try:
        parsed = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(w) for w in parsed] if isinstance(parsed, list) else []


def _from_prediction_row(row, locate: str) -> dict:
    row = dict(row)
    block = {"locate": locate, "prediction_id": row["id"], "timestamp": row["timestamp"]}
    for field in _PREDICTION_FIELDS:
        block[field] = row.get(field)
    block["prediction_interval_low"] = _finite(row.get("prediction_interval_low"))
    block["prediction_interval_high"] = _finite(row.get("prediction_interval_high"))
    block["out_of_distribution"] = bool(row.get("out_of_distribution"))
    # The predictions table doesn't store outside_training_features; only a
    # snapshot of the live result dict keeps them.
    block["outside_training_features"] = []
    block["warnings"] = _decode_warnings(row.get("warnings_json"))
    return block


def _prediction_block(conn, alert: dict, prediction: dict | None, prediction_id, at) -> dict | None:
    if prediction is not None:
        interval = prediction.get("prediction_interval_90_minutes") or [None, None]
        block = {"locate": "snapshot", "prediction_id": prediction_id,
                 "timestamp": prediction.get("timestamp") or at}
        for field in _PREDICTION_FIELDS:
            block[field] = prediction.get(field)
        block["prediction_interval_low"] = _finite(interval[0]) if len(interval) > 0 else None
        block["prediction_interval_high"] = _finite(interval[1]) if len(interval) > 1 else None
        block["out_of_distribution"] = bool(prediction.get("out_of_distribution", False))
        block["outside_training_features"] = [str(f) for f in prediction.get("outside_training_features") or []]
        block["warnings"] = [str(w) for w in prediction.get("warnings") or []]
        return block

    for candidate in (prediction_id, alert.get("prediction_id")):
        if candidate is None:
            continue
        row = conn.execute("SELECT * FROM predictions WHERE id = ?", (candidate,)).fetchone()
        if row is not None:
            return _from_prediction_row(row, "prediction_id")

    row = conn.execute(
        """SELECT * FROM predictions WHERE machine_id = ? AND timestamp <= ?
           ORDER BY timestamp DESC, id DESC LIMIT 1""",
        (alert["machine_id"], at or alert["opened_at"]),
    ).fetchone()
    return _from_prediction_row(row, "nearest_timestamp") if row is not None else None


def _probable_cause_block(alert: dict, trigger: _Trigger, features: dict | None, notes: list) -> dict:
    from src.prediction.pipeline import _FEATURE_ALIASES  # lazy: pipeline imports this module

    label = alert.get("probable_cause")
    human = rule_based.CAUSE_LABELS.get(label, label.replace("_", " ") if label else "not recorded")
    block = {"label": label, "display": f"Probable cause: {human}", "disclaimer": DISCLAIMER,
             "rule": None, "summary": None, "checks": [], "evaluated_label": None,
             "matches_alert": None, "inputs_source": None}

    if features:
        row = {canonical: features.get(short) for short, canonical in _FEATURE_ALIASES.items()}
        block["inputs_source"] = "snapshot_features"
    elif trigger.row is not None:
        row = {canonical: trigger.row.get(canonical) for canonical in _FEATURE_ALIASES.values()}
        block["inputs_source"] = "trigger_reading"
    else:
        notes.append("No reading is available to show the rule behind the probable cause.")
        return block

    trace = rule_based.explain_probable_cause(row)
    block.update(rule=trace["rule"], summary=trace["summary"], checks=trace["checks"],
                 evaluated_label=trace["label"])
    if label is not None:
        block["matches_alert"] = trace["label"] == label
        if not block["matches_alert"]:
            evaluated = rule_based.CAUSE_LABELS.get(trace["label"], trace["label"])
            notes.append(f"The rule evaluated on this reading gives '{evaluated}', which differs "
                         f"from the probable cause recorded on the alert ('{human}'); the recorded "
                         "label is shown.")
    return block


# --- snapshot --------------------------------------------------------------------------

def _guarded(name: str, notes: list, build, fallback):
    try:
        return build()
    except Exception as exc:
        logger.exception("Failed to build the %s section of an alert explanation", name)
        notes.append(f"{name[0].upper()}{name[1:]} could not be computed ({type(exc).__name__}).")
        return fallback


def build_snapshot(conn, alert: dict, *, reading_id=None, prediction_id=None,
                   prediction: dict | None = None, features: dict | None = None,
                   at: str | None = None, predictor=None) -> dict:
    """The explanation body for `alert` (everything but similar incidents):
    explanation_version, synthetic, triggering_readings, key_factors,
    prediction, probable_cause and notes. `reading_id` / `prediction_id` /
    `prediction` / `features` describe the reading and result behind this
    snapshot when the caller has them (the pipeline does); without them the
    alert's own links, then the nearest stored rows, are used."""
    notes: list[str] = []
    trigger = _guarded("trigger reading", notes,
                       lambda: _locate_trigger(conn, alert, reading_id, features, at, notes,
                                                        prediction_id),
                       _Trigger(None, "none"))
    body = {
        "explanation_version": EXPLANATION_VERSION,
        "synthetic": alert.get("source") == "demo",
        "triggering_readings": _guarded("triggering readings", notes,
                                        lambda: _triggering_readings(conn, alert, trigger), None),
        "key_factors": _guarded("key factors", notes,
                                lambda: _key_factors(conn, alert, trigger, predictor),
                                {"status": "error", "method": None, "weighting": None,
                                 "baseline_readings": 0, "factors": [], "operating_conditions": []}),
        "prediction": _guarded("model prediction", notes,
                               lambda: _prediction_block(conn, alert, prediction, prediction_id, at), None),
        "probable_cause": _guarded("probable cause", notes,
                                   lambda: _probable_cause_block(alert, trigger, features, notes), None),
    }
    if body["prediction"] is None:
        notes.append("No model prediction recorded for this alert.")
    if body["synthetic"]:
        notes.append("Synthetic (demo): generated by the admin demo trigger; readings are scaled "
                     "copies of a real reading.")
    body["notes"] = notes
    return _clean(body)


def record_snapshot(conn, alert: dict, kind: str, **snapshot_kwargs) -> int | None:
    """Build and store one snapshot of `alert` (kind 'created' | 'escalated').
    Returns the new row id, or None when the table is absent (a pre-v6 file
    opened outside get_db) or a 'created' row already exists."""
    if not table_exists(conn, "alert_explanations"):
        logger.debug("alert_explanations missing; no snapshot for alert %s", alert.get("id"))
        return None
    body = build_snapshot(conn, alert, **snapshot_kwargs)
    try:
        cur = conn.execute(
            """INSERT INTO alert_explanations
                   (alert_id, kind, reading_id, prediction_id, created_at, explanation_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (alert["id"], kind, snapshot_kwargs.get("reading_id"), snapshot_kwargs.get("prediction_id"),
             _now(), json.dumps(body)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    return cur.lastrowid


# --- similar incidents -----------------------------------------------------------------

def similar_incidents(conn, alert: dict, *, limit: int = SIMILAR_DEFAULT_LIMIT) -> list[dict]:
    """Earlier alerts with the same probable cause (predicted, or recorded as
    the actual cause) or on the same machine, scored 0-1 and joined to what
    actually happened: feedback, work orders and maintenance. A real alert
    never lists demo incidents; a demo alert lists both."""
    if limit <= 0:
        return []
    cause = alert.get("probable_cause")
    has_feedback = table_exists(conn, "alert_feedback")
    feedback_cols = ("f.outcome AS fb_outcome, f.actual_cause AS fb_actual_cause, "
                     "f.actual_failure_at AS fb_actual_failure_at, f.notes AS fb_notes"
                     if has_feedback else
                     "NULL AS fb_outcome, NULL AS fb_actual_cause, NULL AS fb_actual_failure_at, "
                     "NULL AS fb_notes")
    join = "LEFT JOIN alert_feedback f ON f.alert_id = a.id" if has_feedback else ""
    cause_match = "a.probable_cause = :cause OR a.machine_id = :machine"
    if has_feedback:
        cause_match += " OR f.actual_cause = :cause"
    synthetic_filter = "" if alert.get("source") == "demo" else "AND COALESCE(a.source, '') != 'demo'"
    rows = conn.execute(
        f"""SELECT a.id, a.machine_id, a.opened_at, a.resolved_at, a.status, a.severity,
                   a.health_state, a.probable_cause, a.source, {feedback_cols}
            FROM alerts a {join}
            WHERE a.id != :id
              AND (a.opened_at < :opened OR (a.opened_at = :opened AND a.id < :id))
              AND ({cause_match}) {synthetic_filter}
            ORDER BY a.opened_at DESC, a.id DESC LIMIT :cap""",
        {"id": alert["id"], "opened": alert["opened_at"], "cause": cause,
         "machine": alert["machine_id"], "cap": SIMILAR_CANDIDATE_CAP},
    ).fetchall()

    opened = _parse_time(alert["opened_at"])
    scored = []
    for row in rows:
        row = dict(row)
        reasons = []
        same_cause = cause is not None and row["probable_cause"] == cause
        actual_matches = cause is not None and row["fb_actual_cause"] == cause
        if same_cause:
            reasons.append("same_probable_cause")
        if actual_matches:
            reasons.append("actual_cause_matches")
        score = _W_CAUSE if (same_cause or actual_matches) else 0.0
        if row["machine_id"] == alert["machine_id"]:
            reasons.append("same_machine")
            score += _W_MACHINE
        if row["severity"] == alert.get("severity"):
            reasons.append("same_severity")
            score += _W_SEVERITY
        then = _parse_time(row["opened_at"])
        if opened is not None and then is not None:
            age_days = max((opened - then).total_seconds() / 86400.0, 0.0)
            score += _W_RECENCY * math.exp(-age_days / _RECENCY_DAYS)
        scored.append((score, reasons, row))
    # Stable sorts: opened_at descending first, then score.
    scored.sort(key=lambda item: item[2]["opened_at"], reverse=True)
    scored.sort(key=lambda item: item[0], reverse=True)

    has_work_orders = table_exists(conn, "work_orders")
    return [_incident(conn, row, score, reasons, has_work_orders)
            for score, reasons, row in scored[:limit]]


def _incident(conn, row: dict, score: float, reasons: list, has_work_orders: bool) -> dict:
    feedback = None
    cause_confirmed = None
    if row["fb_outcome"] is not None:
        notes = row["fb_notes"]
        feedback = {"outcome": row["fb_outcome"], "actual_cause": row["fb_actual_cause"],
                    "actual_failure_at": row["fb_actual_failure_at"],
                    "notes": notes[:_NOTES_LIMIT] if notes else notes}
        if row["fb_actual_cause"] not in (None, "unknown", "other"):
            cause_confirmed = row["fb_actual_cause"] == row["probable_cause"]

    work_orders = []
    record_ids = []
    if has_work_orders:
        for wo in conn.execute("SELECT id, status, title, completed_at, maintenance_record_id "
                               "FROM work_orders WHERE alert_id = ? ORDER BY id", (row["id"],)):
            work_orders.append({"id": wo["id"], "status": wo["status"], "title": wo["title"],
                                "completed_at": wo["completed_at"]})
            if wo["maintenance_record_id"] is not None:
                record_ids.append(wo["maintenance_record_id"])
    placeholders = ",".join("?" for _ in record_ids)
    linked = f" OR id IN ({placeholders})" if record_ids else ""
    maintenance = [
        {"id": m["id"], "performed_at": m["performed_at"], "type": m["type"],
         "description": m["description"], "technician": m["technician"]}
        for m in conn.execute(
            f"""SELECT id, performed_at, type, description, technician FROM maintenance_records
                WHERE alert_id = ?{linked} ORDER BY performed_at, id""",
            [row["id"], *record_ids])
    ]
    return {
        "alert_id": row["id"], "machine_id": row["machine_id"], "opened_at": row["opened_at"],
        "resolved_at": row["resolved_at"], "status": row["status"], "severity": row["severity"],
        "health_state": row["health_state"], "probable_cause": row["probable_cause"],
        "synthetic": row["source"] == "demo", "similarity": round(min(score, 1.0), 4),
        "match_reasons": reasons, "feedback": feedback, "cause_confirmed": cause_confirmed,
        "work_orders": work_orders, "maintenance": maintenance,
    }


# --- read ------------------------------------------------------------------------------

def _load_snapshot(row):
    try:
        body = json.loads(row["explanation_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("explanation_version") != EXPLANATION_VERSION:
        return None
    return body


def get_explanation(conn, alert_id: int, *, similar_limit: int = SIMILAR_DEFAULT_LIMIT,
                    predictor=None) -> dict | None:
    """The AlertExplanation dict for alert_id, or None for an unknown alert.
    Serves the latest snapshot (highest id), else rebuilds from stored data
    without persisting it; similar incidents are always computed now."""
    row = conn.execute(f"SELECT {live.API_ALERT_COLUMNS} FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        return None
    alert = live.api_alert(row)

    snapshots = []
    if table_exists(conn, "alert_explanations"):
        snapshots = [dict(r) for r in conn.execute(
            "SELECT id, kind, created_at, explanation_json FROM alert_explanations "
            "WHERE alert_id = ? ORDER BY id", (alert_id,))]

    body = _load_snapshot(snapshots[-1]) if snapshots else None
    if body is not None:
        latest = snapshots[-1]
        result = {"source": "snapshot", "snapshot_kind": latest["kind"],
                  "snapshot_at": latest["created_at"], "generated_at": latest["created_at"]}
        notes = list(body.get("notes") or [])
    else:
        body = build_snapshot(conn, alert, reading_id=alert.get("reading_id"),
                              prediction_id=alert.get("prediction_id"), predictor=predictor)
        result = {"source": "reconstructed", "snapshot_kind": None, "snapshot_at": None,
                  "generated_at": _now()}
        notes = list(body.get("notes") or [])
        if snapshots:
            notes.insert(0, "The stored snapshot could not be read; the explanation was rebuilt "
                            "from stored data.")
        else:
            notes.insert(0, "No snapshot was captured for this alert; it was rebuilt from stored data.")
        opening = (body.get("prediction") or {}).get("health_state")
        if opening and opening != alert["health_state"]:
            notes.append("This alert was escalated after it opened and the escalation evidence was "
                          "not captured, so the opening evidence is shown.")

    try:
        similar = similar_incidents(conn, alert, limit=similar_limit)
    except Exception:
        logger.exception("Failed to find incidents similar to alert %s", alert_id)
        notes.append("Similar past incidents could not be computed.")
        similar = []

    result.update({
        "alert": alert,
        "explanation_version": EXPLANATION_VERSION,
        "snapshots": [{"kind": s["kind"], "created_at": s["created_at"]} for s in snapshots],
        "synthetic": alert.get("source") == "demo",
        "triggering_readings": body.get("triggering_readings"),
        "key_factors": body.get("key_factors"),
        "prediction": body.get("prediction"),
        "probable_cause": body.get("probable_cause"),
        "similar_incidents": similar,
        "notes": notes,
    })
    return result
