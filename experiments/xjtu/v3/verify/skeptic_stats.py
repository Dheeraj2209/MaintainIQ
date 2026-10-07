import numpy as np, pandas as pd
df = pd.read_csv("results/rerun_per_bearing.csv", dtype={"bearing": str})
print(df.run.value_counts().to_dict()); print(df.model.unique())
p = df[df.run == "pooled_LOBO_32"]
def piv(col): return p.pivot(index="bearing", columns="model", values=col)
def sf(d, draws=2**20, seed=1):
    rng=np.random.default_rng(seed); a=np.abs(d); s=rng.choice([-1.,1.],size=(draws,len(d)))
    return ((s*a).mean(1) <= d.mean()+1e-12).mean()
def boot(d, reps=20000, seed=7):
    rng=np.random.default_rng(seed); m=d[rng.integers(0,len(d),(reps,len(d)))].mean(1); return np.percentile(m,[2.5,97.5])
for col in ["C_w5","FA_w5"]:
    t=piv(col); print(col, "n=",len(t), t.mean().round(3).to_dict())
    for a,b in [("N","D"),("N","R0"),("D","R0")]:
        d=(t[a]-t[b]).to_numpy()
        print(f"  {a}-{b}: mean {d.mean():.3f} CI {boot(d).round(2)} p_sf {sf(d):.4f} better {int((d<0).sum())} worse {int((d>0).sum())}")
for col in ["MC","EP"]:
    print(col, piv(col).sum().to_dict())
# per dataset
for ds in ["XJTU","FEMTO"]:
    q=p[p.dataset==ds]; t=q.pivot(index="bearing",columns="model",values="C_w5"); f=q.pivot(index="bearing",columns="model",values="FA_w5")
    print(ds, len(t), t.mean().round(2).to_dict(), f.mean().round(2).to_dict())
    print("  MC", q.pivot(index="bearing",columns="model",values="MC").sum().to_dict(), "EP", q.pivot(index="bearing",columns="model",values="EP").sum().to_dict())
    from itertools import product
    d=(t["N"]-t["D"]).to_numpy(); print("  N-D", d.mean().round(3), "exact p", (np.array([ (np.array(s)*np.abs(d)).mean() for s in product([1,-1],repeat=len(d))])<=d.mean()+1e-12).mean() if len(d)<=17 else None)
# FA recompute check from components
t=p.copy(); fa=10*t.LC+5*t.EP+0.5*np.minimum(t.ECh,4)+0.25*np.minimum(t.EFh,4)
print("FA formula max abs err", np.abs(fa-t.FA_w5).max())
