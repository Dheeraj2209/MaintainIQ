# Phase 2 — Batch Backfill Ingestion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A batch loader turns the XJTU-SY feature table into canonical `machines` + `readings` rows — idempotently, with zero NULLs on the readings NOT NULL columns — reusing the single feature-extraction home (`build_feature_table` → `extract_snapshot_features`), never re-implementing feature math.

**Architecture:** One new module `src/ingestion/backfill.py`. A pure core (`backfill_from_table`) maps an already-built feature DataFrame to canonical row dicts and writes them via the Phase 1 `insert_machines`/`insert_readings` helpers (both already `INSERT OR IGNORE`, idempotent on `UNIQUE(machine_id, cycle)`). A thin I/O wrapper (`backfill_dataset`) calls `build_feature_table(dataset_dir)` then delegates to the core. Row-insert counts are measured with `sqlite3.Connection.total_changes` deltas.

**Tech Stack:** Python 3 · SQLite (raw SQL, Phase 1 canonical schema) · pandas · numpy · pytest + FastAPI TestClient.

**Design authority:** `docs/superpowers/specs/2026-08-06-xjtu-sy-ml-integration-design.md`. If this plan and the spec conflict, the spec wins.

## Global Constraints

Every task implicitly includes these (copied from the roadmap / spec):

- **Canonical dataset is XJTU-SY.** XJTU rows populate every column with zero NULLs. `dataset` column defaults to `'xjtu_sy'`; the backfill accepts a `dataset` kwarg for the future isolation seam.
- **No synthetic temperature** anywhere — the canonical schema has no thermal channel; do not add one.
- **RUL is in minutes.** `rul_minutes` is the unit of record. Backfill copies `rul_minutes` straight from the feature table (which derives it from remaining snapshots). No `rul_hours`.
- **Time is `elapsed_minutes` + `cycle`.** `cycle` is the per-machine snapshot index; `UNIQUE(machine_id, cycle)` is the idempotency key. A synthesized `timestamp` is deterministic (fixed epoch + `elapsed_minutes`).
- **Feature extraction has exactly one home:** `src.ingestion.xjtu_sy.extract_snapshot_features`, reached only through `build_feature_table`. The backfill MUST NOT compute any vibration feature itself — it only reshapes an existing feature table into DB rows.
- **No new heavyweight deps.** numpy/pandas only; nothing new.
- **TDD, DRY, YAGNI, frequent commits.** Failing test first, minimal code, green, commit. One deliverable per task.
- **Code-review gate between phases (user mandate, verbatim):** "before integrating any two modules use the code review plugin or any relevant plugins then review then integrate them." This phase ends with a `code-review` pass on its diff before Phase 3 begins.

---

## Committed Interfaces This Phase Consumes (verified against current code)

Do not trust prose over these — they were read from the committed tree at plan time.

**`src/ingestion/xjtu_sy.py`:**
- `SAMPLE_RATE_HZ = 25_600.0`, `SAMPLE_PERIOD_MINUTES = 1.0`.
- `OPERATING_CONDITIONS = {1: {"speed_rpm": 2100.0, "load_kn": 12.0}, 2: {...2250,11}, 3: {...2400,10}}`.
- `build_feature_table(dataset_dir: Path, output_csv: Path | None = None) -> pd.DataFrame`. Each record (row) has keys:
  `bearing_id` (str, e.g. `"Bearing1_1"`), `condition` (int 1–3), `cycle` (int, 0-based), `elapsed_minutes` (float = `cycle * 1.0`), `rul_minutes` (float, descending to 0.0), `speed_rpm` (float), `load_kn` (float), `source_file` (str), plus the full `extract_snapshot_features` dict: keys `h_*`, `v_*`, `m_*` (each the ~40 keys from `extract_window_features`, e.g. `h_rms`, `h_kurtosis`, `v_rms`, `v_kurtosis`, `m_rms`, ...), and `cross_axis_rms_ratio`, `cross_axis_correlation`. All feature values are Python floats.
- `extract_snapshot_features(...)` returns `dict[str, float]` — reached only via `build_feature_table`.

