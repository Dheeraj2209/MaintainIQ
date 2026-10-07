import numpy as np, pandas as pd
f=pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle']).reset_index(drop=True)
o=pd.read_csv('experiments/xjtu/results/baseline_oof.csv').sort_values(['bearing_id','cycle']).reset_index(drop=True)
# causal time-since-onset: onset = first t where m_rms/baseline(first20 median, expanding until 20) > thr for 3 consecutive
def tsince(g,thr):
    v=g.m_rms.to_numpy(); base=pd.Series(v).expanding().median().to_numpy().copy(); base[20:]=base[19] if len(v)>20 else base[20:]
    a=v/base>thr; run=0; on=None; out=np.full(len(v),np.nan)
    for i in range(len(v)):
        run=run+1 if a[i] else 0
        if on is None and run>=3: on=i
        if on is not None: out[i]=i-on
    return out
for thr in (1.3,1.5,2.0):
    f['ts']=np.concatenate([tsince(g,thr) for _,g in f.groupby('bearing_id')])
    post={b:(g.rul_minutes.iloc[0] if False else None) for b,g in f.groupby('bearing_id')}
    # post-onset remaining life at onset per bearing
    D={b: g.loc[g.ts==0,'rul_minutes'].iloc[0] if (g.ts==0).any() else np.nan for b,g in f.groupby('bearing_id')}
    ih=(f.rul_minutes<=120).to_numpy(); pred=np.full(len(f),np.nan)
    for b in D:
        K=np.nanmedian([D[x] for x in D if x!=b])  # LOBO
        m=(f.bearing_id==b).to_numpy()
        ts=f.ts[m].to_numpy(); p=np.where(np.isnan(ts),120.0,np.clip(K-ts,0,120)); pred[m]=p
    e=np.abs(pred[ih]-f.rul_minutes[ih]); print(f'thr {thr}: post-onset durations {dict((k,v) for k,v in D.items())}\n  LOBO heuristic MAE {e.mean():.2f}')
    # blend with baseline regressor
    for w in (0.3,0.5):
        bl=w*pred+(1-w)*o.rul_pred.to_numpy(); print(f'   blend w={w}: MAE {np.abs(bl[ih]-f.rul_minutes[ih]).mean():.2f} (w picked on pooled OOF -> optimistic)')
