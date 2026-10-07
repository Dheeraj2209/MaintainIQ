"""Experiment: physics-based raw-signal snapshot features for the XJTU-SY RUL model.

Steps (from repo root):
    python experiments/xjtu/exp_raw-signal-features.py extract        # 9216 snapshots, 4 workers, cached
    python experiments/xjtu/exp_raw-signal-features.py run V1 V2      # full LOBO per variant (harness)
    python experiments/xjtu/exp_raw-signal-features.py summarize      # merge -> results/raw-signal-features.json

Features come from experiments/xjtu/rawsig_features.py (envelope-spectrum fault-line SNRs at
FTF/BSF/BPFO/BPFI orders of the *measured* shaft speed, shaft 1x/2x/3x relative amplitude,
4-band filter-bank RMS ratio + kurtosis, kurtogram-lite adaptive-band envelope spectrum).
Causal context for them (rs_* columns) uses only the commissioning baseline (first 20
snapshots, median) and windows <= 20 snapshots, i.e. what RealTimeRULPredictor retains.
"""
from __future__ import annotations

import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from experiments.xjtu.harness import (  # noqa: E402
    compute_report, evaluate, load_baseline, load_context, save_oof, summary_metrics,
)
from experiments.xjtu import rawsig_features as rsf  # noqa: E402
from src.training.xjtu_rul import classifier_feature_columns, feature_columns  # noqa: E402

CACHE = HERE / "cache" / "raw_features.parquet"
PARTS = HERE / "results" / "raw-signal-features_parts"
OUT_JSON = HERE / "results" / "raw-signal-features.json"
OUT_OOF = HERE / "results" / "raw-signal-features_oof.csv"
BASELINE_WINDOW = 20

AXIS_SRC = ["shaft_1x_rel", "shaft_2x_rel",
            *[f"bandrms_{lo}_{hi}_rel" for lo, hi in rsf.BANDS],
            *[f"bandkurt_{lo}_{hi}" for lo, hi in rsf.BANDS], "sk_max"]
X_SRC = [f"{p}_{n}_sum_snr" for p in ("env", "envsk") for n in rsf.ORDERS] + [
    f"{p}_{k}" for p in ("env", "envsk") for k in ("BPFI_sb_snr", "fault_max_snr", "shaft_1x_snr")
] + ["envsk_kurtosis"]
RS_SOURCES = [f"rs_{a}_{s}" for a in ("h", "v") for s in AXIS_SRC] + [f"rs_x_{s}" for s in X_SRC]
SIGNED = {c for c in RS_SOURCES if "kurt" in c or c.endswith("sk_max")}  # fisher kurtosis can be <0


# ------------------------------------------------------------------ extraction
def extract_all() -> pd.DataFrame:
    if CACHE.exists():
        return pd.read_parquet(CACHE)
    t = pd.read_csv(REPO / "outputs" / "xjtu_features.csv",
                    usecols=["bearing_id", "cycle", "condition", "source_file"])
    jobs = list(t[["bearing_id", "cycle", "condition", "source_file"]].itertuples(index=False, name=None))
    t0 = time.perf_counter()
    rows = []
    with Pool(4) as pool:
        for i, r in enumerate(pool.imap(rsf.extract_one, jobs, chunksize=32), 1):
            rows.append(r)
            if i % 1000 == 0:
                print(f"{i}/{len(jobs)} {time.perf_counter() - t0:.0f}s", flush=True)
    df = pd.DataFrame(rows).sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    print(f"extracted {df.shape} in {time.perf_counter() - t0:.0f}s")
    return df


def rs_context(raw: pd.DataFrame) -> pd.DataFrame:
    """Causal context for rs_* sources (same baseline rule as add_past_context)."""
    raw = raw.sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    out = {}
    for _, idx in raw.groupby("bearing_id", sort=False).groups.items():
        idx = np.asarray(list(idx))
        for c in RS_SOURCES:
            x = raw.loc[idx, c].astype(float).reset_index(drop=True)
            bl = x.expanding(min_periods=1).median()
            if len(bl) > BASELINE_WINDOW:
                bl.iloc[BASELINE_WINDOW:] = bl.iloc[BASELINE_WINDOW - 1]
            if c in SIGNED:
                rel = x - bl
            else:
                rel = np.log(x.clip(lower=1e-9) / bl.clip(lower=1e-9))
            cols = {
                f"{c}__raw": x,
                f"{c}__raw_mean5": x.rolling(5, min_periods=1).mean(),
                f"{c}__std20": x.rolling(20, min_periods=1).std(ddof=0).fillna(0),
                f"{c}__rel": rel,
                f"{c}__rel_mean5": rel.rolling(5, min_periods=1).mean(),
                f"{c}__rel_mean20": rel.rolling(20, min_periods=1).mean(),
                f"{c}__rel_trend20": ((rel - rel.shift(19)) / 19).fillna(0),
            }
            for k, v in cols.items():
                out.setdefault(k, np.zeros(len(raw)))[idx] = v.to_numpy()
    res = pd.concat([raw[["bearing_id", "cycle"]], pd.DataFrame(out)], axis=1)
    return res


