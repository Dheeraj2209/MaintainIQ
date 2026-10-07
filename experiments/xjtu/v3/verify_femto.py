"""Verify the extracted FEMTO/PRONOSTIA (IEEE PHM 2012) dataset.

Reads local_data/femto/extracted/{Learning_set,Full_Test_Set,Test_set} and
writes experiments/xjtu/v3/results/femto_inventory.json with, per bearing:
file counts vs published counts, index contiguity, delimiter, row/column
shape, timestamp span and inter-snapshot spacing, NaN counts, and whether
the truncated Test_set is a byte-identical prefix of the Full_Test_Set.

Usage: python experiments/xjtu/v3/verify_femto.py
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "local_data" / "femto" / "extracted"
OUT = Path(__file__).resolve().parent / "results" / "femto_inventory.json"

# Published counts (IEEE PHM 2012 challenge details PDF / FEMTO documentation).
PUBLISHED_ACC = {
    "Learning_set": {"Bearing1_1": 2803, "Bearing1_2": 871, "Bearing2_1": 911,
                     "Bearing2_2": 797, "Bearing3_1": 515, "Bearing3_2": 1637},
    "Full_Test_Set": {"Bearing1_3": 2375, "Bearing1_4": 1428, "Bearing1_5": 2463,
                      "Bearing1_6": 2448, "Bearing1_7": 2259, "Bearing2_3": 1955,
                      "Bearing2_4": 751, "Bearing2_5": 2311, "Bearing2_6": 701,
                      "Bearing2_7": 230, "Bearing3_3": 434},
    "Test_set": {"Bearing1_3": 1802, "Bearing1_4": 1139, "Bearing1_5": 2302,
                 "Bearing1_6": 2302, "Bearing1_7": 1502, "Bearing2_3": 1202,
                 "Bearing2_4": 612, "Bearing2_5": 2002, "Bearing2_6": 572,
                 "Bearing2_7": 172, "Bearing3_3": 352},
}
CONDITION = {"1": "1800rpm/4000N", "2": "1650rpm/4200N", "3": "1500rpm/5000N"}


def _read(path: Path):
    txt = path.read_text()
    delim = ";" if ";" in txt.splitlines()[0] else ","
    arr = np.loadtxt(path, delimiter=delim, ndmin=2)
    return delim, arr


def _seconds(row) -> float:
    # acc: h, m, s, microsecond; temp: h, m, s, tenth-of-second
    return row[0] * 3600 + row[1] * 60 + row[2]


def scan_bearing(args):
    split, bearing = args
    d = DATA / split / bearing
    out = {"split": split, "bearing": bearing,
           "condition": CONDITION[bearing[7]]}
    for kind in ("acc", "temp"):
        files = sorted(d.glob(f"{kind}_*.csv"))
        idx = [int(re.findall(r"\d+", f.stem)[0]) for f in files]
        info = {"n_files": len(files)}
        if not files:
            out[kind] = info
            continue
        info["index_min"], info["index_max"] = min(idx), max(idx)
        info["index_contiguous"] = idx == list(range(1, len(files) + 1))
        delims, shapes, nan_files, starts = Counter(), Counter(), 0, []
        absmax = 0.0
        for f in files:
            delim, arr = _read(f)
            delims[delim] += 1
            shapes[f"{arr.shape[0]}x{arr.shape[1]}"] += 1
            if np.isnan(arr).any():
                nan_files += 1
            starts.append(_seconds(arr[0]))
            if kind == "acc":
                absmax = max(absmax, float(np.abs(arr[:, 4:6]).max()))
        st = np.array(starts)
        # day wrap: clock resets at midnight
        st = st + 86400 * np.cumsum(np.r_[0, np.diff(st) < -3600])
        gaps = np.diff(st)
        info.update({
            "delimiters": dict(delims), "shapes": dict(shapes),
            "files_with_nan": nan_files,
            "span_s": float(st[-1] - st[0]) if len(st) > 1 else 0.0,
            "gap_s_median": float(np.median(gaps)) if len(gaps) else None,
            "gap_s_min": float(gaps.min()) if len(gaps) else None,
            "gap_s_max": float(gaps.max()) if len(gaps) else None,
            "n_gaps_not_10s": int(np.sum(np.abs(gaps - 10) > 1)) if kind == "acc" and len(gaps) else None,
        })
        if kind == "acc":
            info["abs_acc_max_g"] = absmax
            pub = PUBLISHED_ACC[split][bearing]
            info["published_n"] = pub
            info["matches_published"] = len(files) == pub
            h = hashlib.sha256()
            for f in files:
                h.update(f.name.encode()); h.update(f.read_bytes())
            info["sha256_concat"] = h.hexdigest()
        out[kind] = info
    return out


def prefix_check(bearing: str) -> dict:
    t = DATA / "Test_set" / bearing
    f = DATA / "Full_Test_Set" / bearing
    res = {}
    for kind in ("acc", "temp"):
        tf = sorted(t.glob(f"{kind}_*.csv"))
        mism = [p.name for p in tf if not (f / p.name).exists()
                or (f / p.name).read_bytes() != p.read_bytes()]
        res[kind] = {"n_test": len(tf), "n_mismatch_vs_full": len(mism),
                     "first_mismatches": mism[:5]}
    return res


def main():
    jobs = [(s, b) for s in PUBLISHED_ACC for b in PUBLISHED_ACC[s]]
    missing = [j for j in jobs if not (DATA / j[0] / j[1]).is_dir()]
    jobs = [j for j in jobs if j not in missing]
    with ProcessPoolExecutor(max_workers=3) as ex:
        rows = list(ex.map(scan_bearing, jobs))
        prefix = dict(zip(PUBLISHED_ACC["Test_set"],
                          ex.map(prefix_check, PUBLISHED_ACC["Test_set"])))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"missing_dirs": missing, "bearings": rows,
                               "test_set_prefix_of_full": prefix}, indent=1))
    for r in rows:
        a, t = r["acc"], r["temp"]
        print(f"{r['split']:14s} {r['bearing']} acc={a['n_files']:5d} pub={a['published_n']:5d} "
              f"ok={a['matches_published']} contig={a['index_contiguous']} delim={a['delimiters']} "
              f"shape={a['shapes']} nan={a['files_with_nan']} span_h={a['span_s']/3600:.2f} "
              f"gap med/min/max={a['gap_s_median']}/{a['gap_s_min']}/{a['gap_s_max']} "
              f"n!=10s={a['n_gaps_not_10s']} |a|max={a['abs_acc_max_g']:.1f} "
              f"temp={t['n_files']} {t.get('delimiters','')} {t.get('shapes','')} "
              f"tcontig={t.get('index_contiguous')}")
    for b, p in prefix.items():
        print("prefix", b, p)
    print("missing", missing)


if __name__ == "__main__":
    main()
