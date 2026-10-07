"""RUL-regressor experiments (classifier held fixed).

The classifier is unchanged, so its OOF probabilities are the harness baseline
OOF (results/baseline_oof.csv, reproduced bit-for-bit from models/ report).
Every regressor variant is fit with the same LOBO split / regressor seeds
(42 + fold) as the harness and scored through harness.compute_report, so the
classification metrics are identical to baseline and RUL metrics comparable.

Stage A  regressor-only LOBO variants (features / model family / training
         window / target transform / sample weights / classifier-prob feature)
Stage B  causal temporal post-processing of the RUL stream (alpha filter that
         encodes "RUL drops 1 min per minute", monotone cap), parameters
         picked by NESTED LOBO (chosen on the other 14 bearings' OOF).
Stage C  prediction intervals: global vs Mondrian (by predicted RUL band)
         leave-bearing-out conformal, coverage measured on the held-out bearing.

Run from repo root:  python experiments/xjtu/exp_rul-regressor.py
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("OMP_NUM_THREADS", "3")  # HistGradientBoosting uses OpenMP; cap threads
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, ExtraTreesClassifier
from sklearn.model_selection import LeaveOneGroupOut

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from experiments.xjtu.harness import (  # noqa: E402
    RESULTS_DIR, compute_report, default_make_regressor, load_context, save_oof,
    summary_metrics,
)
from src.training.xjtu_rul import classifier_feature_columns, feature_columns  # noqa: E402

H = 120.0
N_JOBS = 3
OUT_JSON = RESULTS_DIR / "rul-regressor.json"
OUT_OOF = RESULTS_DIR / "rul-regressor_oof.csv"
CACHE = Path(__file__).resolve().parent / "cache" / "rulreg"
CACHE.mkdir(parents=True, exist_ok=True)

ctx = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
y = ctx["rul_minutes"].to_numpy(float)
ih = y <= H
g = ctx["bearing_id"].astype(str).to_numpy()
cond = ctx["condition"].to_numpy()
FOLDS = list(LeaveOneGroupOut().split(ctx, y, g))

base_oof = pd.read_csv(RESULTS_DIR / "baseline_oof.csv")
assert (base_oof["bearing_id"].to_numpy() == g).all() and (base_oof["cycle"].to_numpy() == ctx["cycle"].to_numpy()).all()
PROB = base_oof["raw_prob"].to_numpy()            # fixed classifier OOF probability
BASE_RUL = base_oof["rul_pred"].to_numpy()
SMOOTH = base_oof["smoothed_prob"].to_numpy()
WARN = base_oof["warning"].to_numpy().astype(bool)

# ------------------------------------------------------------------ features
def compact_features(table: pd.DataFrame) -> pd.DataFrame:
    """Scale-free, causal, live-computable set (needs only last 60 + first 20 rows)."""
    X = pd.DataFrame(index=table.index)
    src = ["m_rms", "h_peak", "v_peak", "m_envelope_rms", "h_kurtosis", "v_kurtosis"]
    for c in src:
        r = table[f"{c}_baseline_ratio"].clip(lower=1e-3)
        base = (table[c] / r).abs().clip(lower=1e-12)  # commissioning baseline level
        X[f"log_{c}_ratio"] = np.log(r)
        for w in (5, 20, 60):
            X[f"{c}_mean{w}_lr"] = np.log((table[f"{c}_mean_{w}"] / base).clip(lower=1e-3))
        for w in (20, 60):
            X[f"{c}_reltrend{w}"] = table[f"{c}_trend_{w}"] / table[c].abs().clip(lower=1e-9)
    return X


def extended_features(table: pd.DataFrame) -> pd.DataFrame:
    """compact + log baseline ratios of all 20 rolling sources + rel std + 1 state feature."""
    X = compact_features(table)
    srcs = [c[: -len("_baseline_ratio")] for c in table.columns if c.endswith("_baseline_ratio")]
    for c in srcs:
        name = f"log_{c}_ratio"
        if name not in X:
            X[name] = np.log(table[f"{c}_baseline_ratio"].clip(lower=1e-3))
        X[f"{c}_relstd20"] = table[f"{c}_std_20"] / table[f"{c}_mean_20"].abs().clip(lower=1e-9)
    return X


def add_state_features(X: pd.DataFrame) -> pd.DataFrame:
    """Features needing per-machine running state (1 float + 1 int) beyond the 80 retained rows."""
    X = X.copy()
    lr = X["m_rms_mean5_lr"]
    X["run_max_log_mrms5"] = lr.groupby(g).cummax()
    crossed = (lr > np.log(1.3)).astype(int)
    first = crossed.groupby(g).cumsum() > 0
    # minutes since mean-5 m_rms ratio first exceeded 1.3x (0 before)
    X["min_since_onset13"] = first.groupby(g).cumsum().astype(float)
    return X


XC = compact_features(ctx)
XE = extended_features(ctx)
XCS = add_state_features(XC)
ALL_F = feature_columns(ctx)
print("compact", XC.shape[1], "extended", XE.shape[1], "full", len(ALL_F), flush=True)

# ------------------------------------------------------------- LOBO regressor
def lobo_reg(name, make, X, train_mask=lambda yt: yt <= H, fwd=lambda t: t, inv=lambda p: p,
             weight_fn=None, extra_cols=None):
    path = CACHE / f"{name}.npy"
    if path.exists():
        return np.load(path)
    t0 = time.perf_counter()
    pred = np.full(len(y), np.nan)
    Xv = X.to_numpy(float) if isinstance(X, pd.DataFrame) else X
    for fold, (tr, te) in enumerate(FOLDS, start=1):
        m = tr[np.asarray(train_mask(y[tr]), bool)]
        Xtr, Xte = Xv[m], Xv[te]
        if extra_cols is not None:
            ftr, fte = extra_cols(fold, m, te)
            Xtr, Xte = np.column_stack([Xtr, ftr]), np.column_stack([Xte, fte])
        est = make(42 + fold)
        kw = {}
        if weight_fn is not None:
            kw["sample_weight"] = weight_fn(m)
        est.fit(Xtr, fwd(np.minimum(y[m], H)), **kw)
        pred[te] = np.clip(inv(est.predict(Xte)), 0, H)
    np.save(path, pred)
    print(f"  [{name}] fitted in {time.perf_counter() - t0:.0f}s", flush=True)
    return pred


def bearing_balanced(m):
    gm = g[m]
    counts = pd.Series(gm).value_counts()
    w = 1.0 / counts.loc[gm].to_numpy()
    return w * len(w) / w.sum()


def et(leaf=30, mf=0.5, n=300):
    return lambda s: ExtraTreesRegressor(n_estimators=n, min_samples_leaf=leaf, max_features=mf,
                                         n_jobs=N_JOBS, random_state=s)


def hgb(loss="squared_error"):
    return lambda s: HistGradientBoostingRegressor(loss=loss, learning_rate=0.05, max_iter=300,
                                                   max_leaf_nodes=15, min_samples_leaf=40,
                                                   l2_regularization=1.0, random_state=s)


def xgbr(objective="reg:squarederror"):
    import xgboost as xgb
    return lambda s: xgb.XGBRegressor(n_estimators=400, learning_rate=0.03, max_depth=4,
                                      min_child_weight=20, subsample=0.8, colsample_bytree=0.6,
                                      reg_lambda=5.0, objective=objective, n_jobs=N_JOBS,
                                      random_state=s, verbosity=0)


# -------- nested-OOF classifier probability as a regressor feature (lite clf)
CLF_F = classifier_feature_columns(ctx)
XCLF = ctx[CLF_F].to_numpy(float)
LABEL = ih.astype(int)


def lite_clf(seed):
    return ExtraTreesClassifier(n_estimators=100, min_samples_leaf=3, max_features=0.8,
                                class_weight="balanced", n_jobs=N_JOBS, random_state=seed)


def nested_prob_cols(fold, m, te):
    """Training rows get inner-LOBO probs (classifier never saw their bearing nor the
    outer test bearing); test rows get an outer lite classifier's prob
    (a lite clf trained on the 14 training bearings).  Smoothed causally with window 3."""
    path = CACHE / f"nestedprob_fold{fold}.npy"
    tr_all = np.setdiff1d(np.arange(len(y)), te)
    if path.exists():
        inner = np.load(path)
    else:
        inner = np.full(len(y), np.nan)
        for b in np.unique(g[tr_all]):
            itr = tr_all[g[tr_all] != b]
            ite = tr_all[g[tr_all] == b]
            c = lite_clf(1000 + fold)
            c.fit(XCLF[itr], LABEL[itr])
            inner[ite] = c.predict_proba(XCLF[ite])[:, 1]
        c = lite_clf(1000 + fold)   # outer lite classifier on the 14 training bearings
        c.fit(XCLF[tr_all], LABEL[tr_all])
        inner[te] = c.predict_proba(XCLF[te])[:, 1]
        np.save(path, inner)
    sm = pd.Series(inner).groupby(g).transform(
        lambda s: s.rolling(3, min_periods=1).median()).to_numpy()
    return sm[m][:, None], sm[te][:, None]


# ---------------------------------------------------------------- metrics
def rul_extra(pred, warn=WARN):
    e = pred[ih] - y[ih]
    ae = np.abs(e)
    pb = pd.Series(ae).groupby(g[ih]).mean()
    live = np.where(warn, pred, H)
    bands = {}
    for lo, hi in ((0, 30), (30, 60), (60, 90), (90, 120.1)):
        s = (y[ih] >= lo) & (y[ih] < hi)
        bands[f"{lo}-{int(hi)}"] = {"bias": float(e[s].mean()), "mae": float(ae[s].mean())}
    return {
        "mae": float(ae.mean()), "rmse": float(np.sqrt((e ** 2).mean())),
        "error_90": float(np.quantile(ae, 0.9, method="higher")),
        "bias": float(e.mean()),
        "mae_rul_le_30": float(ae[y[ih] <= 30].mean()),
        "worst_bearing_mae": float(pb.max()), "median_bearing_mae": float(pb.median()),
        "corr_pred_true": float(np.corrcoef(pred[ih], y[ih])[0, 1]),
        "live_equivalent_mae": float(np.abs(live[ih] - y[ih]).mean()),
        "bands": bands,
        "per_bearing_mae": {k: float(v) for k, v in pb.items()},
    }


def harness_report(pred):
    return compute_report(ctx, y, PROB, pred)


RESULTS = {"variants": {}}


def record(name, pred, desc):
    rep = harness_report(pred)
    s = summary_metrics(rep)
    ex = rul_extra(pred)
    RESULTS["variants"][name] = {"description": desc, "summary": s, "rul": ex}
    print(f"{name:34s} MAE {ex['mae']:6.2f} RMSE {ex['rmse']:6.2f} e90 {ex['error_90']:6.2f} "
          f"bias {ex['bias']:+6.2f} <=30 {ex['mae_rul_le_30']:6.2f} worst {ex['worst_bearing_mae']:5.1f} "
          f"corr {ex['corr_pred_true']:.2f} live {ex['live_equivalent_mae']:6.2f}", flush=True)
    return rep


# ----------------------------------------------------------- temporal post
def alpha_filter(raw, alpha, reset_on_warning=False, warn=WARN):
    """est_t = alpha*raw_t + (1-alpha)*(est_{t-1} - 1); causal, 1 float of state."""
    out = np.empty_like(raw)
    for b in np.unique(g):
        idx = np.flatnonzero(g == b)
        est = None
        for i in idx:
            if reset_on_warning and not warn[i]:
                est = None
                out[i] = raw[i]
                continue
            est = raw[i] if est is None else alpha * raw[i] + (1 - alpha) * (est - 1.0)
            est = min(H, max(0.0, est))
            out[i] = est
    return out


def monotone_cap(raw, slack):
    """out_t = min(raw_t, out_{t-1} - 1 + slack)."""
    out = np.empty_like(raw)
    for b in np.unique(g):
        prev = None
        for i in np.flatnonzero(g == b):
            v = raw[i] if prev is None else min(raw[i], prev - 1.0 + slack)
            v = min(H, max(0.0, v))
            out[i] = prev = v
    return out


def nested_select(raw, grid, fn):
    """For each test bearing pick the grid value minimising in-horizon MAE on the
    other 14 bearings' OOF predictions, then apply it to the test bearing."""
    cand = {p: fn(raw, p) for p in grid}
    out = np.empty_like(raw)
    picks = {}
    for b in np.unique(g):
        other = (g != b) & ih
        best = min(grid, key=lambda p: np.abs(cand[p][other] - y[other]).mean())
        picks[b] = best
        out[g == b] = cand[best][g == b]
    return out, picks


