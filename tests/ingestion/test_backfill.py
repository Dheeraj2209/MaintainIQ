"""Tests for the XJTU-SY batch backfill (feature table -> machines + readings)."""
import json
import sqlite3
from datetime import timedelta

import pandas as pd
import pytest

from src.ingestion.backfill import BACKFILL_EPOCH, BackfillResult, backfill_from_table
from src.storage.db import init_schema


def _fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _row(bearing_id, cycle, *, condition=1, speed=2100.0, load=12.0, **over):
    """One build_feature_table-shaped record: identity/label cols + feature cols."""
    base = {
        "bearing_id": bearing_id,
        "condition": condition,
        "cycle": cycle,
        "elapsed_minutes": float(cycle),
        "rul_minutes": float(10 - cycle),
        "speed_rpm": speed,
        "load_kn": load,
        "source_file": f"{bearing_id}/{cycle + 1}.csv",
        # feature vector (subset is fine; must include the six promoted keys)
        "h_rms": 0.5, "h_kurtosis": 3.1,
        "v_rms": 0.4, "v_kurtosis": 3.0,
        "m_rms": 0.6,
        "cross_axis_rms_ratio": 1.25,
        "cross_axis_correlation": 0.7,
    }
    base.update(over)
    return base


def _table(rows):
    return pd.DataFrame.from_records(rows)


def test_writes_one_machine_per_bearing_and_all_readings():
    conn = _fresh_conn()
    table = _table([
        _row("Bearing1_1", 0), _row("Bearing1_1", 1),
        _row("Bearing1_2", 0), _row("Bearing1_2", 1),
    ])

    result = backfill_from_table(conn, table)

    assert result == BackfillResult(machines_written=2, readings_written=4, skipped=0)
    assert conn.execute("SELECT COUNT(*) FROM machines").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 4


def test_machine_row_fields_from_operating_condition():
    conn = _fresh_conn()
    backfill_from_table(conn, _table([_row("Bearing2_3", 0, condition=2, speed=2250.0, load=11.0)]))

    m = conn.execute(
        "SELECT bearing_id, operating_condition, speed_rpm, load_kn, dataset, "
        "is_documented_failure FROM machines WHERE machine_id = 'Bearing2_3'"
    ).fetchone()
    assert m["bearing_id"] == "Bearing2_3"
    assert m["operating_condition"] == 2
    assert m["speed_rpm"] == 2250.0
    assert m["load_kn"] == 11.0
    assert m["dataset"] == "xjtu_sy"
    assert m["is_documented_failure"] == 1


def test_all_not_null_reading_columns_are_populated():
    conn = _fresh_conn()
    backfill_from_table(conn, _table([_row("Bearing1_1", 0), _row("Bearing1_1", 1)]))

    not_null_cols = [
        "machine_id", "timestamp", "cycle", "elapsed_minutes", "speed_rpm",
        "load_kn", "sample_rate_hz", "vibration_h_rms", "vibration_h_kurtosis",
        "vibration_v_rms", "vibration_v_kurtosis", "cross_axis_rms_ratio",
        "cross_axis_correlation", "features_json", "dataset",
    ]
    rows = conn.execute(f"SELECT {', '.join(not_null_cols)} FROM readings").fetchall()
    assert rows
    for row in rows:
        for col in not_null_cols:
            assert row[col] is not None, f"{col} was NULL"


def test_promoted_columns_copied_from_features():
    conn = _fresh_conn()
    backfill_from_table(conn, _table([
        _row("Bearing1_1", 0, h_rms=0.55, h_kurtosis=4.2, v_rms=0.33,
             v_kurtosis=3.7, cross_axis_rms_ratio=1.4, cross_axis_correlation=0.6)
    ]))

    r = conn.execute(
        "SELECT vibration_h_rms, vibration_h_kurtosis, vibration_v_rms, "
        "vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation, "
        "sample_rate_hz, rul_minutes FROM readings"
    ).fetchone()
    assert r["vibration_h_rms"] == 0.55
    assert r["vibration_h_kurtosis"] == 4.2
    assert r["vibration_v_rms"] == 0.33
    assert r["vibration_v_kurtosis"] == 3.7
    assert r["cross_axis_rms_ratio"] == 1.4
    assert r["cross_axis_correlation"] == 0.6
    assert r["sample_rate_hz"] == 25600.0
    assert r["rul_minutes"] == 10.0


def test_features_json_holds_full_vector_without_identity_columns():
    conn = _fresh_conn()
    backfill_from_table(conn, _table([_row("Bearing1_1", 3)]))

    blob = conn.execute("SELECT features_json FROM readings").fetchone()["features_json"]
    features = json.loads(blob)
    # feature keys present
    for key in ("h_rms", "h_kurtosis", "v_rms", "v_kurtosis", "m_rms",
                "cross_axis_rms_ratio", "cross_axis_correlation"):
        assert key in features
    # identity/label columns excluded
    for key in ("bearing_id", "condition", "cycle", "elapsed_minutes",
                "rul_minutes", "speed_rpm", "load_kn", "source_file"):
        assert key not in features


def test_timestamp_is_deterministic_from_epoch():
    conn = _fresh_conn()
    backfill_from_table(conn, _table([_row("Bearing1_1", 0), _row("Bearing1_1", 5)]))

    rows = conn.execute(
        "SELECT cycle, timestamp FROM readings ORDER BY cycle"
    ).fetchall()
    by_cycle = {r["cycle"]: r["timestamp"] for r in rows}
    assert by_cycle[0] == BACKFILL_EPOCH.isoformat()
    assert by_cycle[5] == (BACKFILL_EPOCH + timedelta(minutes=5)).isoformat()


def test_rerun_is_idempotent_all_skipped():
    conn = _fresh_conn()
    table = _table([_row("Bearing1_1", 0), _row("Bearing1_1", 1), _row("Bearing1_1", 2)])

    first = backfill_from_table(conn, table)
    second = backfill_from_table(conn, table)

    assert first == BackfillResult(machines_written=1, readings_written=3, skipped=0)
    assert second == BackfillResult(machines_written=0, readings_written=0, skipped=3)
    assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM machines").fetchone()[0] == 1


def test_dataset_kwarg_is_written():
    conn = _fresh_conn()
    backfill_from_table(conn, _table([_row("B_val", 0)]), dataset="nasa_ims")

    assert conn.execute("SELECT dataset FROM machines").fetchone()["dataset"] == "nasa_ims"
    assert conn.execute("SELECT dataset FROM readings").fetchone()["dataset"] == "nasa_ims"


def test_empty_table_returns_zero_result():
    conn = _fresh_conn()
    result = backfill_from_table(conn, pd.DataFrame())
    assert result == BackfillResult(0, 0, 0)
    assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0
