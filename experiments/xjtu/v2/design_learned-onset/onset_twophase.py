"""Onset GT v3: offline two-phase degradation model fit on full trajectory (non-causal).
y(t) = a + c*(exp(r*(t-tau)_+) - 1) + eps, fitted per HI by profile LS over (tau, r).
tau_hat = onset; profile-likelihood 90% interval on tau. HIs: log energy, log envelope energy,
log high-frequency energy. Fused onset = median of HI taus; flag 'unidentifiable' if c<=0 or interval too wide."""
import numpy as np, pandas as pd
from pathlib import Path
R = Path(__file__).resolve().parents[4]
d = pd.read_csv(R/'outputs/xjtu_features.csv').sort_values(['bearing_id','cycle'])
def hi(g,name):
    if name=='E':   x=np.maximum(g.h_rms,g.v_rms)
    elif name=='ENV': x=np.maximum(g.h_envelope_rms,g.v_envelope_rms)
    elif name=='HF': x=np.maximum(g.h_rms*np.sqrt(g.h_energy_5000_10000_hz_ratio+g.h_energy_10000_15000_hz_ratio),
                                  g.v_rms*np.sqrt(g.v_energy_5000_10000_hz_ratio+g.v_energy_10000_15000_hz_ratio))
    elif name=='KU': x=np.maximum(g.h_kurtosis,g.v_kurtosis).clip(lower=-2.9)+3
    return np.log(np.asarray(x,float)+1e-9)
def fit(y):
    n=len(y); t=np.arange(n,dtype=float); best=(np.inf,None); prof=np.full(n,np.inf)
    rs=np.logspace(np.log10(0.3/n),np.log10(1.0),40)
    for tau in range(2,n-2):
        dt=np.clip(t-tau,0,None)
        for r in rs:
            X=np.column_stack([np.ones(n),np.expm1(np.minimum(r*dt,50))])
            coef,res,_,_=np.linalg.lstsq(X,y,rcond=None)
            sse=float(np.sum((y-X@coef)**2))
            if sse<prof[tau]: prof[tau]=sse
            if sse<best[0] and coef[1]>0: best=(sse,(tau,r,coef[1]))
    if best[1] is None: return n-1,n-1,n-1
    tau=best[1][0]; s2=best[0]/(n-4)
    ok=np.where((prof-best[0])/s2<=2.71)[0]   # 90% profile interval (chi2_1)
    return tau,int(ok.min()),int(ok.max())
rows=[]
for b,g in d.groupby('bearing_id'):
    g=g.reset_index(drop=True); n=len(g); rul=g.rul_minutes.values
    # long bearings: fit on 2-min decimation is unnecessary; cap grid cost by subsampling for n>600
    step=max(1,n//400); idx=np.arange(0,n,step)
    out={'bearing':b[7:],'life':n}; taus=[]
    for h in ('E','ENV','HF','KU'):
        y=pd.Series(hi(g,h)).rolling(3,center=True,min_periods=1).median().values[idx]
        tau,lo,hi_=fit(y); tau,lo,hi_=idx[tau],idx[lo],idx[hi_]
        out[h]=f'{rul[tau]} [{rul[hi_]}-{rul[lo]}]'; 
        if h!='KU': taus.append(tau)
    fz=int(np.median(taus)); out['RUL_on']=int(rul[fz]); out['frac']=round(fz/n,2)
    rr=np.maximum(g.h_rms/np.median(g.h_rms[:20]), g.v_rms/np.median(g.v_rms[:20])).values
    out['rms@on']=round(rr[fz],2)
    rows.append(out)
t=pd.DataFrame(rows); pd.set_option('display.width',220); print(t.to_string(index=False))
t.to_csv(Path(__file__).with_name('onset_twophase.csv'),index=False)
