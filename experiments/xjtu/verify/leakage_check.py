"""Leakage/causality checks for the combined candidate (skeptic review)."""
import sys, time
from pathlib import Path
import numpy as np, pandas as pd
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from src.training.xjtu_rul import add_past_context, classifier_feature_columns, feature_columns
from experiments.xjtu import combined as C

raw = pd.read_csv(REPO / "outputs" / "xjtu_features.csv")
full = add_past_context(raw)
clf_cols = C.BASE_CLF(C.TBL)
print("clf feature count", len(clf_cols), "== prod", clf_cols == classifier_feature_columns(full))
bad = [c for c in clf_cols if any(t in c for t in ("cycle", "elapsed", "rul", "source"))]
print("suspicious clf cols:", bad)
comp_full = C.compact_features(full)

# TBL vs fresh context
cols = [c for c in full.columns if c in C.TBL.columns]
d = (C.TBL[cols].select_dtypes("number") - full[cols].select_dtypes("number")).abs().max().max()
print("TBL vs fresh add_past_context max abs diff", d, "key order equal",
      (C.TBL[["bearing_id", "cycle"]].values == full[["bearing_id", "cycle"]].values).all())

def maxdiff(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    both_nan = np.isnan(a) & np.isnan(b)
    return np.nanmax(np.where(both_nan, 0, np.abs(a - b))) if a.size else 0.0

worst_prefix = 0.0; worst_live = 0.0
for b in ["Bearing1_3", "Bearing2_3", "Bearing3_1"]:
    rb = raw[raw.bearing_id == b].sort_values("cycle").reset_index(drop=True)
    fb_idx = np.flatnonzero(full.bearing_id.values == b)
    fctx = full.iloc[fb_idx].reset_index(drop=True)
    fcomp = comp_full.iloc[fb_idx].reset_index(drop=True)
    n = len(rb)
    for k in sorted({15, 21, 61, 100, n // 2, n - 1}):
        p = add_past_context(rb.iloc[:k])
        pc = C.compact_features(p)
        m1 = maxdiff(p[clf_cols].values, fctx.loc[:k - 1, clf_cols].values)
        m2 = maxdiff(pc.values, fcomp.iloc[:k].values)
        worst_prefix = max(worst_prefix, m1, m2)
        print(f"{b} prefix k={k:4d}: clf maxdiff {m1:.2e} compact maxdiff {m2:.2e}")
    # live emulation: first 20 baseline rows + last 60 rows
    for t in sorted({5, 25, 70, 150, n // 2, n - 1}):
        rows = pd.concat([rb.iloc[:20], rb.iloc[max(0, t - 59):t + 1]]).drop_duplicates("cycle")
        rows = rows[rows.cycle <= rb.cycle.iloc[t]]
        lc = add_past_context(rows).iloc[[-1]]
        m1 = maxdiff(lc[clf_cols].values[0], fctx.loc[t, clf_cols].values)
        m2 = maxdiff(C.compact_features(lc).values[0], fcomp.iloc[t].values)
        worst_live = max(worst_live, m1, m2)
        print(f"{b} live t={t:4d}: clf maxdiff {m1:.2e} compact maxdiff {m2:.2e}")
print("WORST prefix diff", worst_prefix, "WORST live diff", worst_live)

# rule causality: mask on prefix == prefix of full mask
rng = np.random.default_rng(0)
viol = 0
for ri, (fam, prm) in enumerate(C.RULES):
    p = np.clip(np.cumsum(rng.normal(0, .08, 300)) * .3 + .5, 0, 1)
    full_m = C.rule_mask(fam, prm, p)
    for k in (7, 50, 151, 299):
        if not np.array_equal(C.rule_mask(fam, prm, p[:k]), full_m[:k]):
            viol += 1
print("rule prefix violations:", viol, "of", len(C.RULES) * 4)

# label-proxy check: does any compact/clf feature encode total life? corr of feature with bearing length
lens = full.groupby("bearing_id").size()
print("bearing lengths", lens.to_dict())

# alignment of cached OOF arrays
oof = pd.read_csv(REPO / "experiments/xjtu/results/baseline_oof.csv")
print("baseline_oof aligned", (oof[["bearing_id", "cycle"]].values == C.TBL[["bearing_id", "cycle"]].values).all())

# recompute compact300 regressor OOF (s0) and compare to cache
t0 = time.perf_counter()
X = C.compact_features(C.TBL).to_numpy(float)
out = np.full(len(C.Y), np.nan)
for fold, (tr, te) in enumerate(C.FOLDS, start=1):
    late = tr[C.TRUTH[tr]]
    assert not np.isin(te, late).any()
    out[te] = np.clip(C._compact_reg(42 + fold).fit(X[late], C.Y[late]).predict(X[te]), 0, 120)
cached = np.load(C.reg_path("compact300", "s0"))
print("reg recompute maxdiff", np.nanmax(np.abs(out - cached)), "MAE", np.mean(np.abs(out[C.TRUTH] - C.Y[C.TRUTH])),
      f"{time.perf_counter()-t0:.0f}s")

# recompute classifier et_reg for folds 1 and 9 and compare to cache
cp = np.load(C.clf_path("et_reg", "s0"))
Xc = C.TBL[clf_cols]; lab = C.Y <= 120
for fold in (1, 9):
    tr, te = C.FOLDS[fold - 1]
    ps = [C.et_reg_clf(s + fold, 3).fit(Xc.iloc[tr], lab[tr]).predict_proba(Xc.iloc[te])[:, 1] for s in (42, 200, 1000)]
    print(f"clf fold {fold} ({C.GROUPS.iloc[te[0]]}) recompute maxdiff", np.abs(np.mean(ps, 0) - cp[te]).max())

# fully nested inner arrays: outer test bearing must be NaN in inner probs
for f in range(1, 16):
    inner = np.load(C.CACHE / f"inner_et_reg_s0_f{f:02d}.npy")
    te = C.FOLDS[f - 1][1]
    others = np.setdiff1d(np.arange(len(C.Y)), te)
    assert np.isnan(inner[te]).all() and not np.isnan(inner[others]).any(), f
print("inner arrays: test bearing never scored, all 14 others scored: OK")
