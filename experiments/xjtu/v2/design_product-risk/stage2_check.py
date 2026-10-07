"""LOBO check: conditional-on-onset RUL quantiles (q10 lower bound, q50) from causal post-onset features."""
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor as H
d=pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle']); B=20; parts=[]
for b,g in d.groupby('bearing_id'):
    g=g.reset_index(drop=True)
    hi=np.maximum(g.h_rms.values/g.h_rms.iloc[:B].median(), g.v_rms.values/g.v_rms.iloc[:B].median())
    c5=pd.Series(hi).rolling(5,min_periods=1).median()
    s=c5>=1.3; run=s.groupby((~s).cumsum()).cumsum()>=3
    if not run.any(): continue
    t0=int(np.argmax(run.values))
    # latch from first onset (re-onset handling ignored here)
    p=pd.DataFrame(dict(bearing=b,rul=g.rul_minutes.values,tso=np.arange(len(g))-t0,lc5=np.log(c5.values),
        lmax=np.log(pd.Series(hi).cummax().values), slope10=np.log(c5).diff(10).values,
        kurt=pd.Series(np.maximum(g.h_kurtosis,g.v_kurtosis)).rolling(5,min_periods=1).median().values,
        env=np.log(pd.Series(np.maximum(g.h_envelope_rms/g.h_envelope_rms.iloc[:B].median(),g.v_envelope_rms/g.v_envelope_rms.iloc[:B].median())).rolling(5,min_periods=1).median().values)))
    parts.append(p[p.tso>=0])
P=pd.concat(parts).fillna(0); F=['tso','lc5','lmax','slope10','kurt','env']; res=[]
for b in P.bearing.unique():
    tr,te=P[P.bearing!=b],P[P.bearing==b]
    w=1/tr.groupby('bearing').rul.transform('size')  # equal weight per bearing
    q={}
    for a in (0.1,0.5):
        m=H(loss='quantile',quantile=a,max_iter=150,max_leaf_nodes=8,min_samples_leaf=20,learning_rate=0.05,random_state=0)
        m.fit(tr[F],np.log1p(tr.rul),sample_weight=w); q[a]=np.expm1(m.predict(te[F]))
    y=te.rul.values; crit=q[0.1]<=30; true=y<=30
    res.append(dict(bearing=b,n=len(te),cov_q10=np.mean(y>=q[0.1]),mae_q50=np.mean(abs(y-q[0.5])),
        mae_const_median=np.mean(abs(y-np.median(tr.rul))), crit_prec=(crit&true).sum()/max(crit.sum(),1),
        crit_rec=(crit&true).sum()/max(true.sum(),1), first_crit_rul=float(y[np.argmax(crit)]) if crit.any() else np.nan))
r=pd.DataFrame(res); pd.set_option('display.width',200); print(r.round(2).to_string(index=False)); print(r.drop(columns='bearing').mean().round(3))
