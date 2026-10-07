"""src/feedback/export.py: field episodes as a build_feature_table-shaped CSV
for `python -m src.training.xjtu_rul --features-csv`
(design/2026-10-07-prediction-feedback-design.md, decision 14).

Live readings are inserted on m2 with dataset='live_mqtt' and real feature
dicts from extract_snapshot_features, so the round trip through
xjtu_rul.add_past_context exercises the true column requirements. Nothing is
trained.
"""
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.feedback import export
from src.ingestion.xjtu_sy import extract_snapshot_features

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
_cycle = {"n": 100}


def _iso(minutes, *, z=False):
    text = (T0 + timedelta(minutes=minutes)).isoformat()
    return text.replace("+00:00", "Z") if z else text


def _features(seed):
    rng = np.random.default_rng(seed)
    return extract_snapshot_features(rng.normal(size=1024), rng.normal(size=1024))


def _reading(conn, minutes, *, dataset="live_mqtt", features=None, z=False, machine_id="m2"):
    _cycle["n"] += 1
    feats = _features(_cycle["n"]) if features is None else features
    return conn.execute(
        """INSERT INTO readings
           (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm, load_kn, sample_rate_hz,
            vibration_h_rms, vibration_h_kurtosis, vibration_v_rms, vibration_v_kurtosis,
            cross_axis_rms_ratio, cross_axis_correlation, rul_minutes, features_json, dataset)
           VALUES (?, ?, ?, 0, 2100.0, 12.0, 25600.0, 0.1, 3.0, 0.1, 3.0, 1.0, 0.1, NULL, ?, ?)""",
        (machine_id, _iso(minutes, z=z), _cycle["n"], json.dumps(feats), dataset),
    ).lastrowid


def _episode(conn, outcome="confirmed_failure", *, opened=0, failure=50, source="xjtu_rul",
             machine_id="m2", version="v1"):
    alert_id = conn.execute(
        "INSERT INTO alerts (machine_id, opened_at, severity, health_state, status, source, "
        "created_at, model_version) VALUES (?, ?, 'high', 'critical', 'resolved', ?, ?, ?)",
        (machine_id, _iso(opened), source, _iso(opened), version),
    ).lastrowid
    failure_at = _iso(failure) if (failure is not None and outcome == "confirmed_failure") else None
    fb = conn.execute(
        "INSERT INTO alert_feedback (alert_id, outcome, actual_failure_at, recorded_by, recorded_at) "
        "VALUES (?, ?, ?, 3, ?)", (alert_id, outcome, failure_at, _iso(0)),
    ).lastrowid
    conn.commit()
    return fb


def _standard_readings(conn):
    ids = [_reading(conn, m) for m in (0, 10, 20, 30, 40, 60, 70)]
    conn.commit()
    return ids


def test_one_episode_rows_and_columns(conn):
    ids = _standard_readings(conn)
    fb = _episode(conn)
    result = export.build_export(conn)

    assert result.episode_count == 1 and len(result.rows) == 5
    assert result.columns[: len(export.BASE_COLUMNS)] == export.BASE_COLUMNS
    assert result.columns[len(export.BASE_COLUMNS)] == "h_mean"  # features_json insertion order
    rows = result.rows
    assert {r["bearing_id"] for r in rows} == {f"m2:fb{fb}"}
    assert [r["cycle"] for r in rows] == [0, 1, 2, 3, 4]
    assert [r["elapsed_minutes"] for r in rows] == [0.0, 10.0, 20.0, 30.0, 40.0]
    assert [r["rul_minutes"] for r in rows] == [50.0, 40.0, 30.0, 20.0, 10.0]
    assert [r["source_file"] for r in rows] == [f"reading:{i}" for i in ids[:5]]
    assert rows[0]["condition"] == 1 and rows[0]["speed_rpm"] == 2100.0 and rows[0]["load_kn"] == 12.0
    assert export.count_episodes(conn) == 1


def test_csv_round_trip_is_accepted_by_training_column_requirements(conn):
    from src.training import xjtu_rul

    _standard_readings(conn)
    _episode(conn)
    table = pd.read_csv(io.StringIO(export.to_csv(export.build_export(conn))))
    context = xjtu_rul.add_past_context(table)
    features = xjtu_rul.feature_columns(context)
    assert not (set(features) & xjtu_rul.NON_FEATURE_COLUMNS)
    assert "h_rms" in features and "h_rms_baseline_ratio" in features


def test_concatenates_with_an_xjtu_shaped_table(conn):
    _standard_readings(conn)
    _episode(conn)
    field = pd.read_csv(io.StringIO(export.to_csv(export.build_export(conn))))
    xjtu = pd.DataFrame.from_records([
        {"bearing_id": "Bearing1_1", "condition": 1, "cycle": c, "elapsed_minutes": c * 1.0,
         "rul_minutes": (2 - c) * 1.0, "speed_rpm": 2100, "load_kn": 12.0,
         "source_file": f"Bearing1_1/{c + 1}.csv", **_features(c)}
        for c in range(3)
    ])
    merged = pd.concat([xjtu, field], ignore_index=True, sort=False)
    assert len(merged) == 8
    assert set(xjtu["bearing_id"]).isdisjoint(set(field["bearing_id"]))
    assert pd.api.types.is_numeric_dtype(merged["h_rms"])


