"""Red-team quick checks for PROTOCOL.md (read-only on inputs)."""
import numpy as np, pandas as pd
from pathlib import Path
R = Path(__file__).resolve().parents[4]  # repo root
X = R / "experiments/xjtu"
f = pd.read_csv(R / "outputs/xjtu_features.csv")
gt = pd.read_csv(X / "v2/onset_gt.csv").set_index("bearing")
f["b"] = f.bearing_id.str.replace("Bearing", "")
f = f.sort_values(["b", "cycle"])

def states_ref(d):
    s = np.zeros(len(d), int)
    w = d.warning.values.astype(bool); r = d.rul_pred.values
    s[w & (r <= 120)] = 1; s[w & (r <= 60)] = 2; s[w & (r <= 30)] = 3
    return s

def cost(b, st, rul):
    g = gt.loc[b]; life = len(rul)
    st = np.asarray(st)
    crit = np.where(st >= 3)[0]
    MC = int(not ((st >= 3) & (rul >= 10)).any())
    EC = ((st >= 3) & (rul > 60)).sum() / 60
    EF = ((st >= 2) & (rul > 120)).sum() / 60
    nh = st >= 1
    starts = np.where(nh & ~np.r_[False, nh[:-1]])[0]
    FD = int((starts < g.gt_early_row).sum())
    FDmin = int((nh[: int(g.gt_early_row)]).sum())
    det = np.where(nh)[0]; tdet = det[det >= 0][0] if len(det) else life
    # first detection at/after... use first non-healthy row
    LD = max(0, tdet - g.gt_late_row) / 60
    MO = int(not (nh & (rul >= 10)).any())
    C = 10*MC + EC + 0.5*FD + LD + 0.25*EF
    C1 = 10*MO + 0.5*FD + LD
    return dict(b=b, MC=MC, EC=EC, FD=FD, FDmin=FDmin, LD=LD, EF=EF, MO=MO, C=C, C1=C1,
                crit_lead=(rul[crit[0]] if len(crit) else np.nan))

def summarize(name, rows):
    d = pd.DataFrame(rows)
    print(f"\n== {name}: mean C={d.C.mean():.3f} C1={d.C1.mean():.3f} MC={d.MC.sum()} "
          f"EC={d.EC.mean():.2f} FD={d.FD.sum()} FDmin={d.FDmin.sum()} LD={d.LD.mean():.2f} EF={d.EF.mean():.2f}")
    print(d.round(2).to_string(index=False))
    return d

# 1. reference models
for nm in ["baseline", "combined"]:
    o = pd.read_csv(X / f"results/{nm}_oof.csv"); o["b"] = o.bearing_id.str.replace("Bearing", "")
    o = o.sort_values(["b", "cycle"])
    rows = [cost(b, states_ref(d), d.rul_minutes.values) for b, d in o.groupby("b")]
    summarize(nm, rows)
    print("min rul_pred per bearing:", o.groupby("b").rul_pred.min().round(1).to_dict())

# causal r5
def r5(d):
    h, v = d.h_rms.values, d.v_rms.values
    r = np.maximum(h/np.median(h[:20]), v/np.median(v[:20]))
    return pd.Series(r).rolling(5, min_periods=1).median().values
def p5(d):
    h, v = d.h_peak.values, d.v_peak.values
    r = np.maximum(h/np.median(h[:20]), v/np.median(v[:20]))
    return pd.Series(r).rolling(5, min_periods=1).median().values

def latch(trig, k, start=20):
    run = 0; o = np.zeros(len(trig), bool)
    for i, t in enumerate(trig):
        if i < start: continue
        run = run + 1 if t else 0
        if run >= k: o[i:] = True; break
    return o

# 2. trivial detectors (stage-1 C1) and degenerate "critical at onset"
for name, fn in [
    ("fire_at_row20", lambda d: np.r_[np.zeros(20), np.ones(len(d)-20)].astype(int)),
    ("never", lambda d: np.zeros(len(d), int)),
    ("ratio1.3k3", lambda d: latch(r5(d) >= 1.3, 3).astype(int)),
    ("ratio1.2k3", lambda d: latch(r5(d) >= 1.2, 3).astype(int)),
    ("ratio1.3k3_crit_at_onset", lambda d: 3*latch(r5(d) >= 1.3, 3).astype(int)),
    ("fire_row20_crit_at_onset", lambda d: 3*np.r_[np.zeros(20), np.ones(len(d)-20)].astype(int)),
]:
    rows = [cost(b, fn(d), d.rul_minutes.values) for b, d in f.groupby("b")]
    summarize(name, rows)

# 3. physical severity ranges
print("\n== severity ranges (r5, p5): max before gt_early_row (pre-onset), value in last 10 rows, max overall")
for b, d in f.groupby("b"):
    g = gt.loc[b]; e = int(g.gt_early_row)
    rr, pp = r5(d), p5(d)
    print(b, "r5 pre-on max %.2f last10 %.2f max %.2f | p5 pre-on max %.2f last10 %.2f max %.2f | first p5>=4 RUL %s, >=8 RUL %s" % (
        rr[20:e].max() if e > 20 else np.nan, rr[-10:].max(), rr.max(),
        pp[20:e].max() if e > 20 else np.nan, pp[-10:].max(), pp.max(),
        d.rul_minutes.values[np.argmax(pp >= 4)] if (pp >= 4).any() else None,
        d.rul_minutes.values[np.argmax(pp >= 8)] if (pp >= 8).any() else None))
