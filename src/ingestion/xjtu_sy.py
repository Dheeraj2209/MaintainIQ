"""XJTU-SY run-to-failure bearing dataset ingestion.

The official dataset contains one CSV vibration snapshot per minute.  Each
snapshot has 32,768 horizontal and vertical acceleration samples recorded at
25.6 kHz.  This module deliberately derives the RUL label from the number of
remaining snapshots; it never invents health labels from the input features.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.features.vibration import extract_window_features

SAMPLE_RATE_HZ = 25_600.0
SAMPLE_PERIOD_MINUTES = 1.0

OPERATING_CONDITIONS = {
    1: {"speed_rpm": 2100.0, "load_kn": 12.0},
    2: {"speed_rpm": 2250.0, "load_kn": 11.0},
    3: {"speed_rpm": 2400.0, "load_kn": 10.0},
}


def _number_from_name(path: Path) -> int:
    match = re.search(r"(\d+)$", path.stem)
    if match is None:
        raise ValueError(f"snapshot filename must end in a number: {path.name}")
    return int(match.group(1))


def read_snapshot(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a raw XJTU-SY CSV, tolerating either a header or no header."""
    frame = pd.read_csv(path, header=None)
    numeric = frame.apply(pd.to_numeric, errors="coerce").dropna(how="all")
    if numeric.shape[1] < 2:
        raise ValueError(f"expected two vibration columns in {path}")
    numeric = numeric.iloc[:, :2].dropna()
    if len(numeric) < 32:
        raise ValueError(f"too few valid vibration samples in {path}: {len(numeric)}")
    return (
        numeric.iloc[:, 0].to_numpy(dtype=np.float64),
        numeric.iloc[:, 1].to_numpy(dtype=np.float64),
    )


def extract_snapshot_features(
    horizontal: np.ndarray,
    vertical: np.ndarray,
    sample_rate_hz: float = SAMPLE_RATE_HZ,
) -> dict[str, float]:
    """Extract the exact feature vector used by offline and online paths."""
    if horizontal.ndim != 1 or vertical.ndim != 1:
        raise ValueError("horizontal and vertical signals must be one-dimensional")
    if len(horizontal) != len(vertical):
        raise ValueError("horizontal and vertical signals must have equal lengths")
    if len(horizontal) < 32:
        raise ValueError("at least 32 samples per axis are required")
    if not np.isfinite(horizontal).all() or not np.isfinite(vertical).all():
        raise ValueError("signals contain NaN or infinite values")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")

    h = extract_window_features(horizontal, sample_rate_hz)
    v = extract_window_features(vertical, sample_rate_hz)
    magnitude = extract_window_features(
        np.sqrt(horizontal ** 2 + vertical ** 2), sample_rate_hz
    )
    h_rms, v_rms = h["rms"], v["rms"]
    correlation = float(np.corrcoef(horizontal, vertical)[0, 1])
    if not np.isfinite(correlation):
        correlation = 0.0
    features = {
        **{f"h_{name}": float(value) for name, value in h.items()},
        **{f"v_{name}": float(value) for name, value in v.items()},
        **{f"m_{name}": float(value) for name, value in magnitude.items()},
        "cross_axis_rms_ratio": float(h_rms / max(v_rms, 1e-12)),
        "cross_axis_correlation": correlation,
    }
    return {
        name: float(np.nan_to_num(value, nan=0.0, posinf=1e12, neginf=-1e12))
        for name, value in features.items()
    }


def discover_bearings(dataset_dir: Path) -> list[tuple[Path, str, int]]:
    """Return (directory, bearing_id, condition) for all 15 bearing folders."""
    found: list[tuple[Path, str, int]] = []
    for path in dataset_dir.rglob("Bearing*_*" if dataset_dir.exists() else "__missing__"):
        if not path.is_dir():
            continue
        match = re.fullmatch(r"Bearing([1-3])_([1-5])", path.name, re.IGNORECASE)
        if match:
            condition, bearing = int(match.group(1)), int(match.group(2))
            found.append((path, f"Bearing{condition}_{bearing}", condition))
    return sorted(found, key=lambda item: item[1])


def build_feature_table(dataset_dir: Path, output_csv: Path | None = None) -> pd.DataFrame:
    """Convert the full raw dataset into a compact, trainable feature table."""
    bearings = discover_bearings(dataset_dir)
    if not bearings:
        raise FileNotFoundError(
            f"no Bearing1_1-style folders found below {dataset_dir}; "
            "point --data-dir at the extracted XJTU-SY dataset"
        )

    records: list[dict] = []
    for directory, bearing_id, condition in bearings:
        snapshots = sorted(directory.glob("*.csv"), key=_number_from_name)
        if not snapshots:
            continue
        last_cycle = len(snapshots) - 1
        operating = OPERATING_CONDITIONS[condition]
        for cycle, path in enumerate(snapshots):
            horizontal, vertical = read_snapshot(path)
            records.append({
                "bearing_id": bearing_id,
                "condition": condition,
                "cycle": cycle,
                "elapsed_minutes": cycle * SAMPLE_PERIOD_MINUTES,
                "rul_minutes": (last_cycle - cycle) * SAMPLE_PERIOD_MINUTES,
                "speed_rpm": operating["speed_rpm"],
                "load_kn": operating["load_kn"],
                "source_file": str(path.relative_to(dataset_dir)),
                **extract_snapshot_features(horizontal, vertical),
            })
    if not records:
        raise ValueError(f"bearing folders below {dataset_dir} contain no CSV files")

    table = pd.DataFrame.from_records(records).sort_values(
        ["bearing_id", "cycle"]
    ).reset_index(drop=True)
    if output_csv is not None:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(output_csv, index=False)
    return table
