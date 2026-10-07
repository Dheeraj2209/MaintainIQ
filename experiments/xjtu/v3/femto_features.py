"""FEMTO/PRONOSTIA -> XJTU-SY-compatible per-minute feature table (v3 domain alignment).

Produces a table with EXACTLY the columns (and column order) of outputs/xjtu_features.csv by calling
the production extractor src.ingestion.xjtu_sy.extract_snapshot_features on FEMTO vibration
snapshots. Design choices are documented in experiments/xjtu/v3/DOMAIN_ALIGNMENT.md.

Pipeline (run from repo root):
    python experiments/xjtu/v3/femto_features.py            # extract (cached) + aggregate + combine
    python experiments/xjtu/v3/femto_features.py --refresh  # re-extract snapshot features

Stage 1 (expensive, cached): for every FEMTO acc file (2560 samples, 0.1 s, every 10 s) compute
  * the production feature vector on the single 2560-sample snapshot      -> cache/femto_snapshot_features.csv
  * the production feature vector on the 6 snapshots of one minute
    concatenated (15360 samples)                                           -> cache/femto_concat6_features.csv
Stage 2 (cheap): aggregate to ONE ROW PER MINUTE (XJTU cadence). Minute groups are aligned to the END
  of the run so the last row contains the final snapshot and RUL is an exact integer number of minutes;
  a leading incomplete group (< 6 snapshots, < 1 min of the healthy start) is dropped. Each row only
  uses snapshots recorded up to and including its own (last) snapshot -> causal.
  Modes:  concat6 (PRIMARY): features of the 6 snapshots of the minute concatenated (15360 samples)
          median6           : per-feature median over the 6 per-snapshot (2560-sample) vectors
          last1             : subsample, the minute's last snapshot only
Stage 3: combined_features.csv = XJTU rows (unchanged) + FEMTO rows with bearing_id prefixed
  "FEMTO_" and condition ids 4/5/6.

Read-only on src/, models/, outputs/, local_data/. Writes only under experiments/xjtu/v3/cache/.
"""
from __future__ import annotations

import argparse
import re
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.ingestion.xjtu_sy import SAMPLE_RATE_HZ, extract_snapshot_features  # noqa: E402

V3 = Path(__file__).resolve().parent
CACHE = V3 / "cache"
DATA = ROOT / "local_data" / "femto" / "extracted"
XJTU_TABLE = ROOT / "outputs" / "xjtu_features.csv"
SPLITS = ("Learning_set", "Full_Test_Set")          # complete run-to-failure records only
SNAPS_PER_MIN = 6                                   # 10 s cadence -> 6 snapshots per minute
FEMTO_SAMPLES = 2560
FEMTO_FS = 25_600.0
assert FEMTO_FS == SAMPLE_RATE_HZ                   # same sampling rate as XJTU-SY

# FEMTO operating conditions -> distinct condition ids (XJTU uses 1..3). Load 4000/4200/5000 N.
FEMTO_CONDITIONS = {
    1: {"condition": 4, "speed_rpm": 1800.0, "load_kn": 4.0},
    2: {"condition": 5, "speed_rpm": 1650.0, "load_kn": 4.2},
    3: {"condition": 6, "speed_rpm": 1500.0, "load_kn": 5.0},
}
META_COLUMNS = ["bearing_id", "condition", "cycle", "elapsed_minutes", "rul_minutes",
                "speed_rpm", "load_kn", "source_file"]
MODES = ("median6", "last1", "concat6")
PRIMARY_MODE = "concat6"   # chosen from snapshot_length_study.py (see DOMAIN_ALIGNMENT.md)


def xjtu_columns() -> list[str]:
    return list(pd.read_csv(XJTU_TABLE, nrows=0).columns)