def build_table() -> pd.DataFrame:
    ctx = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    rs = rs_context(extract_all())
    merged = ctx.merge(rs, on=["bearing_id", "cycle"], how="left", validate="one_to_one")
    assert len(merged) == len(ctx) and not merged[[c for c in rs.columns if c.startswith("rs_")]].isna().any().any()
    return merged


# ---------------------------------------------------------------- feature sets
def is_rs(c): return c.startswith("rs_")
def RS_REL(t): return [c for c in t.columns if is_rs(c) and "__rel" in c]
def RS_ABS(t): return [c for c in t.columns if is_rs(c) and ("__raw" in c or "__std20" in c)]
def BASE_CLF(t): return [c for c in classifier_feature_columns(t) if not is_rs(c)]
def BASE_REG(t): return [c for c in feature_columns(t) if not is_rs(c)]
def REL(t):
    return [c for c in BASE_CLF(t) if c not in ("speed_rpm", "load_kn")
            and ("baseline_ratio" in c or "_std_" in c or "_trend_" in c)]


# energy-distribution sources only (shaft-order amplitude and band RMS, relative to total RMS).
# SNR / kurtosis sources are excluded here because their first-20 baseline is inflated by
# run-in transients (healthy rows sit below baseline on most bearings) - found post hoc
# from V3/V4 OOF, so V5/V6 carry selection bias.
ENERGY_SRC = [f"rs_{a}_{s}" for a in ("h", "v") for s in
              ("shaft_1x_rel", "shaft_2x_rel", *[f"bandrms_{lo}_{hi}_rel" for lo, hi in rsf.BANDS])]
def RS_ENERGY_REL(t): return [c for c in RS_REL(t) if c.split("__")[0] in ENERGY_SRC]


VARIANTS = {
    # name: (classifier feature fn, regressor feature fn, description)
    "V1_base_plus_rs": (lambda t: BASE_CLF(t) + RS_REL(t) + RS_ABS(t),
                        lambda t: BASE_REG(t) + RS_REL(t) + RS_ABS(t),
                        "production features + all rs features (both stages)"),
    "V2_rel_ref": (REL, BASE_REG,
                   "reference: relative-only existing features, no speed/load, no rs (prod regressor)"),
    "V3_rel_plus_rs_rel": (lambda t: REL(t) + RS_REL(t),
                           lambda t: BASE_REG(t) + RS_REL(t) + RS_ABS(t),
                           "relative existing + baseline-relative rs features; regressor + all rs"),
    "V4_rel_plus_rs_all": (lambda t: REL(t) + RS_REL(t) + RS_ABS(t),
                           lambda t: BASE_REG(t) + RS_REL(t) + RS_ABS(t),
                           "relative existing + all rs (incl. absolute SNR/ratio levels); regressor + all rs"),
    "V5_rel_plus_rs_energy": (lambda t: REL(t) + RS_ENERGY_REL(t),
                              lambda t: BASE_REG(t) + RS_REL(t) + RS_ABS(t),
                              "relative existing + baseline-relative shaft-order/band-RMS rs only; regressor + all rs"),
    "V6_base_plus_rs_energy": (lambda t: BASE_CLF(t) + RS_ENERGY_REL(t),
                               lambda t: BASE_REG(t) + RS_REL(t) + RS_ABS(t),
                               "production classifier features + baseline-relative shaft-order/band-RMS rs; regressor + all rs"),
}


def run_variant(name: str, table: pd.DataFrame) -> None:
    cf, rf, desc = VARIANTS[name]
    t0 = time.perf_counter()
    res = evaluate(table, classifier_feature_fn=cf, regressor_feature_fn=rf, n_jobs=3, verbose=True)
    PARTS.mkdir(parents=True, exist_ok=True)
    np.savez(PARTS / f"{name}.npz", raw_prob=res["oof"]["raw_prob"], rul_pred=res["oof"]["rul_pred"])
    out = {"variant": name, "description": desc, "summary": summary_metrics(res),
           "classifier_feature_count": res["classifier_feature_count"],
           "regressor_feature_count": res["regressor_feature_count"],
           "first_warning": res["first_warning_events"]["per_bearing"],
           "wall_seconds": time.perf_counter() - t0}
    (PARTS / f"{name}.json").write_text(json.dumps(out, indent=2, default=float))
    print(json.dumps(out["summary"], indent=1, default=float))


