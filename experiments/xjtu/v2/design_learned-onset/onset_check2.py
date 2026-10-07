"""Onset GT v2: binary-segmentation reference + sustained-exceedance (>=80% of remaining life above band)."""
import numpy as np, pandas as pd, sys
from pathlib import Path
R = Path(__file__).resolve().parents[4]
d = pd.read_csv(R/'outputs/xjtu_features.csv').sort_values(['bearing_id','cycle'])
FAM = {'energy':[('rms',1),('envelope_rms',1),('peak_to_peak',1)],
       'impulse':[('kurtosis',1),('envelope_kurtosis',1),('crest_factor',1)],
       'spectral':[('spectral_centroid',-1),('high_band_energy_ratio',-1),('low_band_energy_ratio',1),('spectral_entropy',-1)]}
K_MAD=float(sys.argv[1]) if len(sys.argv)>1 else 4.0
FRAC=float(sys.argv[2]) if len(sys.argv)>2 else 0.8
def cost(x):
    return len(x)*np.log(x.var()+1e-6) if len(x)>1 else 0
def first_segment_end(x, minseg=5):
    """binary segmentation (normal mean/var), BIC penalty; return end of first segment"""
    n=len(x); pen=3*np.log(n)
    cps=[0,n]; changed=True
    while changed:
        changed=False; new=[]
        for a,b in zip(cps[:-1],cps[1:]):
            seg=x[a:b]; base=cost(seg); best=(0,None)
            for k in range(minseg,len(seg)-minseg):
                g=base-cost(seg[:k])-cost(seg[k:])
                if g>best[0]: best=(g,k)
            if best[1] is not None and best[0]>pen: new.append(a+best[1]); changed=True
        cps=sorted(set(cps+new))
        if len(cps)>12: break
    return cps[1]
def onset(z):
    n=len(z); k=max(first_segment_end(z),10 if n>40 else 5)
    ref=z[:k]; med=np.median(ref); mad=np.median(np.abs(ref-med))*1.4826
    mad=max(mad, 0.02)  # floor: 2% in log units
    up=med+K_MAD*mad; above=z>up
    tailfrac=np.cumsum(above[::-1])[::-1]/np.arange(n,0,-1)
    ok=np.where(above & (tailfrac>=FRAC))[0]
    return (int(ok[0]) if len(ok) else n-1), k
rows=[]
for b,g in d.groupby('bearing_id'):
    g=g.reset_index(drop=True); n=len(g); rul=g.rul_minutes.values
    out={'bearing':b[7:],'life':n}; fam={}
    for f,feats in FAM.items():
        z=0
        for ft,s in feats:
            for ax in 'hv':
                x=s*np.log(np.abs(g[f'{ax}_{ft}'].values)+1e-12)
                x=pd.Series(x).rolling(5,center=True,min_periods=1).median().values
                ref=x[:max(5,n//10)]
                x=(x-np.median(ref))/(np.median(np.abs(ref-np.median(ref)))*1.4826+1e-3)
                z=np.maximum(z,x) if isinstance(z,np.ndarray) else x
        # family HI = max over members of robust z vs first 10% (offline only); smooth again
        on,k=onset(np.log1p(np.clip(z,0,None)))
        fam[f]=on; out[f'E_{f}']=int(rul[on])
    ons=sorted(fam.values())
    # fused: median of 3 family onsets (2-of-3 vote)
    fz=ons[1]; out['RUL_fused']=int(rul[fz]); out['frac']=round(fz/n,3)
    out['spread']=int(rul[ons[0]]-rul[ons[2]])
    rr=np.maximum(g.h_rms/np.median(g.h_rms[:20]), g.v_rms/np.median(g.v_rms[:20])).values
    out['rms@on']=round(rr[fz],2)
    rows.append(out)
t=pd.DataFrame(rows); pd.set_option('display.width',220); print(f'K_MAD={K_MAD} FRAC={FRAC}'); print(t.to_string(index=False))
t.to_csv(Path(__file__).with_name(f'onset_v2_k{K_MAD}_f{FRAC}.csv'),index=False)
