"""Onset GT v4 = first time the *offline-fitted* two-phase curve's degradation component exceeds
log(1+delta) above its fitted healthy level a (full-trajectory, denoised, self-referenced level a, not
the 20-row commissioning baseline). Compare against the causal heuristic the detector could trivially
implement (rolling-5 median of max(h,v) rms/commissioning-baseline > 1+delta, 5 consecutive)."""
import numpy as np, pandas as pd, importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('tp',Path(__file__).with_name('onset_twophase.py'))
src=Path(__file__).with_name('onset_twophase.py').read_text().split('rows=[]')[0]
ns={'__file__':str(Path(__file__))}; exec(src,ns)
d=ns['d']; hi=ns['hi']
def fitfull(y):
    n=len(y); t=np.arange(n,dtype=float); best=(np.inf,None)
    for tau in range(0,n-2):
        dt=np.clip(t-tau,0,None)
        for r in np.logspace(np.log10(0.3/n),0,40):
            X=np.column_stack([np.ones(n),np.expm1(np.minimum(r*dt,50))])
            coef=np.linalg.lstsq(X,y,rcond=None)[0]
            if coef[1]<=0: continue
            sse=float(np.sum((y-X@coef)**2))
            if sse<best[0]: best=(sse,(tau,r,coef))
    tau,r,coef=best[1]; return coef[1]*np.expm1(np.minimum(r*np.clip(t-tau,0,None),50)), tau
DELTAS=[0.15,0.3,0.6]
rows=[]
for b,g in d.groupby('bearing_id'):
    g=g.reset_index(drop=True); n=len(g); rul=g.rul_minutes.values
    step=max(1,n//400); idx=np.arange(0,n,step)
    exc={}; knee=[]
    for h in ('E','ENV','HF'):
        y=pd.Series(hi(g,h)).rolling(3,center=True,min_periods=1).median().values[idx]
        e,tau=fitfull(y); exc[h]=np.interp(np.arange(n),idx,e); knee.append(idx[tau])
    fused=np.median(np.vstack(list(exc.values())),axis=0)
    out={'bearing':b[7:],'cond':int(g.condition.iloc[0]),'life':n,'knee_RUL':int(rul[int(np.median(knee))])}
    rr=pd.Series(np.maximum(g.h_rms/np.median(g.h_rms[:20]), g.v_rms/np.median(g.v_rms[:20]))).rolling(5,min_periods=1).median().values
    for dl in DELTAS:
        k=np.where(fused>=np.log1p(dl))[0]; on=int(k[0]) if len(k) else n-1
        out[f'GT{dl}']=int(rul[on])
        c=rr>1+dl; run=np.convolve(c,np.ones(5),'full')[:n]>=5
        k2=np.where(run)[0]; out[f'heur{dl}']=int(rul[k2[0]]) if len(k2) else -1
    rows.append(out)
t=pd.DataFrame(rows); pd.set_option('display.width',220); print(t.to_string(index=False))
for dl in DELTAS:
    a=t[f'GT{dl}'].values; h=t[f'heur{dl}'].clip(lower=0).values
    print(f'delta={dl}: median post-onset life {np.median(a):.0f} min [IQR {np.percentile(a,25):.0f}-{np.percentile(a,75):.0f}], '
          f'GT-vs-causal-heuristic |diff| median {np.median(np.abs(a-h)):.0f} min, n|diff|>20: {(np.abs(a-h)>20).sum()}/15, spearman={pd.Series(a).corr(pd.Series(h),method="spearman"):.2f}')
t.to_csv(Path(__file__).with_name('onset_delta.csv'),index=False)
