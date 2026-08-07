"""Tests for the XJTU-SY batch backfill (feature table -> machines + readings)."""
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.ingestion.backfill import BACKFILL_EPOCH, BackfillResult, backfill_from_table
from src.ingestion import backfill as backfill_module
from src.ingestion.backfill import backfill_dataset
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


def _signal(scale=1.0, points=128):
    # Mirrors tests/test_xjtu_rul.py: a clean 1 kHz tone, enough samples for the
    # feature extractor's 32-sample floor.
    t = np.arange(points) / 25600.0
    return scale * np.sin(2 * np.pi * 1000 * t)


def _write_raw_dataset(root):
    """Create a tiny but real XJTU-SY layout: one bearing, three snapshots."""
    bearing = root / "35Hz12kN" / "Bearing1_1"
    bearing.mkdir(parents=True)
    for number, scale in enumerate((1.0, 1.2, 1.5), start=1):
        pd.DataFrame({"h": _signal(scale), "v": _signal(scale * 1.1)}).to_csv(
            bearing / f"{number}.csv", index=False
        )
    return root


def test_backfill_dataset_delegates_to_build_feature_table(monkeypatch):
    conn = _fresh_conn()
    fake_table = _table([_row("Bearing1_1", 0), _row("Bearing1_1", 1)])
    seen = {}

    def _fake_build(dataset_dir, output_csv=None):
        seen["dir"] = dataset_dir
        return fake_table

    monkeypatch.setattr(backfill_module, "build_feature_table", _fake_build)

    result = backfill_dataset(conn, "/some/dataset/dir")

    assert isinstance(seen["dir"], Path)
    assert str(seen["dir"]) == str(Path("/some/dataset/dir"))
    assert result == BackfillResult(machines_written=1, readings_written=2, skipped=0)


def test_backfill_dataset_end_to_end_populates_zero_null_readings(tmp_path):
    conn = _fresh_conn()
    _write_raw_dataset(tmp_path)

    result = backfill_dataset(conn, tmp_path)

    assert result.machines_written == 1
    assert result.readings_written == 3
    assert result.skipped == 0

    # True end-of-run RUL from real extraction: 3 snapshots -> 2,1,0 minutes.
    ruls = [
        r["rul_minutes"]
        for r in conn.execute(
            "SELECT rul_minutes FROM readings WHERE machine_id='Bearing1_1' ORDER BY cycle"
        ).fetchall()
    ]
    assert ruls == [2.0, 1.0, 0.0]

    # Zero NULLs on every NOT NULL column, from the real feature path.
    not_null_cols = [
        "machine_id", "timestamp", "cycle", "elapsed_minutes", "speed_rpm",
        "load_kn", "sample_rate_hz", "vibration_h_rms", "vibration_h_kurtosis",
        "vibration_v_rms", "vibration_v_kurtosis", "cross_axis_rms_ratio",
        "cross_axis_correlation", "features_json", "dataset",
    ]
    for row in conn.execute(f"SELECT {', '.join(not_null_cols)} FROM readings").fetchall():
        for col in not_null_cols:
            assert row[col] is not None, f"{col} was NULL"


def test_cli_backfills_from_cached_feature_csv(tmp_path, monkeypatch):
    """`python -m src.ingestion.backfill --features-csv <cache>` loads the DB
    from an already-built feature table, no raw dataset needed."""
    from src.storage.db import get_connection

    csv = tmp_path / "features.csv"
    _table([
        _row("Bearing1_1", 0), _row("Bearing1_1", 1),
        _row("Bearing1_2", 0),
    ]).to_csv(csv, index=False)

    db_file = tmp_path / "cli.db"
    monkeypatch.setattr(backfill_module, "get_connection", lambda: get_connection(db_file))

    result = backfill_module.main(["--features-csv", str(csv)])

    assert result.machines_written == 2
    assert result.readings_written == 3
    verify = get_connection(db_file)
    try:
        assert verify.execute("SELECT COUNT(*) FROM machines").fetchone()[0] == 2
        assert verify.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 3
    finally:
        verify.close()


def test_cli_requires_data_dir_when_feature_csv_missing(tmp_path, monkeypatch):
    """With no cache and no --data-dir, the CLI errors instead of writing an
    empty DB."""
    from src.storage.db import get_connection

    db_file = tmp_path / "cli.db"
    monkeypatch.setattr(backfill_module, "get_connection", lambda: get_connection(db_file))

    with pytest.raises(SystemExit):
        backfill_module.main(["--features-csv", str(tmp_path / "does-not-exist.csv")])


@pytest.fixture
def backfilled_client(tmp_path):
    """TestClient (admin) whose DB is a fresh schema + one backfilled bearing."""
    from fastapi.testclient import TestClient

    from src.api.app import app
    from src.api.deps import get_db
    from src.auth.security import hash_password
    from src.auth.seed import DEMO_USERS

    db_file = tmp_path / "backfilled.db"
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    email, name, password, role = DEMO_USERS[0]  # admin
    conn.execute(
        "INSERT INTO users (email, name, hashed_password, role, is_active, created_at) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (email, name, hash_password(password), role, BACKFILL_EPOCH.isoformat()),
    )
    conn.commit()
    backfill_from_table(conn, _table([
        _row("Bearing1_1", 0, h_rms=0.5),
        _row("Bearing1_1", 1, h_rms=0.6),
        _row("Bearing1_1", 2, h_rms=0.7),
    ]))
    conn.close()

    def _override():
        c = sqlite3.connect(db_file, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override
    try:
        with TestClient(app) as test_client:
            resp = test_client.post("/api/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200, resp.text
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_trends_endpoint_returns_points_for_backfilled_machine(backfilled_client):
    resp = backfilled_client.get(
        "/api/machines/Bearing1_1/trends", params={"metric": "vibration_h_rms"}
    )
    assert resp.status_code == 200, resp.text
    points = resp.json()
    # Three snapshots, returned oldest-first for left-to-right charting.
    assert [p["value"] for p in points] == [0.5, 0.6, 0.7]
    assert all(p["timestamp"] for p in points)