# ----------------------------------------------------------- intervals
def conformal(pred, level=0.9, mondrian=False):
    """Leave-bearing-out conformal on in-horizon rows: quantile of |err| of the
    other 14 bearings (optionally per predicted-RUL band), coverage on test bearing."""
    ae = np.abs(pred - y)
    bands = np.digitize(pred, [30, 60, 90]) if mondrian else np.zeros(len(y), int)
    cover, width = [], []
    for b in np.unique(g):
        te = (g == b) & ih
        other = (g != b) & ih
        q = np.zeros(len(y))
        for k in np.unique(bands):
            sel = other & (bands == k)
            src = ae[sel] if sel.sum() >= 30 else ae[other]
            q[bands == k] = np.quantile(src, level, method="higher")
        lo, hi = np.maximum(0, pred - q), np.minimum(H, pred + q)
        cover.append(((y >= lo) & (y <= hi))[te])
        width.append((hi - lo)[te])
    c, w = np.concatenate(cover), np.concatenate(width)
    return {"coverage": float(c.mean()), "mean_width": float(w.mean())}


# ================================================================== main
if __name__ == "__main__":
    t_all = time.perf_counter()
    rep0 = record("baseline", BASE_RUL, "production regressor OOF (harness baseline)")
    const = np.full(len(y), 60.0)
    record("constant_60", const, "constant 60-minute prediction (floor reference)")

    V = {}
    V["et_compact"] = (lobo_reg("et_compact", et(), XC), "ExtraTrees leaf30 mf0.5, 42 compact scale-free feats")
    V["et_compact_state"] = (lobo_reg("et_compact_state", et(), XCS),
                             "et_compact + running max log m_rms ratio + minutes since 1.3x onset (needs predictor state)")
    V["et_extended"] = (lobo_reg("et_extended", et(), XE), "ExtraTrees leaf30 on 82 extended scale-free feats")
    V["hgb_compact"] = (lobo_reg("hgb_compact", hgb(), XC), "HistGradientBoosting (L2) compact")
    V["hgb_compact_abs"] = (lobo_reg("hgb_compact_abs", hgb("absolute_error"), XC), "HistGradientBoosting (L1) compact")
    V["xgb_compact"] = (lobo_reg("xgb_compact", xgbr(), XC), "XGBoost depth4 compact")
    V["et_compact_w180"] = (lobo_reg("et_compact_w180", et(), XC, train_mask=lambda t: t <= 180),
                            "et_compact trained on rul<=180, target clipped 120")
    V["et_compact_w240"] = (lobo_reg("et_compact_w240", et(), XC, train_mask=lambda t: t <= 240),
                            "et_compact trained on rul<=240, target clipped 120")
    V["et_compact_sqrt"] = (lobo_reg("et_compact_sqrt", et(), XC, fwd=np.sqrt, inv=lambda p: np.square(p)),
                            "et_compact, sqrt target")
    V["et_compact_log"] = (lobo_reg("et_compact_log", et(), XC, fwd=lambda t: np.log1p(t), inv=np.expm1),
                           "et_compact, log1p target")
    V["et_compact_bw"] = (lobo_reg("et_compact_bw", et(), XC, weight_fn=bearing_balanced),
                          "et_compact, equal total weight per bearing")
    V["et_full_leaf30"] = (lobo_reg("et_full_leaf30", et(leaf=30, mf=0.5), ctx[ALL_F]),
                           "ExtraTrees leaf30 mf0.5 on production 342 feats")
    V["et_compact_prob"] = (lobo_reg("et_compact_prob", et(), XC, extra_cols=nested_prob_cols),
                            "et_compact + smoothed classifier prob (nested-OOF for training rows)")
    V["et_compact_leaf80"] = (lobo_reg("et_compact_leaf80", et(leaf=80, mf=0.5), XC),
                              "et_compact with min_samples_leaf=80 (stronger regularisation)")
    V["et_compact_leaf150"] = (lobo_reg("et_compact_leaf150", et(leaf=150, mf=0.5), XC),
                               "et_compact with min_samples_leaf=150")
    V["et_compact_leaf300"] = (lobo_reg("et_compact_leaf300", et(leaf=300, mf=0.5), XC),
                               "et_compact with min_samples_leaf=300")
    for k, (p, d) in V.items():
        record(k, p, d)

    # leaf size chosen per test bearing by nested LOBO (on the other 14 bearings' OOF)
    leaf_map = {30: V["et_compact"][0], 80: V["et_compact_leaf80"][0], 150: V["et_compact_leaf150"][0],
                300: V["et_compact_leaf300"][0]}
    nl, picks = nested_select(np.zeros(len(y)), [30, 80, 150, 300], lambda r, L: leaf_map[L])
    V["et_compact_nestedleaf"] = (nl, "")
    record("et_compact_nestedleaf", nl, "et_compact, min_samples_leaf in {30,80,150,300} nested-LOBO-selected")
    RESULTS["variants"]["et_compact_nestedleaf"]["nested_picks"] = {k: float(v) for k, v in picks.items()}

    # shrink toward the training-fold in-horizon median (fold-specific constant);
    # weight chosen by nested LOBO on the other 14 bearings' OOF predictions.
    prior = np.empty(len(y))
    for tr, te in FOLDS:
        prior[te] = np.median(y[tr][ih[tr]])
    for src_name in ("baseline", "et_compact"):
        raw = BASE_RUL if src_name == "baseline" else V[src_name][0]
        pp, picks = nested_select(raw, [0.0, 0.25, 0.5, 0.75, 1.0],
                                  lambda r, w: w * r + (1 - w) * prior)
        name = f"{src_name}+shrink"
        record(name, pp, f"{src_name} blended with training-fold median prior, weight nested-selected")
        RESULTS["variants"][name]["nested_picks"] = {k: float(v) for k, v in picks.items()}
        V[name] = (pp, "")

    # ensemble of the two strongest non-state families (fixed a priori: equal mean)
    ens = np.mean([V["et_compact"][0], V["hgb_compact"][0], V["xgb_compact"][0]], axis=0)
    V["ens_et_hgb_xgb"] = (ens, "mean of et/hgb/xgb compact")
    record("ens_et_hgb_xgb", ens, "mean of et/hgb/xgb compact")

    # ---------------- Stage B: temporal post-processing (nested-selected)
    print("\nStage B: temporal post-processing", flush=True)
    stageB = {}
    for src_name in ("baseline", "et_compact", "et_compact_state", "et_compact_nestedleaf"):
        raw = BASE_RUL if src_name == "baseline" else V[src_name][0]
        for kind, grid, fn in (
            ("alpha", [0.05, 0.1, 0.2, 0.3, 0.5, 1.0], lambda r, a: alpha_filter(r, a)),
            ("alpha_reset", [0.05, 0.1, 0.2, 0.3, 0.5, 1.0], lambda r, a: alpha_filter(r, a, True)),
            ("monocap", [1.0, 1.25, 1.5, 2.0, 3.0, 5.0, 1000.0], monotone_cap),
        ):
            pp, picks = nested_select(raw, grid, fn)
            name = f"{src_name}+{kind}"
            record(name, pp, f"{src_name} with causal {kind} post-proc, param nested-LOBO-selected")
            RESULTS["variants"][name]["nested_picks"] = {k: float(v) for k, v in picks.items()}
            # pooled (non-nested, optimistic) per-param MAE for transparency
            stageB[name] = {str(p): float(np.abs(fn(raw, p)[ih] - y[ih]).mean()) for p in grid}
            V[name] = (pp, "")
    RESULTS["stageB_pooled_mae_by_param_DIAGNOSTIC_ONLY"] = stageB

    # ---------------- Stage C: intervals
    print("\nStage C: intervals", flush=True)
    intervals = {}
    for k in ("baseline", "et_compact", "et_compact_state", "xgb_compact", "et_compact_prob",
              "et_compact_nestedleaf", "et_compact_nestedleaf+monocap", "et_compact+monocap", "constant_60"):
        p = BASE_RUL if k == "baseline" else const if k == "constant_60" else V[k][0]
        intervals[k] = {"global": conformal(p), "mondrian_by_pred_band": conformal(p, mondrian=True)}
        print(k, intervals[k], flush=True)
    RESULTS["intervals_lobo_90"] = intervals

    # choose best by in-horizon MAE among variants NOT requiring state change and
    # among all; reported honestly (selection on pooled OOF across ~25 variants).
    rows = {k: v["rul"]["mae"] for k, v in RESULTS["variants"].items() if k != "constant_60"}
    RESULTS["ranking_by_mae"] = sorted(rows.items(), key=lambda kv: kv[1])
    best = os.environ.get("BEST_VARIANT", "et_compact")
    best_pred = V[best][0]
    best_rep = harness_report(best_pred)
    save_oof(best_rep, OUT_OOF)
    RESULTS["best_variant"] = best
    RESULTS["best_summary"] = summary_metrics(best_rep)
    RESULTS["baseline_summary"] = summary_metrics(rep0)
    RESULTS["wall_seconds"] = time.perf_counter() - t_all
    OUT_JSON.write_text(json.dumps(RESULTS, indent=2, default=float), encoding="utf-8")
    print("\nranking:")
    for k, v in RESULTS["ranking_by_mae"][:12]:
        print(f"  {k:34s} {v:6.2f}")
