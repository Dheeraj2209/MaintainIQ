"""Batch backfill: an XJTU-SY feature table -> canonical machines + readings rows.

Feature math has exactly one home (src.ingestion.xjtu_sy.extract_snapshot_features,
reached via build_feature_table). This module NEVER computes a vibration feature;
it only reshapes an already-built feature table into DB rows and writes them
through the Phase 1 insert helpers (both INSERT OR IGNORE -> idempotent on
UNIQUE(machine_id, cycle)).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.ingestion.xjtu_sy import SAMPLE_RATE_HZ, build_feature_table
from src.storage.db import insert_machines, insert_readings

# XJTU snapshots are one-per-minute but carry no wall clock. We anchor every
# bearing's cycle 0 at this fixed UTC epoch and add elapsed_minutes, so the
# synthesized `timestamp` is fully reproducible across runs. Reproducibility is
# what lets a re-run map to the same rows; idempotency itself is enforced by
# UNIQUE(machine_id, cycle) in the readings table.
BACKFILL_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)

# Columns in a build_feature_table record that are identity/label, not features.
# Everything else is an extract_snapshot_features key and belongs in features_json.
_NON_FEATURE_COLUMNS = frozenset({
    "bearing_id", "condition", "cycle", "elapsed_minutes", "rul_minutes",
    "speed_rpm", "load_kn", "source_file",
})


@dataclass
class BackfillResult:
    machines_written: int
    readings_written: int
    skipped: int


def _timestamp_for(elapsed_minutes: float) -> str:
    return (BACKFILL_EPOCH + timedelta(minutes=float(elapsed_minutes))).isoformat()


def _feature_dict(record: dict) -> dict:
    # Cast to float: DataFrame.to_dict yields numpy scalars, which json.dumps and
    # sqlite3 parameter binding both reject.
    return {
        key: float(value)
        for key, value in record.items()
        if key not in _NON_FEATURE_COLUMNS
    }


def backfill_from_table(conn, feature_df: pd.DataFrame, *, dataset: str = "xjtu_sy") -> BackfillResult:
    """Reshape a build_feature_table DataFrame into machines + readings rows.

    One machines row per bearing_id; one readings row per snapshot. Re-running is
    a no-op on already-present (machine_id, cycle) pairs (reported as `skipped`).
    """
    if feature_df.empty:
        return BackfillResult(0, 0, 0)

    records = feature_df.to_dict("records")

    machines: dict[str, dict] = {}
    reading_rows: list[dict] = []
    for record in records:
        bearing_id = record["bearing_id"]
        if bearing_id not in machines:
            machines[bearing_id] = {
                "machine_id": bearing_id,
                "bearing_id": bearing_id,
                "operating_condition": int(record["condition"]),
                "speed_rpm": float(record["speed_rpm"]),
                "load_kn": float(record["load_kn"]),
                "dataset": dataset,
                "is_documented_failure": 1,  # all XJTU bearings are run-to-failure
            }

        features = _feature_dict(record)
        reading_rows.append({
            "machine_id": bearing_id,
            "timestamp": _timestamp_for(record["elapsed_minutes"]),
            "cycle": int(record["cycle"]),
            "elapsed_minutes": float(record["elapsed_minutes"]),
            "speed_rpm": float(record["speed_rpm"]),
            "load_kn": float(record["load_kn"]),
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "vibration_h_rms": features["h_rms"],
            "vibration_h_kurtosis": features["h_kurtosis"],
            "vibration_v_rms": features["v_rms"],
            "vibration_v_kurtosis": features["v_kurtosis"],
            "cross_axis_rms_ratio": features["cross_axis_rms_ratio"],
            "cross_axis_correlation": features["cross_axis_correlation"],
            "rul_minutes": float(record["rul_minutes"]),
            "features_json": json.dumps(features, sort_keys=True),
            "dataset": dataset,
        })

    before = conn.total_changes
    insert_machines(conn, list(machines.values()))
    machines_written = conn.total_changes - before

    before = conn.total_changes
    insert_readings(conn, reading_rows)
    readings_written = conn.total_changes - before
    skipped = len(reading_rows) - readings_written

    return BackfillResult(machines_written, readings_written, skipped)


def backfill_dataset(conn, dataset_dir: Path, *, dataset: str = "xjtu_sy") -> BackfillResult:
    """Build the feature table from a raw dataset directory, then backfill it."""
    table = build_feature_table(Path(dataset_dir))
    return backfill_from_table(conn, table, dataset=dataset)
