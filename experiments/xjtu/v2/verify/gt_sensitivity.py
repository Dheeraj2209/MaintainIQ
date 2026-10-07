"""Leakage/circularity lens: sensitivity of the v2 decision quantities to the onset GT definition.
Read-only w.r.t. frozen artefacts. Writes experiments/xjtu/v2/verify/gt_sensitivity*.csv"""
import pickle, sys
from pathlib import Path
import numpy as np, pandas as pd
V2 = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(V2))
import causal as c
sys.path.insert(0, str(V2))
import onset_gt as og

OUT = Path(__file__).resolve().parent
FEATS = c.load_features(); GT = c.load_gt(); BS = sorted(FEATS)
A = pickle.load(open(V2 / "cache/post-onset-rul/part_N.pkl", "rb"))
D = {b: A["Dres"][b]["pub"]["states"] for b in BS}
DP = {b: A["DPres"][b]["pub"]["states"] for b in BS}
N = {}
for r in A["Nres"]:
    b = r["held_out"]; N[b] = (r["pub"] if "pub" in r else A["Dres"][b]["pub"])["states"]
R0 = c.states_r(pd.read_csv(c.ROOT / "experiments/xjtu/results/baseline_oof.csv"))
DUM = c.dummy_states(FEATS, 3)
MODELS = {"N": N, "D": D, "D'": DP, "R0": R0, **DUM}

# ---------------------------------------------------------------- alternative onset definitions
F = pd.read_csv(c.ROOT / "outputs/xjtu_features.csv").sort_values(["bearing_id", "cycle"])
F["b"] = F.bearing_id.str.replace("Bearing", "", regex=False)
alt = {}
for b, g in F.groupby("b", sort=True):
    g = g.reset_index(drop=True); n = len(g); rul = g.rul_minutes.to_numpy()
    hE = og.centred(og.hi(g, ["h_rms", "v_rms"]))
    row = lambda t: None if t is None else int(t)
    # (1) same family, other levels
    e115 = og.perm_exceed(hE, 1.15); e15 = og.perm_exceed(hE, 1.5)
    # (2) non-amplitude statistic: kurtosis (impulsiveness), level relative to whole-life low level
    gk = g.assign(h_k3=g.h_kurtosis + 3.0, v_k3=g.v_kurtosis + 3.0)      # Pearson kurtosis (file stores excess)
    hk = og.centred(og.hi(gk, ["h_k3", "v_k3"]))
    k13 = og.perm_exceed(hk, 1.3)
    # (3) spectral-shape statistic: spectral centroid shift vs first-30%-of-life median, |log ratio| > log 1.15
    sc = np.max(np.vstack([np.abs(np.log(g[f"{a}_spectral_centroid"].to_numpy() /
                 np.median(g[f"{a}_spectral_centroid"].to_numpy()[: max(5, int(0.3 * n))]))) for a in "hv"]), axis=0)
    sc = og.centred(sc)
    s15 = og.perm_exceed(np.exp(sc), 1.15)
    # (4) failure-anchored (feature-free): product horizon RUL = 120 (old label), and RUL = 60
    r120 = int(max(0, n - 1 - 120)); r60 = int(max(0, n - 1 - 60))
    alt[b] = dict(E1p15=e115, E1p5=e15, KURT1p3=k13, SPEC1p15=s15, RUL120=r120, RUL60=r60,
                  on=int(GT.at[b, "gt_on_row"]), early=int(GT.at[b, "gt_early_row"]), late=int(GT.at[b, "gt_late_row"]), n=n)
AL = pd.DataFrame(alt).T
AL.to_csv(OUT / "gt_sensitivity_onsets.csv")
print("alternative onset ROWS (None = no permanent exceedance -> falls back to primary interval)")
print(AL.to_string())

def rows_for(b, variant):
    if variant == "primary":
        return int(GT.at[b, "gt_early_row"]), int(GT.at[b, "gt_late_row"])
    v = AL.at[b, variant]
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return int(GT.at[b, "gt_early_row"]), int(GT.at[b, "gt_late_row"])
    return int(v), int(v)

def frame(st, variant, w=5.0):
    rows = []
    for b in BS:
        rul = FEATS[b].rul_minutes.to_numpy(float); te, tl = rows_for(b, variant)
        r = c.bearing_cost(st[b], rul, te, tl, float(rul[tl]), w); r["bearing"] = b
        r["GTpart"] = c.W_FD * r["FD"] + c.W_FDH * r["FDh"] + c.W_LD * r["LD"]
        r["FApart"] = r["C"] - r["GTpart"]
        rows.append(r)
    return pd.DataFrame(rows).set_index("bearing")

VARS = ["primary", "E1p15", "E1p5", "KURT1p3", "SPEC1p15", "RUL120", "RUL60"]
out = []; pairs = []
for v in VARS:
    fr = {m: frame(st, v) for m, st in MODELS.items()}
    for m, f in fr.items():
        out.append(dict(variant=v, model=m, C=f.C.mean(), GTpart=f.GTpart.mean(), FApart=f.FApart.mean(),
                        C1=f.C1.mean(), FD=int(f.FD.sum()), LD_h=f.LD.mean(), MO=int(f.MO.sum())))
    for x, y in (("N", "D"), ("N", "R0"), ("D", "R0"), ("D'", "R0"), ("D", "onset_only"), ("N", "onset_eq_critical"),
                 ("D", "onset_eq_critical")):
        d = fr[x].C - fr[y].C; dg = fr[x].GTpart - fr[y].GTpart
        lo, hi = c.boot_ci(d.to_numpy())
        pairs.append(dict(variant=v, pair=f"{x}-{y}", mean=d.mean(), ci_lo=lo, ci_hi=hi, p=c.signflip_p(d.to_numpy()),
                          GT_terms_share=dg.mean(), FA_terms=(d - dg).mean()))
    # E2 eligibility in stage-1 form and C form for the main candidates
O = pd.DataFrame(out); P = pd.DataFrame(pairs)
O.to_csv(OUT / "gt_sensitivity_models.csv", index=False); P.to_csv(OUT / "gt_sensitivity_pairs.csv", index=False)
pd.set_option("display.width", 220)
print(O.pivot(index="model", columns="variant", values="C").round(3)[VARS].to_string())
print(O.pivot(index="model", columns="variant", values="C1").round(3)[VARS].to_string())
print(O.pivot(index="model", columns="variant", values="LD_h").round(3)[VARS].to_string())
print(P.round(3).to_string(index=False))
