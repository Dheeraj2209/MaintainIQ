"""LOBO feasibility of a condition-free causal onset detector vs offline GT (GT0.3, energy-fused two-phase fit).
Cross-family test for circularity: detector built ONLY from non-energy features must recover an energy-defined onset."""
import numpy as np, pandas as pd
from pathlib import Path
from sklearn.ensemble import ExtraTreesClassifier
R=Path(__file__).resolve().parents[4]; H=Path(__file__).parent
d=pd.read_csv(R/'outputs/xjtu_features.csv').sort_values(['bearing_id','cycle']).reset_index(drop=True)
gt=pd.read_csv(H/'onset_delta.csv'); gt['bearing_id']='Bearing'+gt.bearing.astype(str)
GTCOL='GT0.3'
E=['rms','envelope_rms','peak','peak_to_peak','std']
NE=['kurtosis','envelope_kurtosis','crest_factor','impulse_factor','shape_factor','spectral_entropy','spectral_centroid',
    'high_band_energy_ratio','low_band_energy_ratio','envelope_energy_100_200_hz_ratio','envelope_energy_200_500_hz_ratio',
    'energy_5000_10000_hz_ratio','energy_10000_15000_hz_ratio']
def causal(g,bases):
    out={}
    for b in bases:
        for ax in 'hv':
            x=g[f'{ax}_{b}'].astype(float).values; base=np.median(x[:20]) if len(x)>=20 else None
            # baseline = expanding median, frozen after 20 rows (same as live predictor)
            bl=pd.Series(x).expanding().median().to_numpy().copy(); bl[20:]=bl[min(19,len(x)-1)]
            r=x/np.where(np.abs(bl)<1e-12,1e-12,bl)
            s=pd.Series(r)
            out[f'{ax}_{b}_r']=r; out[f'{ax}_{b}_r_med5']=s.rolling(5,min_periods=1).median().values
            out[f'{ax}_{b}_r_med20']=s.rolling(20,min_periods=1).median().values
            out[f'{ax}_{b}_r_std10']=s.rolling(10,min_periods=1).std(ddof=0).values
        out[f'mx_{b}_r_med5']=np.maximum(out[f'h_{b}_r_med5'],out[f'v_{b}_r_med5'])
    return pd.DataFrame(out)
feats={k:[] for k in ('E','NE')}; parts=[]
for b,g in d.groupby('bearing_id',sort=False):
    g=g.reset_index(drop=True); fe=causal(g,E); fn=causal(g,NE)
    fe.columns=['E_'+c for c in fe.columns]; fn.columns=['N_'+c for c in fn.columns]
    on=int(gt.loc[gt.bearing_id==b,GTCOL].iloc[0]); n=len(g)
    meta=pd.DataFrame({'bearing_id':b,'rul':g.rul_minutes.values,'y':(g.rul_minutes.values<=on).astype(int),'on_rul':on})
    parts.append(pd.concat([meta,fe,fn],axis=1))
X=pd.concat(parts,ignore_index=True).replace([np.inf,-np.inf],np.nan).fillna(1.0)
sets={'E':[c for c in X if c.startswith('E_')],'NE':[c for c in X if c.startswith('N_')]}
sets['ALL']=sets['E']+sets['NE']
B=X.bearing_id.unique()
w=X.groupby('bearing_id').bearing_id.transform('count'); w=1.0/w
def alarm_eval(p,sub,thr=0.5,k=3):
    s=pd.Series(p).rolling(5,min_periods=1).mean().values>thr
    run=np.convolve(s,np.ones(k),'full')[:len(s)]>=k
    i=np.where(run)[0]; rul=sub.rul.values; on=sub.on_rul.iloc[0]
    a=rul[i[0]] if len(i) else -1
    pre=(run & (rul>on)).sum()   # alarm-minutes before GT onset
    return a,on,pre,(rul>on).sum()
res={}
for name,cols in list(sets.items())+[('HEUR',None)]:
    rows=[]
    oof=np.zeros(len(X))
    for b in B:
        te=X.bearing_id==b; tr=~te
        if cols is None:
            oof[te]=(X.loc[te,'E_mx_rms_r_med5']>1.3).astype(float)
        else:
            m=ExtraTreesClassifier(n_estimators=200,min_samples_leaf=20,max_features=0.3,n_jobs=3,random_state=0)
            m.fit(X.loc[tr,cols],X.loc[tr,'y'],sample_weight=w[tr]); oof[te]=m.predict_proba(X.loc[te,cols])[:,1]
    for b in B:
        sub=X[X.bearing_id==b]; a,on,pre,npre=alarm_eval(oof[sub.index],sub)
        rows.append((b[7:],on,a,on-a if a>=0 else None,pre,npre))
    r=pd.DataFrame(rows,columns=['b','GT_on_RUL','alarm_RUL','delay','pre_onset_alarm_min','pre_onset_min'])
    res[name]=r
    dl=r.delay.dropna()
    print(f'{name:5s} delay median {dl.median():.0f} min, |delay|<=15: {(dl.abs()<=15).sum()}/15, missed {r.alarm_RUL.lt(0).sum()}, '
          f'alarm RUL>=30: {(r.alarm_RUL>=30).sum()}/15, pre-onset alarm min total {r.pre_onset_alarm_min.sum()} of {r.pre_onset_min.sum()}')
out=res['E'][['b','GT_on_RUL']].copy()
for k,v in res.items(): out[k+'_alarmRUL']=v.alarm_RUL.values
print(out.to_string(index=False)); out.to_csv(H/'detector_check.csv',index=False)