def test_excluded_episodes_and_readings(conn):
    _standard_readings(conn)
    _reading(conn, 35, dataset="xjtu_sy")
    conn.commit()
    _episode(conn, "false_alarm")
    _episode(conn, "maintenance_prevented")
    _episode(conn, "confirmed_failure", failure=None)
    _episode(conn, source="demo")
    assert export.count_episodes(conn) == 0
    result = export.build_export(conn)
    assert result.episode_count == 0 and result.rows == []

    _episode(conn)
    result = export.build_export(conn)
    assert len(result.rows) == 5  # the xjtu_sy reading at +35 is not used


def test_maintenance_before_the_alert_starts_the_episode(conn):
    _standard_readings(conn)
    conn.execute("INSERT INTO maintenance_records (machine_id, performed_at, created_at) "
                 "VALUES ('m2', ?, ?)", (_iso(5), _iso(5)))
    _episode(conn, opened=25)
    assert [r["rul_minutes"] for r in export.build_export(conn).rows] == [40.0, 30.0, 20.0, 10.0]


def test_maintenance_after_the_alert_does_not_cut(conn):
    _standard_readings(conn)
    conn.execute("INSERT INTO maintenance_records (machine_id, performed_at, created_at) "
                 "VALUES ('m2', ?, ?)", (_iso(25), _iso(25)))
    _episode(conn, opened=0)
    assert len(export.build_export(conn).rows) == 5


def test_earlier_failure_on_the_machine_starts_the_next_episode(conn):
    _standard_readings(conn)
    first = _episode(conn, opened=0, failure=15)
    second = _episode(conn, opened=20, failure=50)
    rows = export.build_export(conn).rows
    by_episode = {}
    for r in rows:
        by_episode.setdefault(r["bearing_id"], []).append(r["rul_minutes"])
    assert by_episode == {f"m2:fb{first}": [15.0, 5.0], f"m2:fb{second}": [30.0, 20.0, 10.0]}


def test_unusable_rows_and_empty_episodes_are_skipped(conn):
    _reading(conn, 0, features={})
    partial = _features(1)
    del partial["h_rms"]
    _reading(conn, 10, features=partial)
    _reading(conn, 20)
    conn.execute("UPDATE readings SET features_json = 'not json' WHERE id = ?", (_reading(conn, 30),))
    conn.commit()
    _episode(conn, failure=50)
    result = export.build_export(conn)
    assert len(result.rows) == 1 and result.skipped_rows == 3 and result.skipped_episodes == 0

    conn.execute("INSERT INTO machines (machine_id, dataset) VALUES ('m3', 'live_mqtt')")
    _reading(conn, 0, features={}, machine_id="m3")
    conn.commit()
    _episode(conn, machine_id="m3", failure=50)
    result = export.build_export(conn)
    assert result.episode_count == 1 and result.skipped_episodes == 1


def test_mixed_timestamp_suffixes_compare_as_times(conn):
    for minutes in (0, 10, 20, 30, 40, 60):
        _reading(conn, minutes, z=minutes % 20 == 0)
    conn.commit()
    _episode(conn, failure=40)
    rows = export.build_export(conn).rows
    assert [r["rul_minutes"] for r in rows] == [40.0, 30.0, 20.0, 10.0, 0.0]


def test_empty_export_writes_the_header_only(conn):
    text = export.to_csv(export.build_export(conn))
    assert text.strip() == ",".join(export.BASE_COLUMNS)


def test_model_version_filter(conn):
    _standard_readings(conn)
    _episode(conn, version="v1")
    assert export.count_episodes(conn, model_version="v2") == 0
    assert export.build_export(conn, model_version="v2").rows == []
    assert export.count_episodes(conn, model_version="v1") == 1


def test_cli_writes_and_merges(db_path, tmp_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        _standard_readings(c)
        _episode(c)
    finally:
        c.close()
    out = tmp_path / "feedback.csv"
    assert export.main(["--out", str(out), "--db", str(db_path)]) == 0
    field = pd.read_csv(out)
    assert len(field) == 5

    xjtu_path = tmp_path / "xjtu.csv"
    pd.DataFrame.from_records([
        {"bearing_id": "Bearing1_1", "condition": 1, "cycle": 0, "elapsed_minutes": 0.0,
         "rul_minutes": 1.0, "speed_rpm": 2100, "load_kn": 12.0, "source_file": "a.csv",
         "only_in_xjtu": 1.0, **_features(0)},
    ]).to_csv(xjtu_path, index=False)
    merged_path = tmp_path / "merged.csv"
    assert export.main(["--out", str(out), "--db", str(db_path), "--merge-with", str(xjtu_path),
                        "--merged-out", str(merged_path)]) == 0
    merged = pd.read_csv(merged_path)
    assert len(merged) == 6
    assert "only_in_xjtu" in merged.columns and "h_rms" in merged.columns
    assert set(merged["bearing_id"]) == {"Bearing1_1", *field["bearing_id"]}


def test_cli_merge_requires_merged_out(db_path, tmp_path):
    with pytest.raises(SystemExit):
        export.main(["--out", str(tmp_path / "x.csv"), "--db", str(db_path),
                     "--merge-with", str(tmp_path / "y.csv")])
