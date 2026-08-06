"""Ingestion of the NASA IMS Bearing Dataset as MaintainIQ's M1 dataset source.

Reuses the cached feature tables committed under
research/ims_bearing_baseline/outputs/ (see that folder's README for how
they were generated from the raw run-to-failure snapshots).

Modeling decision (see design/DESIGN_BASELINE.md addendum): each
(test, bearing) pair is treated as a distinct machine in our multi-machine
schema. This is a documented analogy for an academic prototype, not a claim
that these are physically separate factory machines.
"""
from pathlib import Path

import pandas as pd

from src.features.vibration import VIBRATION_FEATURE_COLUMNS

RESEARCH_DIR = Path(__file__).resolve().parents[2] / "research" / "ims_bearing_baseline"
OUTPUTS_DIR = RESEARCH_DIR / "outputs"

BEARINGS = ["B1", "B2", "B3", "B4"]

# Documented failure modes per test (IMS / Univ. of Cincinnati publications).
TESTS = {
    "test1": {"csv": OUTPUTS_DIR / "features_test1.csv", "failing_bearings": ["B3", "B4"]},
    "test2": {"csv": OUTPUTS_DIR / "features_test2.csv", "failing_bearings": ["B1"]},
    "test3": {"csv": OUTPUTS_DIR / "features_test3.csv", "failing_bearings": ["B3"]},
}


def _bearing_columns(df: pd.DataFrame, bearing: str) -> dict:
    """Map feature name -> (h_col, v_col or None) for one bearing in a wide feature CSV."""
    cols = {}
    for feat in VIBRATION_FEATURE_COLUMNS:
        h_col = f"{bearing}_h_{feat}"
        v_col = f"{bearing}_v_{feat}"
        cols[feat] = (
            h_col if h_col in df.columns else None,
            v_col if v_col in df.columns else None,
        )
    return cols


def load_test_as_long_format(test_name: str) -> pd.DataFrame:
    """Load one IMS test's wide feature CSV and tag it into MaintainIQ's
    per-machine record schema: timestamp, machine_id, sensor_id, features.
    """
    if test_name not in TESTS:
        raise ValueError(f"Unknown test '{test_name}'. Expected one of {list(TESTS)}")

    spec = TESTS[test_name]
    wide = pd.read_csv(spec["csv"], parse_dates=["timestamp"])

    records = []
    for bearing in BEARINGS:
        col_map = _bearing_columns(wide, bearing)
        if all(h is None for h, _ in col_map.values()):
            continue  # bearing not present in this test's channel layout

        row = pd.DataFrame({
            "timestamp": wide["timestamp"],
            "machine_id": f"{test_name}_{bearing}",
            "sensor_id": f"{test_name}_{bearing}_vibration",
            "source_test": test_name,
            "bearing": bearing,
            "is_documented_failure": bearing in spec["failing_bearings"],
        })
        for feat, (h_col, v_col) in col_map.items():
            row[f"vibration_h_{feat}"] = wide[h_col] if h_col else pd.NA
            if v_col:
                row[f"vibration_v_{feat}"] = wide[v_col]
        records.append(row)

    return pd.concat(records, ignore_index=True).sort_values(["machine_id", "timestamp"]).reset_index(drop=True)


def load_all_tests() -> pd.DataFrame:
    """Load and concatenate all three IMS tests into one multi-machine feature table."""
    return pd.concat(
        [load_test_as_long_format(name) for name in TESTS],
        ignore_index=True,
    )