# --------------------------------------------------------------- post analysis
def loo_threshold_report(table, y, probs, rul, grid=np.round(np.arange(0.40, 0.81, 0.05), 2)):
    """Threshold for bearing b chosen by max F1 on the OTHER 14 bearings' OOF rows.

    Not fully nested (those OOF probs came from models that had b in training), so
    mildly optimistic, but the test bearing itself never influences its threshold.
    """
    bearings = table["bearing_id"].to_numpy()
    reps = {th: compute_report(table, y, probs, rul, threshold=th) for th in grid}
    warn = np.zeros(len(table), bool)
    chosen = {}
    inh = y <= 120
    for b in np.unique(bearings):
        other = bearings != b
        best = max(grid, key=lambda th: _f1(reps[th]["oof"]["warning"][other], inh[other]))
        chosen[b] = float(best)
        warn[bearings == b] = reps[best]["oof"]["warning"][bearings == b]
    rep = compute_report(table, y, probs, rul, postprocess_fn=lambda t, p: warn)
    return rep, chosen


def _f1(w, t):
    tp = np.sum(w & t)
    return 2 * tp / max(np.sum(w) + np.sum(t), 1)


def live_rul_mae(rep):
    o = rep["oof"]
    inh = o["in_horizon"]
    live = np.where(o["warning"], o["rul_pred"], 120.0)
    y = o["table_keys"]["rul_minutes"].to_numpy()
    return float(np.mean(np.abs(live[inh] - y[inh])))


def summarize() -> None:
    table = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    y = table["rul_minutes"].to_numpy(float)
    base = load_baseline()
    base_oof = pd.read_csv(HERE / "results" / "baseline_oof.csv")
    base_rep = compute_report(table, y, base_oof["raw_prob"].to_numpy(), base_oof["rul_pred"].to_numpy())
    out = {"baseline": {"summary": summary_metrics(base_rep), "live_rul_mae": live_rul_mae(base_rep)},
           "variants": {}}
    brep_loo, bch = loo_threshold_report(table, y, base_oof["raw_prob"].to_numpy(), base_oof["rul_pred"].to_numpy())
    out["baseline"]["loo_threshold"] = {"summary": summary_metrics(brep_loo), "chosen": bch}
    best_name, best_rep = None, None
    for p in sorted(PARTS.glob("*.json")):
        info = json.loads(p.read_text())
        arr = np.load(PARTS / f"{info['variant']}.npz")
        rep = compute_report(table, y, arr["raw_prob"], arr["rul_pred"])
        assert abs(summary_metrics(rep)["average_precision"] - info["summary"]["average_precision"]) < 1e-9
        info["live_rul_mae"] = live_rul_mae(rep)
        lrep, ch = loo_threshold_report(table, y, arr["raw_prob"], arr["rul_pred"])
        info["loo_threshold"] = {"summary": summary_metrics(lrep), "chosen": ch,
                                 "live_rul_mae": live_rul_mae(lrep)}
        out["variants"][info["variant"]] = info
        if best_rep is None or info["summary"]["average_precision"] > out["variants"][best_name]["summary"]["average_precision"]:
            best_name, best_rep = info["variant"], rep
    out["best_variant_by_ap"] = best_name
    OUT_JSON.write_text(json.dumps(out, indent=2, default=float))
    if best_rep is not None:
        save_oof(best_rep, OUT_OOF)
    keys = ["roc_auc", "average_precision", "precision", "recall", "f1", "false_alarm_count",
            "missed_failure_window_count", "early_warning_bearings", "no_warning_bearings",
            "rul_mae", "rul_error_90"]
    print(f"{'variant':<22}" + "".join(f"{k[:10]:>11}" for k in keys) + f"{'liveMAE':>9}")
    rows = [("baseline", out["baseline"]["summary"], out["baseline"]["live_rul_mae"])]
    rows += [("  loo-thr", out["baseline"]["loo_threshold"]["summary"], float("nan"))]
    for n, i in out["variants"].items():
        rows.append((n, i["summary"], i["live_rul_mae"]))
        rows.append(("  loo-thr", i["loo_threshold"]["summary"], i["loo_threshold"]["live_rul_mae"]))
    for n, s, l in rows:
        print(f"{n:<22}" + "".join(f"{s[k]:>11.3f}" if isinstance(s[k], float) else f"{s[k]:>11d}" for k in keys) + f"{l:>9.2f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summarize"
    if cmd == "extract":
        df = extract_all()
        print(df.shape)
    elif cmd == "run":
        tbl = build_table()
        print("table", tbl.shape, flush=True)
        for v in sys.argv[2:]:
            run_variant(v, tbl)
    elif cmd == "summarize":
        summarize()
