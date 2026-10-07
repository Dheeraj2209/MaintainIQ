"""Field episodes for retraining (design/2026-10-07-prediction-feedback-design.md,
decision 14).

Every confirmed failure with a recorded failure time becomes one run-to-
failure trajectory in the exact column layout of
src.ingestion.xjtu_sy.build_feature_table, so it can be merged with the
XJTU-SY table and fed to `python -m src.training.xjtu_rul --features-csv`:

    bearing_id       "{machine_id}:fb{feedback_id}" — unique per episode, never
                     collides with XJTU's Bearing1_1-style ids
    condition        machines.operating_condition ('' when unknown; a
                     non-feature column for training)
    cycle            0..n-1 in time order
    elapsed_minutes  since the episode's first reading
    rul_minutes      actual_failure_at - reading time
    speed_rpm, load_kn, source_file ("reading:{id}"), then the features_json keys

Only live MQTT readings are used: replayed XJTU-SY readings are already in
the training table with exact labels, and their 2003 timestamps can't be
aligned with a wall-clock failure time. An episode runs from the latest
repair before the alert opened (or the machine's previous recorded failure)
up to and including the failure time. Demo alerts never qualify.

Retraining stays manual (README, "Retraining with field feedback"): export,
merge with the XJTU-SY table (--merge-with), train, review the evaluation
report, and only then register the model. The route and this module's
import never pull in pandas or sklearn; ROLLING_SOURCE_COLUMNS is imported
from the training module only when an export is actually built, and pandas
only for the CLI merge.
"""
import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.storage.db import table_exists
from src.telemetry.device_health import parse_iso

# Mirrors src.telemetry.ingest.LIVE_DATASET without importing the ingest stack.
LIVE_DATASET = "live_mqtt"

# build_feature_table's column order (src/ingestion/xjtu_sy.py).
BASE_COLUMNS = ["bearing_id", "condition", "cycle", "elapsed_minutes", "rul_minutes",
                "speed_rpm", "load_kn", "source_file"]

_EPISODE_WHERE = """
    FROM alert_feedback f
    JOIN alerts a ON a.id = f.alert_id
    WHERE f.outcome = 'confirmed_failure' AND f.actual_failure_at IS NOT NULL
      AND COALESCE(a.source, '') != 'demo'
"""


@dataclass
class ExportResult:
    rows: list = field(default_factory=list)
    columns: list = field(default_factory=lambda: list(BASE_COLUMNS))
    episode_count: int = 0
    skipped_rows: int = 0
    skipped_episodes: int = 0


def _version_clause(model_version) -> tuple[str, list]:
    if model_version is None:
        return "", []
    return " AND a.model_version = ?", [model_version]


def count_episodes(conn, *, model_version=None) -> int:
    """Qualifying feedback rows; cheap — reads no readings."""
    if not table_exists(conn, "alert_feedback"):
        return 0
    clause, params = _version_clause(model_version)
    return conn.execute(f"SELECT COUNT(*) {_EPISODE_WHERE}{clause}", params).fetchone()[0]


def _episode_start(conn, machine_id: str, opened, failed, feedback_id: int):
    """The latest of: a repair before the alert opened (a repair between the
    alert and the failure is a failed fix and does not reset the
    trajectory), and an earlier recorded failure on the same machine."""
    candidates = []
    for row in conn.execute(
        "SELECT performed_at FROM maintenance_records WHERE machine_id = ?", (machine_id,)
    ):
        performed = parse_iso(row[0])
        if performed is not None and opened is not None and performed < opened:
            candidates.append(performed)
    for row in conn.execute(
        """SELECT f.actual_failure_at FROM alert_feedback f JOIN alerts a ON a.id = f.alert_id
           WHERE a.machine_id = ? AND f.outcome = 'confirmed_failure'
             AND f.actual_failure_at IS NOT NULL AND f.id != ?""",
        (machine_id, feedback_id),
    ):
        earlier = parse_iso(row[0])
        if earlier is not None and earlier < failed:
            candidates.append(earlier)
    return max(candidates) if candidates else None


def _live_readings(conn, machine_id: str) -> list[tuple]:
    """(parsed_ts, row) for every live reading of the machine, time-ordered.
    Parsed, never string-compared: Z and +00:00 suffixes mix."""
    readings = []
    for row in conn.execute(
        """SELECT id, timestamp, speed_rpm, load_kn, features_json FROM readings
           WHERE machine_id = ? AND dataset = ?""",
        (machine_id, LIVE_DATASET),
    ):
        ts = parse_iso(row[1])
        if ts is not None:
            readings.append((ts, row))
    readings.sort(key=lambda item: item[0])
    return readings