**`src/storage/db.py` (Phase 1 canonical helpers — ALREADY accept the canonical dict; DO NOT modify db.py this phase):**
- `insert_machines(conn, machines: list[dict])` — `INSERT OR IGNORE`. Keys: required `machine_id`; optional `bearing_id`, `operating_condition`, `speed_rpm`, `load_kn`, `dataset` (default `'xjtu_sy'`), `is_documented_failure` (default 0). Commits internally.
- `insert_readings(conn, readings: list[dict])` — `INSERT OR IGNORE`, idempotent on `(machine_id, cycle)`. Canonical reading keys (`_READING_COLUMNS`): `machine_id, timestamp, cycle, elapsed_minutes, speed_rpm, load_kn, sample_rate_hz, vibration_h_rms, vibration_h_kurtosis, vibration_v_rms, vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation, rul_minutes, features_json, dataset`. Missing keys are filled to `None`; `dataset` defaults to `'xjtu_sy'`; `features_json` defaults to `'{}'`. Commits internally.
- `readings` NOT NULL columns (must be populated by backfill): `machine_id, timestamp, cycle, elapsed_minutes, speed_rpm, load_kn, sample_rate_hz, vibration_h_rms, vibration_h_kurtosis, vibration_v_rms, vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation, features_json, dataset`. (`rul_minutes` is nullable but backfill always sets it.)
- `init_schema(conn)` — installs the canonical schema via the migration runner.

**`src/api/routes/machines.py`:** `GET /machines/{machine_id}/trends?metric=<m>&limit=<n>` — valid metrics: `vibration_h_rms`, `vibration_h_kurtosis`, `vibration_v_rms`, `vibration_v_kurtosis`, `cross_axis_rms_ratio`, `cross_axis_correlation`, `rul_minutes`. Returns `list[{timestamp, value}]` oldest-first. Behind auth (login as a demo admin).

**`src/ingestion/__init__.py`** already exists and is empty — do not modify it. `tests/ingestion/` does not exist yet — create it as a package (mirrors `tests/storage/__init__.py`).

---

## File Structure

