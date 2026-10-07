"""Post-onset RUL feasibility. Anchor = causal heuristic alarm (onset detected online). LOBO over 14 bearings
with an alarm. Compares constant, conditional-survival (KM-style) on time-since-onset, and ET on causal
severity features + time-since-onset, target log1p(RUL). Bearing-equal weighting."""
import numpy as np, pandas as pd, runpy
from pathlib import Path
from sklearn.ensemble import ExtraTreesRegressor
H=Path(__file__).parent
ns=runpy.run_path(str(H/'detector_check.py').replace('detector_check','_feat_only'),run_name='x') if False else None
src=(H/'detector_check.py').read_text().split("sets={")[0]; ns={'__file__':str(H/'detector_check.py')}; exec(src,ns)
X=ns['X']
rows=[]
for b,g in X.groupby('bearing_id',sort=False):
    s=(g.E_mx_rms_r_med5>1.3).values; run=np.convolve(s,np.ones(5),'full')[:len(s)]>=5
    i=np.where(run)[0]
    if not len(i): continue
    gg=g.iloc[i[0]:].copy(); gg['since']=np.arange(len(gg)); gg['Lpost']=len(gg); rows.append(gg)
P=pd.concat(rows); B=P.bearing_id.unique()
print('post-alarm lives:',P.groupby('bearing_id').Lpost.first().sort_values().to_dict())
sev=[c for c in P if c.startswith('E_') and ('med5' in c or 'med20' in c or 'std10' in c)]+[c for c in P if c.startswith('N_') and 'med5' in c]
w=1/P.groupby('bearing_id').bearing_id.transform('count')
pred={k:np.zeros(len(P)) for k in ('const','surv','ET','ET_nosince')}
for b in B:
    te=(P.bearing_id==b).values; tr=~te
    L=P[tr].groupby('bearing_id').Lpost.first().values
    pred['const'][te]=np.median(L)
    sn=P.since.values[te]
    pred['surv'][te]=[np.median(L[L>s]-s) if (L>s).any() else 1.0 for s in sn]
    for nm,cols in (('ET',sev+['since']),('ET_nosince',sev)):
        m=ExtraTreesRegressor(n_estimators=200,min_samples_leaf=10,max_features=0.3,n_jobs=3,random_state=0)
        m.fit(P.loc[tr,cols],np.log1p(P.rul[tr]),sample_weight=w[tr]); pred[nm][te]=np.expm1(m.predict(P.loc[te,cols]))
y=P.rul.values
for k,p in pred.items():
    e=np.abs(p-y); bm=pd.Series(e).groupby(P.bearing_id.values).mean()
    crit=y<=30; pc=p<=30
    late=(p>y)
    print(f'{k:10s} row-MAE {e.mean():6.1f} bearing-MAE {bm.mean():6.1f} | MAE@RUL<=60 {e[y<=60].mean():5.1f} | '
          f'critical recall {pc[crit].mean():.2f} prec {crit[pc].mean() if pc.any() else 0:.2f} | min pred {p.min():.0f}')
# severity-only critical rule check
for thr in (2,3,4,6):
    s=np.maximum(P.E_h_rms_r_med5,P.E_v_rms_r_med5).values>thr
    print(f'severity rms med5>{thr}: P(RUL<=30|rule)={ (y[s]<=30).mean():.2f}, recall of RUL<=30 rows={s[y<=30].mean():.2f}, bearings covered={P[s & (y<=30)].bearing_id.nunique()}')
