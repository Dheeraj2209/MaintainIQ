"""Does reference pipeline D (TSO + global conformal, model_crit = q10<=30) collapse to 'critical at onset'?
Uses causal ratio1.3k3 FPT, LOBO median post-onset life, LOBO bearing-equal-weight conformal on log1p residuals."""
import numpy as np, pandas as pd
from pathlib import Path
R = Path(__file__).resolve().parents[4]
f = pd.read_csv(R/"outputs/xjtu_features.csv", usecols=["bearing_id","cycle","rul_minutes","h_rms","v_rms"])
f["b"]=f.bearing_id.str.replace("Bearing",""); f=f.sort_values(["b","cycle"])
fpt={}; dat={}
for b,d in f.groupby("b"):
    h,v=d.h_rms.values,d.v_rms.values
    r=pd.Series(np.maximum(h/np.median(h[:20]),v/np.median(v[:20]))).rolling(5,min_periods=1).median().values
    run=0; t=None
    for i in range(20,len(r)):
        run=run+1 if r[i]>=1.3 else 0
        if run>=3: t=i; break
    fpt[b]=t; dat[b]=d.rul_minutes.values
bs=[b for b in fpt if fpt[b] is not None]
def wq(vals,wts,q):
    o=np.argsort(vals); c=np.cumsum(wts[o])/wts.sum(); return vals[o][np.searchsorted(c,q)]
for b in bs:
    tr=[x for x in bs if x!=b]
    L=np.median([dat[x][fpt[x]] for x in tr])
    res=[];w=[]
    for x in tr:  # residuals of other bearings under same TSO rule (approx; L not re-left-out)
        tso=np.arange(len(dat[x])-fpt[x]); pred=np.maximum(0,L-tso); tru=dat[x][fpt[x]:]
        e=np.log1p(tru)-np.log1p(pred); res+=list(e); w+=[1/len(e)]*len(e)
    res=np.array(res); w=np.array(w); lo=wq(res,w,0.10)
    rul=dat[b][fpt[b]:]; tso=np.arange(len(rul)); q50=np.maximum(0,L-tso)
    q10=np.expm1(np.log1p(q50)+lo)
    crit=np.where(q10<=30)[0]; c50=np.where(q50<=30)[0]
    print(b,"onset RUL",rul[0],"L=%.0f"%L,"q10@onset=%.1f"%q10[0],
          "first q10<=30 at RUL",rul[crit[0]] if len(crit) else None,
          "| first q50<=30 at RUL",rul[c50[0]] if len(c50) else None)