def read_acc(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """FEMTO acc file: h, m, s, us, horizontal g, vertical g. Delimiter sniffed per file (',' or ';')."""
    with open(path, "r") as fh:
        first = fh.readline()
    arr = np.loadtxt(path, delimiter=";" if ";" in first else ",", ndmin=2)
    if arr.shape != (FEMTO_SAMPLES, 6) or not np.isfinite(arr).all():
        raise ValueError(f"unexpected FEMTO acc file {path}: shape {arr.shape}")
    return arr[:, 4].astype(np.float64), arr[:, 5].astype(np.float64)


def discover() -> list[tuple[str, Path, int]]:
    out = []
    for split in SPLITS:
        for d in sorted((DATA / split).glob("Bearing*_*")):
            m = re.fullmatch(r"Bearing([1-3])_([1-7])", d.name)
            if m and d.is_dir():
                out.append((split, d, int(m.group(1))))
    if len(out) != 17:
        raise FileNotFoundError(f"expected 17 FEMTO bearings under {DATA}, found {len(out)}")
    return out


def _acc_files(d: Path) -> list[Path]:
    files = sorted(d.glob("acc_*.csv"), key=lambda p: int(p.stem.split("_")[1]))
    idx = [int(p.stem.split("_")[1]) for p in files]
    if idx != list(range(1, len(files) + 1)):
        raise ValueError(f"non-contiguous acc files in {d}")
    return files


def minute_groups(n_snap: int) -> list[np.ndarray]:
    """End-aligned complete groups of 6 snapshot indices (0-based), oldest first."""
    n_min = n_snap // SNAPS_PER_MIN
    start = n_snap - n_min * SNAPS_PER_MIN          # dropped leading incomplete group size
    return [np.arange(start + SNAPS_PER_MIN * g, start + SNAPS_PER_MIN * (g + 1)) for g in range(n_min)]


def extract_bearing(args) -> tuple[pd.DataFrame, pd.DataFrame]:
    split, d, cond = args
    files = _acc_files(d)
    bid = f"FEMTO_{d.name}"
    sigs = [read_acc(f) for f in files]
    snap = []
    for i, (h, v) in enumerate(sigs):
        snap.append({"bearing_id": bid, "split": split, "femto_condition": cond, "snap": i,
                     "file": f"{split}/{d.name}/{files[i].name}", **extract_snapshot_features(h, v)})
    conc = []
    for g, idx in enumerate(minute_groups(len(sigs))):
        h = np.concatenate([sigs[i][0] for i in idx]); v = np.concatenate([sigs[i][1] for i in idx])
        conc.append({"bearing_id": bid, "minute": g, **extract_snapshot_features(h, v)})
    return pd.DataFrame(snap), pd.DataFrame(conc)


def stage1(refresh: bool = False, n_jobs: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    CACHE.mkdir(parents=True, exist_ok=True)
    ps, pc = CACHE / "femto_snapshot_features.csv", CACHE / "femto_concat6_features.csv"
    if ps.exists() and pc.exists() and not refresh:
        return pd.read_csv(ps), pd.read_csv(pc)
    with Pool(n_jobs) as pool:
        res = pool.map(extract_bearing, discover(), chunksize=1)
    S = pd.concat([r[0] for r in res], ignore_index=True)
    C = pd.concat([r[1] for r in res], ignore_index=True)
    S.to_csv(ps, index=False); C.to_csv(pc, index=False)
    return S, C


def aggregate(S: pd.DataFrame, C: pd.DataFrame, mode: str) -> pd.DataFrame:
    """One row per minute in XJTU column order/semantics."""
    cols = xjtu_columns()
    feat_cols = [c for c in cols if c not in META_COLUMNS]
    rows = []
    for bid, g in S.groupby("bearing_id", sort=True):
        g = g.sort_values("snap").reset_index(drop=True)
        groups = minute_groups(len(g))
        n_min = len(groups)
        cond = FEMTO_CONDITIONS[int(g.femto_condition.iloc[0])]
        X = g[feat_cols].to_numpy(float)
        if mode == "concat6":
            cg = C[C.bearing_id == bid].sort_values("minute")
            assert len(cg) == n_min
            F = cg[feat_cols].to_numpy(float)
        for m, idx in enumerate(groups):
            if mode == "median6":
                f = np.median(X[idx], axis=0)
            elif mode == "last1":
                f = X[idx[-1]]
            else:
                f = F[m]
            rows.append({
                "bearing_id": bid, "condition": cond["condition"], "cycle": m,
                "elapsed_minutes": float(m), "rul_minutes": float(n_min - 1 - m),
                "speed_rpm": cond["speed_rpm"], "load_kn": cond["load_kn"],
                "source_file": f"{g.file.iloc[idx[0]]}..{Path(g.file.iloc[idx[-1]]).name}",
                **dict(zip(feat_cols, f)),
            })
    T = pd.DataFrame(rows)[cols].sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    return T


def check_schema(T: pd.DataFrame) -> None:
    X = pd.read_csv(XJTU_TABLE, nrows=50)
    assert list(T.columns) == list(X.columns), "column mismatch"
    for col in X.columns:
        assert pd.api.types.is_numeric_dtype(T[col]) == pd.api.types.is_numeric_dtype(X[col]), col
    num = T.select_dtypes("number")
    assert np.isfinite(num.to_numpy()).all(), "non-finite values"
    for b, g in T.groupby("bearing_id"):
        assert (g.cycle.to_numpy() == np.arange(len(g))).all()
        assert g.rul_minutes.iloc[-1] == 0 and (np.diff(g.rul_minutes) == -1).all()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--n-jobs", type=int, default=3)
    a = ap.parse_args()
    S, C = stage1(a.refresh, min(a.n_jobs, 3))
    tables = {}
    for mode in MODES:
        T = aggregate(S, C, mode)
        check_schema(T)
        T.to_csv(CACHE / f"femto_features_{mode}.csv", index=False)
        tables[mode] = T
    T = tables[PRIMARY_MODE]
    T.to_csv(CACHE / "femto_features.csv", index=False)
    # round_trip parsing so the XJTU rows are re-written with byte-identical numeric text
    X = pd.read_csv(XJTU_TABLE, float_precision="round_trip")
    assert not set(X.bearing_id) & set(T.bearing_id) and not set(X.condition) & set(T.condition)
    P = pd.concat([X, T], ignore_index=True)[list(X.columns)]
    P.to_csv(CACHE / "combined_features.csv", index=False)
    life = T.groupby("bearing_id").size()
    print(f"FEMTO rows={len(T)} bearings={T.bearing_id.nunique()} mode={PRIMARY_MODE}; "
          f"combined rows={len(P)} bearings={P.bearing_id.nunique()}")
    print(life.to_string())


if __name__ == "__main__":
    main()