- Create: `src/ingestion/backfill.py` — `BackfillResult`, `BACKFILL_EPOCH`, `backfill_from_table`, `backfill_dataset`.
- Create: `tests/ingestion/__init__.py` (empty package marker).
- Create: `tests/ingestion/test_backfill.py` — all Phase 2 tests.
- **Do NOT modify** `src/storage/db.py` — its Phase 1 helpers already accept the canonical dict. (The roadmap's "Modify db.py" item is already satisfied by Phase 1; touching it would be scope creep.)

---

### Task 1: Core batch backfill (`backfill_from_table` + `BackfillResult`)

**Files:**
- Create: `src/ingestion/backfill.py`
- Create: `tests/ingestion/__init__.py`
- Create: `tests/ingestion/test_backfill.py`

**Interfaces:**
- Consumes: `src.ingestion.xjtu_sy.SAMPLE_RATE_HZ`; `src.storage.db.insert_machines`, `insert_readings`, `init_schema`; a feature DataFrame shaped like `build_feature_table`'s output.
- Produces:
  - `BackfillResult` dataclass: `machines_written: int`, `readings_written: int`, `skipped: int`.
  - `BACKFILL_EPOCH: datetime` — the fixed UTC anchor for synthesized timestamps.
  - `backfill_from_table(conn, feature_df: pd.DataFrame, *, dataset: str = "xjtu_sy") -> BackfillResult` — one `machines` row per `bearing_id` (`operating_condition=condition`, `speed_rpm`/`load_kn` from the row, `is_documented_failure=1`, given `dataset`); one `readings` row per record with the six promoted vibration columns copied out and `features_json` = the full `extract_snapshot_features` dict (identity/label columns excluded); idempotent via the helpers' `INSERT OR IGNORE`.

- [ ] **Step 1: Create the test package marker**

Create `tests/ingestion/__init__.py`:

```python
```
(empty file — just create it so pytest treats `tests/ingestion` as a package, matching `tests/storage/__init__.py`.)

- [ ] **Step 2: Write the failing tests for the core**

Create `tests/ingestion/test_backfill.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/ingestion/test_backfill.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.ingestion.backfill'` (import error at collection).

- [ ] **Step 4: Write the minimal implementation**

Create `src/ingestion/backfill.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/ingestion/test_backfill.py -q`
Expected: PASS (9 tests).

- [ ] **Step 6: Run the full backend suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS — the prior green count (102) plus the 9 new tests = 111.

- [ ] **Step 7: Commit**

```bash
git add src/ingestion/backfill.py tests/ingestion/__init__.py tests/ingestion/test_backfill.py
git commit -m "feat: batch backfill core (feature table -> machines + readings)"
```

---

### Task 2: Dataset-directory wrapper + end-to-end acceptance

**Files:**
- Modify: `src/ingestion/backfill.py` (already contains `backfill_dataset` from Task 1 — this task adds its tests; no code change expected unless a test surfaces a defect)
- Modify: `tests/ingestion/test_backfill.py` (append wrapper + end-to-end + trends-endpoint tests)

**Interfaces:**
- Consumes: `src.ingestion.backfill.backfill_dataset`, `backfill_from_table`; `src.ingestion.xjtu_sy.build_feature_table`; the existing `GET /machines/{id}/trends` endpoint and auth (`src.auth.seed.DEMO_USERS`, `src.auth.security.hash_password`).
- Produces: no new code contract — proves the wrapper delegates correctly and that backfilled rows are queryable end-to-end through the real trends endpoint (roadmap acceptance).

- [ ] **Step 1: Write the failing wrapper + end-to-end tests**

Append to `tests/ingestion/test_backfill.py` (add the two new imports at the top of the file, then the fixture and tests at the bottom):

Add to the import block at the top of the file:

```python
import numpy as np

from src.ingestion import backfill as backfill_module
from src.ingestion.backfill import backfill_dataset
```

Append at the end of the file:

```python
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
    with TestClient(app) as test_client:
        resp = test_client.post("/api/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        yield test_client
    app.dependency_overrides.clear()


def test_trends_endpoint_returns_points_for_backfilled_machine(backfilled_client):
    resp = backfilled_client.get(
        "/api/machines/Bearing1_1/trends", params={"metric": "vibration_h_rms"}
    )
    assert resp.status_code == 200, resp.text
    points = resp.json()
    # Three snapshots, returned oldest-first for left-to-right charting.
    assert [p["value"] for p in points] == [0.5, 0.6, 0.7]
    assert all(p["timestamp"] for p in points)
```

- [ ] **Step 2: Run the new tests to verify they pass**

Run: `python -m pytest tests/ingestion/test_backfill.py -q`
Expected: PASS (12 tests total — 9 from Task 1 + 3 new).

Note: `backfill_dataset` and `backfill_from_table` already exist from Task 1, so these tests should pass immediately. If any fails, fix the defect it surfaced in `src/ingestion/backfill.py` (minimal change), re-run, and include the fix in this task's commit. Do not weaken a test to make it pass.

- [ ] **Step 3: Run the full backend suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS — 111 + 3 = 114 tests (adjust the arithmetic only if Task 1's final count differed; the point is: prior count + 3, zero failures).

- [ ] **Step 4: Acceptance greps**

Confirm no feature math was re-implemented in the backfill module (it must only reference the promoted keys / call the helpers, never compute FFT/RMS/kurtosis):

Run: `grep -nE "rfft|kurtosis|np\.sqrt|hilbert|extract_window_features" src/ingestion/backfill.py`
Expected: no output (backfill computes no features).

Confirm the single feature-extraction home is honored (backfill reaches features only via `build_feature_table`):

Run: `grep -nE "build_feature_table|extract_snapshot_features" src/ingestion/backfill.py`
Expected: one hit — the `build_feature_table` import/use; NO direct `extract_snapshot_features` call.

- [ ] **Step 5: Commit**

```bash
git add tests/ingestion/test_backfill.py src/ingestion/backfill.py
git commit -m "test: backfill dataset wrapper + end-to-end trends acceptance"
```

---

## Review Gate (Phase 2 → 3)

Run `code-review` on the Phase 2 diff. Focus (from the roadmap):
- **Idempotency:** re-running the backfill writes zero new rows and reports them as `skipped`, guaranteed by `UNIQUE(machine_id, cycle)` + `INSERT OR IGNORE` (not by ad-hoc existence checks).
- **Zero NULLs on XJTU rows:** every `readings` NOT NULL column is populated for real-extraction rows.
- **Feature math not re-implemented:** backfill calls `build_feature_table` only; the six promoted columns are copied out of the feature dict; no vibration feature is computed in `backfill.py`.
- **Deterministic timestamps:** fixed `BACKFILL_EPOCH` + `elapsed_minutes`; documented.
- **No db.py changes:** the Phase 1 insert helpers were reused, not duplicated or modified.

## Acceptance (roadmap, verbatim)
- Backfilling a small fixture dataset twice yields identical row counts the second time (all skipped). ✔ `test_rerun_is_idempotent_all_skipped`, `test_backfill_dataset_end_to_end_populates_zero_null_readings`.
- Every `readings` NOT NULL column is populated. ✔ `test_all_not_null_reading_columns_are_populated`, end-to-end NULL sweep.
- Trends endpoint returns real points for a backfilled machine. ✔ `test_trends_endpoint_returns_points_for_backfilled_machine`.

## Self-Review Notes (author, at plan time)
- **Spec coverage:** the roadmap's Phase 2 file map, interfaces, mapping rules, review gate, and all three acceptance criteria each map to a task/test above.
- **db.py "Modify" item:** already satisfied by Phase 1 (`insert_machines`/`insert_readings` accept the canonical dict). Called out as no-op to prevent scope creep.
- **numpy-scalar hazard:** `DataFrame.to_dict("records")` yields numpy scalars that break both `json.dumps` and sqlite3 binding — every value is explicitly cast to `int`/`float`; `_feature_dict` floats the whole vector.
- **Type consistency:** `BackfillResult`, `BACKFILL_EPOCH`, `backfill_from_table`, `backfill_dataset` names/signatures are identical across the module, tests, and this document.
