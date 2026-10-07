"""Quantify the snapshot-length domain gap on XJTU itself (v3 domain alignment).

For a stratified subsample of XJTU-SY raw snapshots (32768 samples) compare the production feature
vector on the full snapshot ("full", = outputs/xjtu_features.csv semantics) with FEMTO-like emulations:
  short_median6 : median over 6 feature vectors of 2560-sample chunks spread over the snapshot
  short_last1   : one 2560-sample chunk (the last)
  short_concat6 : features of the 6 chunks concatenated (15360 samples, with 5 joins)
Per feature we report the within-bearing Spearman correlation of the trajectory (emulation vs full),
the median ratio emulation/full, and the healthy-phase (rows 0..19) robust CV of each version.

Writes experiments/xjtu/v3/results/snapshot_length_study.{csv,json}. Read-only on data/src.
    python experiments/xjtu/v3/snapshot_length_study.py
"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.ingestion.xjtu_sy import extract_snapshot_features, read_snapshot  # noqa: E402

V3 = Path(__file__).resolve().parent
OUT = V3 / "results"
DATA = ROOT / "local_data" / "xjtu_full" / "XJTU-SY_Bearing_Datasets"
L, K = 2560, 6
MAX_PER_BEARING = 160


def chunks(x: np.ndarray) -> list[np.ndarray]:
    starts = np.linspace(0, len(x) - L, K).astype(int)
    return [x[s:s + L] for s in starts]


def rows_for(n: int) -> np.ndarray:
    keep = set(range(min(20, n))) | set(range(max(0, n - 20), n))
    keep |= set(np.linspace(0, n - 1, MAX_PER_BEARING - len(keep)).astype(int).tolist())
    return np.array(sorted(keep))


def one(args):
    bid, path, cycle = args
    h, v = read_snapshot(path)
    out = {}
    out["full"] = extract_snapshot_features(h, v)
    hc, vc = chunks(h), chunks(v)
    F = [extract_snapshot_features(a, b) for a, b in zip(hc, vc)]
    keys = list(F[0])
    out["short_median6"] = {k: float(np.median([f[k] for f in F])) for k in keys}
    out["short_last1"] = F[-1]
    out["short_concat6"] = extract_snapshot_features(np.concatenate(hc), np.concatenate(vc))
    return [{"bearing_id": bid, "cycle": cycle, "version": ver, **f} for ver, f in out.items()]


def main():
    tasks = []
    for d in sorted(DATA.glob("*/Bearing*_*")):
        files = sorted(d.glob("*.csv"), key=lambda p: int(p.stem))
        for i in rows_for(len(files)):
            tasks.append((d.name, files[i], int(i)))
    with Pool(3) as pool:
        res = pool.map(one, tasks, chunksize=8)
    R = pd.DataFrame([r for rr in res for r in rr])
    R.to_csv(V3 / "cache" / "snapshot_length_study_raw.csv", index=False)

    # reproduce check against the production table
    X = pd.read_csv(ROOT / "outputs/xjtu_features.csv")
    full = R[R.version == "full"].merge(X, on=["bearing_id", "cycle"], suffixes=("", "_prod"))
    repro = float(np.max(np.abs(full.h_rms - full.h_rms_prod) / full.h_rms_prod))

    feats = [c for c in R.columns if c not in ("bearing_id", "cycle", "version")]
    F = R[R.version == "full"].set_index(["bearing_id", "cycle"])
    rows = []
    for ver in ("short_median6", "short_last1", "short_concat6"):
        E = R[R.version == ver].set_index(["bearing_id", "cycle"]).loc[F.index]
        for c in feats:
            rhos, ratios = [], []
            for b in F.index.get_level_values(0).unique():
                f, e = F.loc[b, c].to_numpy(float), E.loc[b, c].to_numpy(float)
                if np.std(f) > 0 and np.std(e) > 0:
                    rhos.append(spearmanr(f, e).statistic)
                with np.errstate(divide="ignore", invalid="ignore"):
                    r = e / f
                ratios.append(np.nanmedian(r[np.isfinite(r)]) if np.isfinite(r).any() else np.nan)

            def hcv(T):
                vals = []
                for b in F.index.get_level_values(0).unique():
                    x = T.loc[b, c].to_numpy(float)[:20]
                    med = np.median(x)
                    if abs(med) > 1e-12:
                        vals.append(1.4826 * np.median(np.abs(x - med)) / abs(med))
                return float(np.median(vals)) if vals else np.nan

            rows.append(dict(version=ver, feature=c, spearman_within_median=float(np.nanmedian(rhos)) if rhos else np.nan,
                             spearman_within_min=float(np.nanmin(rhos)) if rhos else np.nan,
                             ratio_median=float(np.nanmedian(ratios)),
                             healthy_rcv_full=hcv(F), healthy_rcv_emul=hcv(E)))
    S = pd.DataFrame(rows)
    S.to_csv(OUT / "snapshot_length_study.csv", index=False)
    key = ["h_rms", "h_peak", "h_kurtosis", "h_crest_factor", "h_spectral_entropy", "h_dominant_freq",
           "h_spectral_centroid", "h_energy_5000_10000_hz_ratio", "h_envelope_rms", "h_envelope_kurtosis",
           "h_envelope_energy_100_200_hz_ratio", "h_envelope_energy_0_50_hz_ratio", "m_rms", "cross_axis_correlation"]
    summ = {"n_snapshots": int(len(F)), "repro_max_rel_err_h_rms_vs_production": repro,
            "by_version": {ver: S[(S.version == ver) & S.feature.isin(key)].set_index("feature")
                           .drop(columns="version").round(4).to_dict("index")
                           for ver in ("short_median6", "short_last1", "short_concat6")},
            "fraction_features_spearman_ge_0.9": S.groupby("version").spearman_within_median
            .apply(lambda s: float((s >= 0.9).mean())).to_dict(),
            "median_healthy_rcv_all_features": S.groupby("version")[["healthy_rcv_full", "healthy_rcv_emul"]]
            .median().round(4).to_dict("index")}
    (OUT / "snapshot_length_study.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ, indent=1)[:6000])


if __name__ == "__main__":
    main()
