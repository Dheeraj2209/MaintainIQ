"""Empirical causality test of the v2 held-out prediction path (leakage lens).
For held-out bearings, the full pipeline output on rows [0, cut) computed from the FULL trajectory must
equal the output computed when the bearing is TRUNCATED at `cut` (future rows physically absent).
Also: held-out RUL labels replaced by garbage must not change any output.
Models (trained on the other 14 bearings) are shared between full and truncated runs; everything that
touches the held-out bearing (indicators, static features, stage-1 trigger/FPT, provisional and post-FPT
features, conformal application, fixed-point replay, publish) is recomputed from the truncated data."""
import os, sys, importlib.util, time, copy
os.environ["OMP_NUM_THREADS"] = "3"
sys.argv = [sys.argv[0], "--part", "N"]
from pathlib import Path
import numpy as np, pandas as pd
V2 = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(V2))
spec = importlib.util.spec_from_file_location("bp", V2 / "build_post-onset-rul.py")
bp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bp)
c, h = bp.c, bp.h
T0 = time.time()
G = bp.FULL_GRID
def pick(**kw):
    return [x for x in G if all(x[k] == v for k, v in kw.items())][0]
CFGS = {
  "N_modal": (dict(family="ratio", G=1.3, k=3, ref="fallback"),
              pick(hysteresis="latch", stage2="ET", stage2_params=dict(min_samples_leaf=10, max_features=0.3),
                   binning="global", model_crit=("q50", None), S1=3.0, S2=4.0)),
  "stress_calm_HGBq_mondrian_q10": (dict(family="dual", S=3, G=1.2, k=5, ref="fallback"),
              pick(hysteresis="calm", stage2="HGBq", stage2_params=dict(min_samples_leaf=20), binning="mondrian",
                   model_crit=("q10", 0.1), S1=2.0, S2=6.0)),
  "D": (h.D_STAGE1, bp.D_DOWN),
}
for nm, (s1, _) in CFGS.items():
    s1.setdefault("name", h.stage1_name(s1))
ORIG = {k: copy.copy(getattr(bp, k)) for k in ("FEATS", "EXTRA", "NROW", "RUL")}
ORIG_IND = {ref: dict(bp.IND[ref]) for ref in h.REFS}; ORIG_STAT = {ref: dict(bp.STAT[ref]) for ref in h.REFS}

def set_bearing(b, cut=None, garbage_rul=False):
    for k in ("FEATS", "EXTRA", "NROW", "RUL"):
        getattr(bp, k)[b] = ORIG[k][b]
    if cut is not None:
        bp.FEATS[b] = ORIG["FEATS"][b].iloc[:cut].reset_index(drop=True)
        bp.EXTRA[b] = ORIG["EXTRA"][b].iloc[:cut].reset_index(drop=True)
        bp.NROW[b] = cut; bp.RUL[b] = ORIG["RUL"][b][:cut]
    if garbage_rul:
        rng = np.random.default_rng(0)
        g = rng.uniform(0, 3000, bp.NROW[b])
        bp.RUL[b] = g; f = bp.FEATS[b].copy(); f["rul_minutes"] = g; bp.FEATS[b] = f
        e = bp.EXTRA[b].copy(); e["rul_minutes"] = g; bp.EXTRA[b] = e
    for ref in h.REFS:
        bp.IND[ref][b] = c.indicators(bp.FEATS[b], ref)
        bp.STAT[ref][b] = bp._static(ref, b)

def publish(Ffit, s1cfg, d, b):
    sp = bp.s1_spec(s1cfg)
    F = bp.FoldStage2(Ffit.train, sp, Ffit.seed)
    F.inner, F.oof, F.refit = Ffit.inner, Ffit.oof, Ffit.refit
    assert all(sp["fpt"][t] == Ffit.s1["fpt"][t] for t in Ffit.train)
    return bp.outer_publish(F, d, b, np.nan)

def same(a, bb, tol=1e-9):
    a = np.asarray(a, float); bb = np.asarray(bb, float)
    return bool(np.all((np.isnan(a) & np.isnan(bb)) | (np.abs(a - bb) <= tol * np.maximum(1, np.abs(a)))))

rows = []
for b, step in (("3_5", 1), ("1_2", 1), ("2_3", 4)):
    train = [x for x in bp.BS if x != b]
    for nm, (s1cfg, d) in CFGS.items():
        set_bearing(b)
        sp = bp.s1_spec(s1cfg)
        Ffit = bp.FoldStage2(train, sp, 42); Ffit.ensure(d["stage2_index"])
        full = publish(Ffit, s1cfg, d, b)
        set_bearing(b, garbage_rul=True)
        gar = publish(Ffit, s1cfg, d, b)
        g_ok = all(same(full[k], gar[k]) for k in ("states", "q50", "lo", "hi", "prob"))
        n = len(ORIG["RUL"][b]); bad = []
        for cut in range(c.COMMISSION + 1, n + 1, step):
            set_bearing(b, cut=cut)
            tr = publish(Ffit, s1cfg, d, b)
            for k in ("states", "q50", "lo", "hi", "prob"):
                if not same(full[k][:cut], tr[k]):
                    i = int(np.where(~((np.isnan(np.asarray(full[k][:cut], float)) & np.isnan(np.asarray(tr[k], float)))
                                       | (np.abs(np.asarray(full[k][:cut], float) - np.asarray(tr[k], float)) <= 1e-9)))[0][0])
                    bad.append((cut, k, i))
        set_bearing(b)
        st = full["states"]
        rows.append(dict(bearing=b, config=nm, n=n, cuts_tested=len(range(c.COMMISSION + 1, n + 1, step)),
                         prefix_mismatches=len(bad), first_mismatch=bad[:3], garbage_rul_identical=g_ok,
                         first_nonhealthy=int(np.argmax(st >= 1)) if (st >= 1).any() else -1,
                         first_crit=int(np.argmax(st >= 3)) if (st >= 3).any() else -1))
        print(rows[-1], f"{time.time()-T0:.0f}s", flush=True)
R = pd.DataFrame(rows); R.to_csv(Path(__file__).with_name("prefix_truncation.csv"), index=False)
print(R.to_string(index=False))
