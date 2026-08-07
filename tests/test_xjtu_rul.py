import sqlite3

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from src.ingestion.xjtu_sy import build_feature_table, extract_snapshot_features, read_snapshot
from src.storage.db import init_schema
from src.training.xjtu_rul import (
    ROLLING_SOURCE_COLUMNS,
    add_past_context,
    feature_columns,
    register_trained_model,
    train_and_export,
)


def _signal(scale=1.0, points=128):
    t = np.arange(points) / 25600.0
    return scale * np.sin(2 * np.pi * 1000 * t)


def test_register_trained_model_activates_artifact(tmp_path):
    """After training, the artifact is recorded as the single active model so
    the /model observability page and model_performance report resolve it."""
    artifact_path = tmp_path / "m.joblib"
    joblib.dump(
        {"model_version": "xjtu-rul-TESTVER", "regressor": ExtraTreesRegressor()},
        artifact_path,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    version = register_trained_model(conn, artifact_path, {"mae_minutes": 1.0})

    assert version == "xjtu-rul-TESTVER"
    row = conn.execute(
        "SELECT model_version, algorithm, is_active, metrics_json "
        "FROM model_registry WHERE is_active = 1"
    ).fetchone()
    assert row["model_version"] == "xjtu-rul-TESTVER"
    assert row["algorithm"] == "ExtraTreesRegressor"
    assert row["is_active"] == 1
    assert "mae_minutes" in row["metrics_json"]


def test_register_trained_model_deactivates_previous(tmp_path):
    """Registering a second model leaves exactly one active row."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    for version in ("xjtu-rul-OLD", "xjtu-rul-NEW"):
        path = tmp_path / f"{version}.joblib"
        joblib.dump({"model_version": version, "regressor": ExtraTreesRegressor()}, path)
        register_trained_model(conn, path, {})

    active = conn.execute(
        "SELECT model_version FROM model_registry WHERE is_active = 1"
    ).fetchall()
    assert [r["model_version"] for r in active] == ["xjtu-rul-NEW"]


def test_snapshot_features_are_finite_and_dual_axis():
    features = extract_snapshot_features(_signal(), _signal(2.0))
    assert "h_rms" in features and "v_rms" in features
    assert np.isfinite(list(features.values())).all()
    assert 0.49 < features["cross_axis_rms_ratio"] < 0.51


def test_read_snapshot_tolerates_official_style_header(tmp_path):
    path = tmp_path / "1.csv"
    pd.DataFrame({"Horizontal_vibration_signals": _signal(), "Vertical_vibration_signals": _signal(2)}).to_csv(
        path, index=False
    )
    horizontal, vertical = read_snapshot(path)
    assert len(horizontal) == 128
    assert vertical.std() > horizontal.std()


def test_build_table_uses_true_end_of_run_rul(tmp_path):
    bearing = tmp_path / "35Hz12kN" / "Bearing1_1"
    bearing.mkdir(parents=True)
    for number, scale in enumerate((1.0, 1.2, 1.5), start=1):
        pd.DataFrame({"h": _signal(scale), "v": _signal(scale * 1.1)}).to_csv(
            bearing / f"{number}.csv", index=False
        )
    table = build_feature_table(tmp_path)
    assert table["rul_minutes"].tolist() == [2.0, 1.0, 0.0]
    assert table["bearing_id"].unique().tolist() == ["Bearing1_1"]


def test_context_is_past_only_and_excludes_identifiers():
    rows = []
    for cycle, value in enumerate((1.0, 2.0, 50.0)):
        row = {"bearing_id": "b1", "cycle": cycle, "rul_minutes": 2 - cycle,
               "elapsed_minutes": cycle, "condition": 1, "source_file": f"{cycle}.csv"}
        for column in ROLLING_SOURCE_COLUMNS:
            row[column] = value
        rows.append(row)
    context = add_past_context(pd.DataFrame(rows))
    assert context.loc[1, "h_rms_mean_5"] == 1.5
    assert context.loc[0, "h_rms_mean_5"] == 1.0
    features = feature_columns(context)
    assert "bearing_id" not in features
    assert "rul_minutes" not in features
    assert "cycle" not in features
    assert "condition" not in features


def test_training_exports_model_and_grouped_evaluation(tmp_path):
    rows = []
    for bearing_number in range(3):
        # Longer than the 120-minute prognostic horizon so every grouped
        # training fold contains both outside-horizon and late-life samples.
        lifetime = 130 + bearing_number * 10
        for cycle in range(lifetime):
            degradation = cycle / lifetime
            row = {
                "bearing_id": f"b{bearing_number}", "condition": bearing_number + 1,
                "cycle": cycle, "elapsed_minutes": cycle,
                "rul_minutes": lifetime - cycle - 1,
                "speed_rpm": 2100 + bearing_number * 150, "load_kn": 12 - bearing_number,
                "source_file": f"b{bearing_number}/{cycle}.csv",
            }
            for offset, column in enumerate(ROLLING_SOURCE_COLUMNS):
                row[column] = 1.0 + degradation * (offset + 1)
            rows.append(row)
    artifact = tmp_path / "model.joblib"
    report_path = tmp_path / "report.json"
    report = train_and_export(pd.DataFrame(rows), artifact, report_path)
    assert artifact.exists() and report_path.exists()
    assert report["bearing_count"] == 3
    tested = {bearing for fold in report["folds"] for bearing in fold["test_bearings"]}
    assert tested == {"b0", "b1", "b2"}
