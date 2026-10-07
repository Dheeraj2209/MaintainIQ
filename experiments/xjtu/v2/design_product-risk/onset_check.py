"""Quick feasibility checks for degradation-anchored onset definitions (product-risk design)."""
import numpy as np, pandas as pd
d = pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle'])
B = 20
rows = []
ref = {}
for b, g in d.groupby('bearing_id'):
    ref[b] = dict(h=g.h_rms.iloc[:B].median(), v=g.v_rms.iloc[:B].median(),
                  hp=g.h_peak.iloc[:B].median(), vp=g.v_peak.iloc[:B].median(),
                  hk=g.h_kurtosis.iloc[:B].median(), vk=g.v_kurtosis.iloc[:B].median())
def first_sustained(x, thr, rul):
    # hindsight "final crossing": first index after which smoothed x stays >= thr for >=90% of remaining rows, and at index x>=thr
    n=len(x); above = x>=thr
    suff = np.cumsum(above[::-1])[::-1] / np.arange(n,0,-1)
    idx = np.where(above & (suff>=0.9))[0]
    return (int(idx[0]), float(rul[idx[0]])) if len(idx) else (None, np.nan)
for b, g in d.groupby('bearing_id'):
    g = g.reset_index(drop=True); rul = g.rul_minutes.values; n=len(g)
    r = ref[b]
    others = [ref[o] for o in ref if o!=b]
    fh = np.median([o['h'] for o in others]); fv = np.median([o['v'] for o in others])
    hi_self = np.maximum(g.h_rms.values/r["h"], g.v_rms.values/r["v"])
    hi_fleet = np.maximum(g.h_rms.values/fh, g.v_rms.values/fv)
    pk_self = np.maximum(g.h_peak.values/r["hp"], g.v_peak.values/r["vp"])
    kurt = np.maximum(g.h_kurtosis.values, g.v_kurtosis.values)
    cen = lambda s: pd.Series(s).rolling(5, center=True, min_periods=1).median().values
    caus = lambda s: pd.Series(s).rolling(5, min_periods=1).median().values
    hs, hf, ku = cen(hi_self), cen(hi_fleet), cen(kurt)
    # baseline noise band (MAD) for self HI
    base = hi_self[:B]; band = 1 + max(0.1, 3*1.4826*np.median(np.abs(base-np.median(base))))
    out = dict(bearing=b, n=n, base0_fleetratio=round(float(np.median(hi_fleet[:B])),2), band=round(band,2))
    for name, s, t in [('self1.3',hs,1.3),('selfband',hs,band),('fleet1.5',hf,1.5),('self2.0',hs,2.0),('self3.0',hs,3.0),('kurt4',ku,4.0)]:
        out['rul@'+name] = first_sustained(s,t,rul)[1]
    # causal detector proxy: rolling-5 median self HI>=1.3 for 3 consecutive rows; first alarm + count of pre-onset alarm rows
    c = caus(hi_self) >= 1.3
    run = pd.Series(c).groupby((~pd.Series(c)).cumsum()).cumsum().values >= 3
    out['causal_first_rul'] = float(rul[np.argmax(run)]) if run.any() else np.nan
    out['last_peak_ratio'] = round(float(pk_self[-1]),1); out['max_peak_ratio_prev']=round(float(pk_self[:-1].max()),1)
    out['last_rms_ratio'] = round(float(hi_self[-1]),1)
    # how many minutes before end does HI first exceed 3x / 5x (causal, raw)
    for k in (2,3,5):
        i = np.argmax(caus(hi_self)>=k) if (caus(hi_self)>=k).any() else None
        out[f'causal_rul_x{k}'] = float(rul[i]) if i is not None else np.nan
    rows.append(out)
pd.set_option('display.width',250); pd.set_option('display.max_columns',40)
res = pd.DataFrame(rows); print(res.to_string(index=False))
res.to_csv('experiments/xjtu/v2/design_product-risk/onset_check.csv', index=False)
