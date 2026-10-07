"""Causal health-indicator (HI) feature engineering for the XJTU-SY RUL model.

Run from repo root:
    python experiments/xjtu/exp_degradation-features.py check     # causality / live-equivalence check
    python experiments/xjtu/exp_degradation-features.py screen    # lite classifier + regressor screens
    python experiments/xjtu/exp_degradation-features.py full      # full-size runs of the chosen configs

Everything is computed on the existing per-snapshot table (outputs/xjtu_features.csv via the
harness context cache). No raw re-extraction.

Feature families added (all per bearing, causal):
  * lr_*        log ratio of a per-snapshot feature to the commissioning baseline (median of the
                first 20 snapshots, expanding during the first 20 -- identical to production
                baseline_ratio). Kurtosis uses log((k+3)/(k0+3)) (excess -> Pearson).
  * hi_<name>   a handful of cross-axis health indicators: max over h/v of lr_* (rms, peak,
                envelope rms, kurtosis, envelope kurtosis, |spectral entropy|), m_rms, composite.
  * window HI stats (live-computable from the retained last-60 + first-20 rows, no state change):
                med5, ewm5/ewm20 (finite 60-row window), slope20/slope60 (OLS), std20,
                frac60_13 (fraction of last 60 snapshots above log 1.3).
  * stateful HI stats (need a small per-machine state in RealTimeRULPredictor, see STATE_NOTE):
                runmax (expanding max of med5), drawdown (runmax - med5), cusum (one-sided CUSUM of
                hi - log 1.1, as log1p), since13/since2 (minutes since med5 first > log1.3 / log2,
                -1 if never), cnt13 (cumulative #snapshots with med5 > log1.3).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from experiments.xjtu.harness import (  # noqa: E402
    RESULTS_DIR, compute_report, evaluate, load_baseline, load_context, save_oof, save_report,
    summary_metrics, compare, smooth_probabilities, sustained_warning_mask,
)
from src.training.xjtu_rul import classifier_feature_columns, feature_columns  # noqa: E402
from sklearn.dummy import DummyClassifier, DummyRegressor  # noqa: E402
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.metrics import f1_score  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402

BASELINE_WINDOW = 20
EWM_WINDOW = 60
LOG13, LOG2, CUSUM_K = np.log(1.3), np.log(2.0), np.log(1.1)
EPS = 1e-9

STATE_NOTE = (
    "Stateful HI features need, per machine and per HI (8 HIs): running max of med5 (float), "
    "CUSUM accumulator (float), first cycle med5>log1.3 and >log2 (2 ints, -1 sentinel), "
    "cumulative count med5>log1.3 (int) = 5 scalars x 8 HIs = 40 numbers per machine, updated "
    "once per snapshot after the HI value of the new row is computed (med5 needs only the last "
    "5 rows, already retained). They must be persisted with the history and reset in "
    "reset_machine()."
)

AXES = ("h", "v")
LR_SOURCES_HV = [
    "rms", "peak", "peak_to_peak", "kurtosis", "crest_factor", "impulse_factor",
    "clearance_factor", "spectral_entropy", "spectral_centroid", "spectral_spread",
    "spectral_energy", "low_band_energy_ratio", "mid_band_energy_ratio", "high_band_energy_ratio",
    "energy_0_500_hz_ratio", "energy_500_1000_hz_ratio", "energy_1000_2000_hz_ratio",
    "energy_2000_5000_hz_ratio", "energy_5000_10000_hz_ratio", "energy_10000_15000_hz_ratio",
    "envelope_rms", "envelope_peak", "envelope_kurtosis", "envelope_crest_factor",
    "envelope_energy_0_50_hz_ratio", "envelope_energy_50_100_hz_ratio",
    "envelope_energy_100_200_hz_ratio", "envelope_energy_200_500_hz_ratio",
    "envelope_energy_500_1000_hz_ratio",
]
LR_SOURCES_M = ["m_rms", "m_peak", "m_kurtosis", "m_crest_factor", "m_envelope_rms",
                "m_envelope_kurtosis", "m_envelope_peak"]
HI_NAMES = ("rms", "mrms", "peak", "env", "kurt", "envkurt", "sent", "comp")
WIN_STATS = ("med5", "ewm5", "ewm20", "slope20", "slope60", "std20", "frac60_13")
STATE_STATS = ("runmax", "drawdown", "cusum", "since13", "since2", "cnt13")


def _baseline(values: np.ndarray) -> np.ndarray:
    s = pd.Series(values)
    b = s.expanding(min_periods=1).median()
    if len(b) > BASELINE_WINDOW:
        b.iloc[BASELINE_WINDOW:] = b.iloc[BASELINE_WINDOW - 1]
    return b.to_numpy()


def _log_ratio(col: str, x: np.ndarray) -> np.ndarray:
    if col.endswith("kurtosis"):
        x = x + 3.0  # excess -> Pearson kurtosis (>0)
    b = _baseline(x)
    return np.log(np.clip(x, EPS, None)) - np.log(np.clip(b, EPS, None))


def _finite_ewm(x: pd.Series, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    w = (1 - alpha) ** np.arange(EWM_WINDOW)[::-1]  # oldest .. newest
    out = np.empty(len(x))
    v = x.to_numpy()
    for i in range(len(v)):
        seg = v[max(0, i - EWM_WINDOW + 1): i + 1]
        ww = w[-len(seg):]
        out[i] = np.dot(seg, ww) / ww.sum()
    return out


def _rolling_slope(x: np.ndarray, window: int) -> np.ndarray:
    out = np.zeros(len(x))
    for i in range(len(x)):
        seg = x[max(0, i - window + 1): i + 1]
        if len(seg) < 3:
            continue
        t = np.arange(len(seg), dtype=float)
        t -= t.mean()
        out[i] = np.dot(t, seg - seg.mean()) / np.dot(t, t)
    return out


def _bearing_hi(g: pd.DataFrame) -> pd.DataFrame:
    """g: one bearing, sorted by cycle. Returns new feature columns (same index)."""
    out: dict[str, np.ndarray] = {}
    for ax in AXES:
        for s in LR_SOURCES_HV:
            c = f"{ax}_{s}"
            out[f"lr_{c}"] = _log_ratio(c, g[c].to_numpy(float))
    for c in LR_SOURCES_M:
        out[f"lr_{c}"] = _log_ratio(c, g[c].to_numpy(float))
    mx = lambda a, b: np.maximum(out[a], out[b])  # noqa: E731
    hi = {
        "rms": mx("lr_h_rms", "lr_v_rms"),
        "mrms": out["lr_m_rms"],
        "peak": mx("lr_h_peak", "lr_v_peak"),
        "env": mx("lr_h_envelope_rms", "lr_v_envelope_rms"),
        "kurt": mx("lr_h_kurtosis", "lr_v_kurtosis"),
        "envkurt": mx("lr_h_envelope_kurtosis", "lr_v_envelope_kurtosis"),
        "sent": np.maximum(np.abs(out["lr_h_spectral_entropy"]), np.abs(out["lr_v_spectral_entropy"])),
    }
    hi["comp"] = (hi["rms"] + hi["peak"] + hi["env"]) / 3.0
    cycles = g["cycle"].to_numpy(float)
    for name, x in hi.items():
        s = pd.Series(x)
        med5 = s.rolling(5, min_periods=1).median().to_numpy()
        p = f"hi_{name}"
        out[p] = x
        out[f"{p}_med5"] = med5
        out[f"{p}_ewm5"] = _finite_ewm(s, 5)
        out[f"{p}_ewm20"] = _finite_ewm(s, 20)
        out[f"{p}_slope20"] = _rolling_slope(x, 20)
        out[f"{p}_slope60"] = _rolling_slope(x, 60)
        out[f"{p}_std20"] = s.rolling(20, min_periods=1).std(ddof=0).fillna(0).to_numpy()
        out[f"{p}_frac60_13"] = (s > LOG13).astype(float).rolling(60, min_periods=1).mean().to_numpy()
        # ---- stateful (needs per-machine scalars live) ----
        runmax = np.maximum.accumulate(med5)
        out[f"{p}_runmax"] = runmax
        out[f"{p}_drawdown"] = runmax - med5
        cus = np.zeros(len(x))
        acc = 0.0
        for i, v in enumerate(x):
            acc = max(0.0, acc + v - CUSUM_K)
            cus[i] = acc
        out[f"{p}_cusum"] = np.log1p(cus)
        for thr, tag in ((LOG13, "since13"), (LOG2, "since2")):
            above = med5 > thr
            first = np.where(np.maximum.accumulate(above), 0, 1)  # 0 once crossed
            first_cycle = pd.Series(np.where(above, cycles, np.nan)).cummin().to_numpy()
            out[f"{p}_{tag}"] = np.where(first == 0, cycles - first_cycle, -1.0)
        out[f"{p}_cnt13"] = np.cumsum(med5 > LOG13).astype(float)
    return pd.DataFrame(out, index=g.index)


def add_hi_features(ctx: pd.DataFrame) -> pd.DataFrame:
    ctx = ctx.sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    parts = [_bearing_hi(g) for _, g in ctx.groupby("bearing_id", sort=False)]
    new = pd.concat(parts).loc[ctx.index]
    return pd.concat([ctx, new], axis=1)


# ------------------------------------------------------------------ feature sets
def cols_lr(t):
    return [c for c in t.columns if c.startswith("lr_")]


def cols_hi_window(t):
    return [c for c in t.columns if c.startswith("hi_") and not c.endswith(STATE_STATS)]


def cols_hi_state(t):
    return [c for c in t.columns if c.startswith("hi_") and c.endswith(STATE_STATS)]


def cols_rel(t):
    """Diagnosis-1 'relative only' set (no speed/load): baseline_ratio / std / trend."""
    base = [c for c in classifier_feature_columns(t) if c not in ("speed_rpm", "load_kn")]
    return [c for c in base if "baseline_ratio" in c or "_std_" in c or "_trend_" in c]


FEATURE_SETS = {
    "rel": cols_rel,
    "lr_win": lambda t: cols_lr(t) + cols_hi_window(t),
    "lr_full": lambda t: cols_lr(t) + cols_hi_window(t) + cols_hi_state(t),
    "hi_full": lambda t: cols_hi_window(t) + cols_hi_state(t),
    "rel_lr_full": lambda t: cols_rel(t) + cols_lr(t) + cols_hi_window(t) + cols_hi_state(t),
    # round 2 (chosen after seeing round-1 lite screens -> mild selection bias)
    "hi_win": cols_hi_window,
    "hi_core_full": lambda t: [c for c in cols_hi_window(t) + cols_hi_state(t)
                               if c.split("_")[1] in ("rms", "mrms", "peak", "env", "comp")],
}
REG_SETS = {
    "baseline_all": feature_columns,
    # scale-free regressor inputs: amplitude log-ratios + all HI stats (no absolute levels,
    # no speed/load, no elapsed_minutes)
    "reg_hi_full": lambda t: [c for c in cols_lr(t) if any(k in c for k in (
        "_rms", "_peak", "_kurtosis", "envelope_rms"))] + cols_hi_window(t) + cols_hi_state(t),
    "reg_hi_win": lambda t: [c for c in cols_lr(t) if any(k in c for k in (
        "_rms", "_peak", "_kurtosis", "envelope_rms"))] + cols_hi_window(t),
}


# ------------------------------------------------------------------ estimators
def et_clf(n_estimators=350, leaf=3, max_features=0.8):
    def make(seed, n_jobs):
        return Pipeline([("imputer", SimpleImputer(strategy="median")),
                         ("classifier", ExtraTreesClassifier(
                             n_estimators=n_estimators, min_samples_leaf=leaf,
                             max_features=max_features, class_weight="balanced",
                             n_jobs=n_jobs, random_state=seed))])
    return make


def et_reg(n_estimators=350, leaf=3, max_features=0.8):
    def make(seed, n_jobs):
        return Pipeline([("imputer", SimpleImputer(strategy="median")),
                         ("regressor", ExtraTreesRegressor(
                             n_estimators=n_estimators, min_samples_leaf=leaf,
                             max_features=max_features, n_jobs=n_jobs, random_state=seed))])
    return make


dummy_reg = lambda seed, n_jobs: DummyRegressor(strategy="constant", constant=60.0)  # noqa: E731
dummy_clf = lambda seed, n_jobs: DummyClassifier(strategy="prior")  # noqa: E731


# ------------------------------------------------------------------ extra metrics
def live_rul_mae(res) -> float:
    o = res["oof"]
    ih = o["in_horizon"]
    y = o["table_keys"]["rul_minutes"].to_numpy(float)
    live = np.where(o["warning"], o["rul_pred"], 120.0)
    return float(np.mean(np.abs(live[ih] - y[ih])))


def nested_threshold(table, y, raw_prob, grid=np.round(np.arange(0.30, 0.86, 0.025), 3),
                     smoothing_window=3, persistence=3):
    """Per test bearing, pick the threshold maximizing pooled F1 on the OTHER 14 bearings' OOF
    probabilities, then apply it to the test bearing. Approximate nesting: the other bearings'
    OOF probabilities came from models that saw the test bearing in training (no refit)."""
    ih = y <= 120
    sm = smooth_probabilities(table, raw_prob, smoothing_window)
    warn_by_t = {t: sustained_warning_mask(table, sm, t, persistence) for t in grid}
    bearings = table["bearing_id"].to_numpy()
    out = np.zeros(len(y), dtype=bool)
    chosen = {}
    for b in np.unique(bearings):
        te = bearings == b
        best = max(grid, key=lambda t: f1_score(ih[~te], warn_by_t[t][~te], zero_division=0))
        chosen[b] = float(best)
        out[te] = warn_by_t[best][te]
    return out, chosen


def flat(res, extra=None):
    s = summary_metrics(res)
    s["live_rul_mae"] = live_rul_mae(res)
    if extra:
        s.update(extra)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}


# ------------------------------------------------------------------ stages
OUT_JSON = RESULTS_DIR / "degradation-features.json"


def _load_out():
    return json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}


def _save_out(d):
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(d, indent=2, default=str))


def get_table():
    cache = REPO / "experiments" / "xjtu" / "cache" / "context_hi.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    t = add_hi_features(load_context())
    t.to_parquet(cache, index=False)
    return t


def stage_check():
    """(1) prefix-causality: features for row t computed on rows<=t equal full-table values.
    (2) live-equivalence for window features: recomputed on first-20 + last-60 rows only."""
    ctx = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    full = add_hi_features(ctx)
    newc = [c for c in full.columns if c.startswith(("lr_", "hi_"))]
    win = [c for c in newc if not c.endswith(STATE_STATS)]
    rng = np.random.default_rng(0)
    worst_prefix = worst_live = 0.0
    for b in ["Bearing3_1", "Bearing2_3", "Bearing1_2"]:
        g = ctx[ctx.bearing_id == b]
        fb = full[full.bearing_id == b]
        for t in sorted(rng.choice(len(g), 6, replace=False)) + [len(g) - 1]:
            pre = add_hi_features(g.iloc[: t + 1])
            d = np.nanmax(np.abs(pre[newc].iloc[-1].to_numpy(float) - fb[newc].iloc[t].to_numpy(float)))
            worst_prefix = max(worst_prefix, d)
            keep = sorted(set(range(min(20, t + 1))) | set(range(max(0, t - 59), t + 1)))
            live = add_hi_features(g.iloc[keep])
            # live context has a cycle gap; slopes use row index -> identical to offline for last row
            d2 = np.nanmax(np.abs(live[win].iloc[-1].to_numpy(float) - fb[win].iloc[t].to_numpy(float)))
            worst_live = max(worst_live, d2)
    print(f"max |prefix - full| over new features: {worst_prefix:.3e}")
    print(f"max |live(first20+last60) - full| over WINDOW features: {worst_live:.3e}")
    out = _load_out()
    out["causality_check"] = {"max_abs_prefix_diff": worst_prefix,
                              "max_abs_live_window_diff": worst_live,
                              "n_new_features": len(newc), "n_window": len(win)}
    _save_out(out)


def stage_screen():
    t = get_table()
    out = _load_out()
    scr = out.setdefault("screen_lite_classifier", {
        "_note": "1 seed, 150 trees, leaf 3, mf 0.8, constant-60 regressor; compare only to "
                 "the lite rows (diag/ablation.json lite_baseline AUC .796 AP .592 FA 409 miss 625; "
                 "relative_only_no_cond AUC .842 AP .669 FA 548 miss 622)"})
    for name in ["lr_win", "lr_full", "hi_full", "rel_lr_full"]:
        if name in scr:
            continue
        t0 = time.time()
        r = evaluate(t, make_classifier=et_clf(150), make_regressor=dummy_reg,
                     classifier_feature_fn=FEATURE_SETS[name], ensemble_seeds=(42,), n_jobs=3)
        s = flat(r, {"n_feat": r["classifier_feature_count"], "secs": round(time.time() - t0)})
        s["first_warn"] = {e["bearing_id"]: e["actual_rul_minutes_at_first_warning"]
                           for e in r["first_warning_events"]["per_bearing"]}
        scr[name] = s
        print(name, {k: v for k, v in s.items() if k != "first_warn"}, flush=True)
        _save_out(out)
    regs = out.setdefault("screen_regressor", {
        "_note": "classifier = prior dummy (irrelevant); regressor trained on in-horizon rows, "
                 "seed 42+fold. Only RUL metrics are meaningful."})
    for name, fs, mk in [("baseline_all_plus_hi_leaf3", "baseline_all", et_reg()),
                         ("reg_hi_full_leaf30", "reg_hi_full", et_reg(350, 30, 0.5)),
                         ("reg_hi_win_leaf30", "reg_hi_win", et_reg(350, 30, 0.5)),
                         ("reg_hi_full_leaf10", "reg_hi_full", et_reg(350, 10, 0.5))]:
        if name in regs:
            continue
        t0 = time.time()
        r = evaluate(t, make_classifier=dummy_clf, make_regressor=mk, ensemble_seeds=(42,),
                     regressor_feature_fn=REG_SETS[fs], n_jobs=3)
        s = summary_metrics(r)
        regs[name] = {"rul_mae": round(s["rul_mae"], 3), "rul_rmse": round(s["rul_rmse"], 3),
                      "rul_error_90": round(s["rul_error_90"], 3),
                      "worst_bearing_mae": round(max(p["within_horizon_rul_mae_minutes"]
                                                     for p in r["per_bearing"]), 2),
                      "n_feat": r["regressor_feature_count"], "secs": round(time.time() - t0)}
        print(name, regs[name], flush=True)
        _save_out(out)


def stage_screen2():
    t = get_table()
    out = _load_out()
    scr = out["screen_lite_classifier"]
    for name, fs, cfg in [("hi_win", "hi_win", (150, 3, 0.8)),
                          ("hi_core_full", "hi_core_full", (150, 3, 0.8)),
                          ("hi_full_leaf20_mf0.5", "hi_full", (150, 20, 0.5))]:
        if name in scr:
            continue
        t0 = time.time()
        r = evaluate(t, make_classifier=et_clf(*cfg), make_regressor=dummy_reg,
                     classifier_feature_fn=FEATURE_SETS[fs], ensemble_seeds=(42,), n_jobs=3)
        s = flat(r, {"n_feat": r["classifier_feature_count"], "secs": round(time.time() - t0)})
        s["first_warn"] = {e["bearing_id"]: e["actual_rul_minutes_at_first_warning"]
                           for e in r["first_warning_events"]["per_bearing"]}
        scr[name] = s
        print(name, {k: v for k, v in s.items() if k != "first_warn"}, flush=True)
        _save_out(out)


def stage_full(clf_set: str, reg_set: str, reg_cfg: tuple, tag: str, save_best: bool = False,
               clf_cfg: tuple = (350, 3, 0.8)):
    t = get_table()
    out = _load_out()
    full = out.setdefault("full", {})
    t0 = time.time()
    r = evaluate(t, make_classifier=et_clf(*clf_cfg), make_regressor=et_reg(*reg_cfg),
                 classifier_feature_fn=FEATURE_SETS[clf_set], regressor_feature_fn=REG_SETS[reg_set],
                 n_jobs=3, verbose=True)
    y = r["oof"]["table_keys"]["rul_minutes"].to_numpy(float)
    table = r["oof"]["table_keys"]
    entry = {"classifier_set": clf_set, "classifier_cfg": clf_cfg,
             "regressor_set": reg_set, "regressor_cfg": reg_cfg,
             "n_clf_feat": r["classifier_feature_count"], "n_reg_feat": r["regressor_feature_count"],
             "secs": round(time.time() - t0), "at_threshold_0.6": flat(r)}
    entry["first_warn_0.6"] = {e["bearing_id"]: e["actual_rul_minutes_at_first_warning"]
                               for e in r["first_warning_events"]["per_bearing"]}
    warn, chosen = nested_threshold(table, y, r["oof"]["raw_prob"])
    rn = compute_report(table, y, r["oof"]["raw_prob"], r["oof"]["rul_pred"],
                        postprocess_fn=lambda tb, p: warn)
    entry["nested_threshold"] = flat(rn)
    entry["nested_threshold_chosen"] = chosen
    entry["first_warn_nested"] = {e["bearing_id"]: e["actual_rul_minutes_at_first_warning"]
                                  for e in rn["first_warning_events"]["per_bearing"]}
    full[tag] = entry
    _save_out(out)
    print(json.dumps({k: v for k, v in entry.items() if not k.startswith("first")}, indent=1))
    compare(r)
    np.save(REPO / "experiments" / "xjtu" / "cache" / f"oof_{tag}.npy",
            np.vstack([r["oof"]["raw_prob"], r["oof"]["rul_pred"]]))
    if save_best:
        save_oof(r, RESULTS_DIR / "degradation-features_oof.csv")
        save_report(r, RESULTS_DIR / "degradation-features_best_report.json")
    return r


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "screen"
    if stage == "check":
        stage_check()
    elif stage == "screen":
        stage_screen()
    elif stage == "screen2":
        stage_screen2()
    elif stage == "full":
        # full <clf_set> <clf_leaf> <clf_mf> <reg_set> <reg_leaf> <reg_mf> <tag> [best]
        clf_set, cleaf, cmf, reg_set, leaf, mf, tag = sys.argv[2:9]
        stage_full(clf_set, reg_set, (350, int(leaf), float(mf)), tag,
                   save_best=len(sys.argv) > 9 and sys.argv[9] == "best",
                   clf_cfg=(350, int(cleaf), float(cmf)))
