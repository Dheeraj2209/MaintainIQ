"""Feasibility: offline (non-causal) onset ground truth per bearing from fused HIs."""
import numpy as np, pandas as pd
from pathlib import Path
R = Path(__file__).resolve().parents[4]
d = pd.read_csv(R/'outputs/xjtu_features.csv').sort_values(['bearing_id','cycle'])
FAM = {  # health-indicator families (per axis, take max over h/v of log-ratio to reference)
 'energy': ['rms','envelope_rms'],
 'impulse': ['kurtosis','envelope_kurtosis','crest_factor'],
 'spectral': ['spectral_centroid','high_band_energy_ratio','spectral_entropy'],
}
def seg_fit(x):
    """best single mean-shift changepoint, Gaussian cost, min seg 3; returns index of first post-change row"""
    n=len(x); cs=np.cumsum(x); cs2=np.cumsum(x*x); best=(np.inf,None)
    for k in range(3,n-2):
        a=x[:k]; b=x[k:]
        c=a.var()*len(a)+b.var()*len(b)
        if c<best[0]: best=(c,k)
    return best[1]
def hi_onset(z, k_mad=4.0):
    """z: centered-median-smoothed HI. Reference = pre-changepoint segment (uses whole trajectory).
    onset = 1 + last index where z within healthy band (failure-anchored, never-returns)."""
    n=len(z)
    if n<8: return 0, np.nan
    k=seg_fit(z); ref=z[:k]
    med=np.median(ref); mad=np.median(np.abs(ref-med))*1.4826+1e-9
    up=med+k_mad*mad
    inside=np.where(z<=up)[0]
    if len(inside)==0: return 0, up
    on=inside[-1]+1
    return min(on,n-1), up
rows=[]
for b,g in d.groupby('bearing_id'):
    g=g.reset_index(drop=True); n=len(g); rul=g.rul_minutes.values
    out={'bearing':b,'cond':g.condition.iloc[0],'life':n}
    fam_on={}
    for f,feats in FAM.items():
        ons=[]
        for ft in feats:
            for ax in 'hv':
                x=np.log(np.abs(g[f'{ax}_{ft}'].values)+1e-12)
                if ft=='spectral_entropy': x=-x  # entropy drops with defect tones? use both signs below
                z=pd.Series(x).rolling(5,center=True,min_periods=1).median().values
                on,_=hi_onset(z); ons.append(on)
                if ft=='spectral_entropy':
                    on2,_=hi_onset(-z); ons.append(on2)
        # family onset = median across members (robust)
        fam_on[f]=int(np.median(ons))
        out[f'rul_on_{f}']=int(rul[fam_on[f]])
    # fused: earliest onset confirmed by >=2 families within 10% of life
    ons=sorted(fam_on.values())
    fused=None
    for i,o in enumerate(ons):
        if sum(abs(o2-o)<=max(5,0.1*n) for o2 in ons)>=2: fused=o;break
    if fused is None: fused=ons[1]
    out['rul_on_fused']=int(rul[fused]); out['onset_frac']=round(fused/n,3)
    # energy ratio vs early reference at fused onset (informational)
    rr=np.maximum(g.h_rms/np.median(g.h_rms[:20]), g.v_rms/np.median(g.v_rms[:20])).values
    out['rmsratio_at_on']=round(rr[fused],2); out['rmsratio_last30_med']=round(np.median(rr[-30:]),2)
    out['n_pre20_overlap']=int(fused<20)
    rows.append(out)
t=pd.DataFrame(rows); pd.set_option('display.width',200); print(t.to_string(index=False))
t.to_csv(Path(__file__).with_name('onset_candidates.csv'),index=False)