def _features(raw, required) -> dict | None:
    try:
        features = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(features, dict) or not features:
        return None
    if any(column not in features for column in required):
        return None  # add_past_context needs every rolling source column
    return features


def build_export(conn, *, model_version=None) -> ExportResult:
    from src.training.xjtu_rul import ROLLING_SOURCE_COLUMNS

    result = ExportResult()
    if not table_exists(conn, "alert_feedback"):
        return result
    clause, params = _version_clause(model_version)
    episodes = conn.execute(
        f"""SELECT f.id AS feedback_id, f.actual_failure_at, a.machine_id, a.opened_at,
                   (SELECT m.operating_condition FROM machines m
                    WHERE m.machine_id = a.machine_id) AS condition
            {_EPISODE_WHERE}{clause} ORDER BY f.id""",
        params,
    ).fetchall()

    readings_by_machine: dict = {}
    feature_order: list = []
    extra_keys: set = set()
    for episode in episodes:
        failed = parse_iso(episode["actual_failure_at"])
        if failed is None:
            result.skipped_episodes += 1
            continue
        machine_id = episode["machine_id"]
        start = _episode_start(conn, machine_id, parse_iso(episode["opened_at"]), failed,
                               episode["feedback_id"])
        if machine_id not in readings_by_machine:
            readings_by_machine[machine_id] = _live_readings(conn, machine_id)

        kept = []
        for ts, row in readings_by_machine[machine_id]:
            if ts > failed or (start is not None and ts <= start):
                continue
            features = _features(row[4], ROLLING_SOURCE_COLUMNS)
            if features is None:
                result.skipped_rows += 1
                continue
            kept.append((ts, row, features))
        if not kept:
            result.skipped_episodes += 1
            continue

        result.episode_count += 1
        first_ts = kept[0][0]
        bearing_id = f"{machine_id}:fb{episode['feedback_id']}"
        condition = episode["condition"] if episode["condition"] is not None else ""
        for cycle, (ts, row, features) in enumerate(kept):
            features = {k: v for k, v in features.items() if k not in BASE_COLUMNS}
            if not feature_order:
                feature_order = list(features)
            else:
                extra_keys.update(k for k in features if k not in feature_order)
            result.rows.append({
                "bearing_id": bearing_id,
                "condition": condition,
                "cycle": cycle,
                "elapsed_minutes": (ts - first_ts).total_seconds() / 60.0,
                "rul_minutes": (failed - ts).total_seconds() / 60.0,
                "speed_rpm": row[2],
                "load_kn": row[3],
                "source_file": f"reading:{row[0]}",
                **features,
            })

    result.columns = BASE_COLUMNS + feature_order + sorted(extra_keys - set(feature_order))
    return result


def to_csv(result: ExportResult) -> str:
    """The export as CSV text; the header is written even with no rows."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=result.columns, restval="", lineterminator="\n")
    writer.writeheader()
    writer.writerows(result.rows)
    return buffer.getvalue()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export confirmed-failure field episodes as an xjtu_rul feature CSV")
    parser.add_argument("--out", type=Path, required=True, help="field-episode CSV to write")
    parser.add_argument("--merge-with", type=Path,
                        help="an XJTU-SY feature CSV to concatenate the episodes onto")
    parser.add_argument("--merged-out", type=Path, help="where to write the merged CSV")
    parser.add_argument("--model-version", help="only alerts opened by this model version")
    parser.add_argument("--db", type=Path, help="SQLite file (default: MAINTAINIQ_DB_PATH)")
    args = parser.parse_args(argv)
    if args.merge_with is not None and args.merged_out is None:
        parser.error("--merged-out is required with --merge-with")

    from src.storage.db import DEFAULT_DB_PATH, get_connection
    from src.storage.migrations import ensure_current_schema

    conn = get_connection(args.db or DEFAULT_DB_PATH)
    try:
        ensure_current_schema(conn)
        result = build_export(conn, model_version=args.model_version)
    finally:
        conn.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_csv(result), encoding="utf-8")
    print(f"wrote {len(result.rows)} rows from {result.episode_count} episode(s) to {args.out} "
          f"(skipped {result.skipped_rows} rows, {result.skipped_episodes} episodes)")

    if args.merge_with is not None:
        import pandas as pd

        merged = pd.concat([pd.read_csv(args.merge_with), pd.read_csv(args.out)],
                           ignore_index=True, sort=False)
        args.merged_out.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(args.merged_out, index=False)
        print(f"wrote {len(merged)} rows to {args.merged_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
