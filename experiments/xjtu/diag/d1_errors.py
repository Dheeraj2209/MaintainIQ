import pandas as pd, numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score
d=pd.read_csv('experiments/xjtu/results/baseline_oof.csv').sort_values(['bearing_id','cycle']).reset_index(drop=True)
d['fa']=d.warning&~d.in_horizon; d['miss']=~d.warning&d.in_horizon
g=d.groupby('bearing_id')
s=pd.DataFrame({'cond':g.condition.first(),'n':g.size(),'n_in':g.in_horizon.sum(),'n_out':(~d.in_horizon).groupby(d.bearing_id).sum(),
 'fa':g.fa.sum(),'miss':g.miss.sum(),'warn':g.warning.sum(),
 'mean_p_in':d[d.in_horizon].groupby('bearing_id').smoothed_prob.mean(),'mean_p_out':d[~d.in_horizon].groupby('bearing_id').smoothed_prob.mean()})
def auc(x):
    try: return roc_auc_score(x.in_horizon,x.smoothed_prob)
    except: return np.nan
s['auc_within']=g.apply(auc)
print(s.round(3)); print('tot fa',s.fa.sum(),'miss',s.miss.sum())
bands=[120,180,400,1000,1e9]
o=d[~d.in_horizon].copy(); o['band']=pd.cut(o.rul_minutes,bands)
print('\nFA by RUL band'); print(o.groupby('band',observed=True).agg(n=('fa','size'),fa=('fa','sum'),p=('smoothed_prob','mean')))
print(pd.crosstab(o.bearing_id,o.band,values=o.fa,aggfunc='sum'))
i=d[d.in_horizon].copy(); i['band']=pd.cut(i.rul_minutes,[-1,30,60,90,120])
print('\nMiss by RUL band'); print(i.groupby('band',observed=True).agg(n=('miss','size'),miss=('miss','sum'),p=('smoothed_prob','mean')))
print(pd.crosstab(i.bearing_id,i.band,values=i.miss,aggfunc='sum'))
# FA contiguous segments
print('\nFA episodes')
for b,x in d.groupby('bearing_id'):
    w=x.warning.values; r=x.rul_minutes.values
    segs=[];st=None
    for k in range(len(w)):
        if w[k] and st is None: st=k
        if (not w[k] or k==len(w)-1) and st is not None:
            e=k if w[k] else k-1; segs.append((r[st],r[e],e-st+1)); st=None
    segs=[sg for sg in segs if sg[0]>120]
    if segs: print(b,len(segs),segs[:12])
# pooled AUC by condition and within-cond
for c,x in d.groupby('condition'):
    print('cond',c,'AUC',round(roc_auc_score(x.in_horizon,x.smoothed_prob),3),'AP',round(average_precision_score(x.in_horizon,x.smoothed_prob),3),'prev',round(x.in_horizon.mean(),3))
# calibration
d['bin']=pd.cut(d.smoothed_prob,np.linspace(0,1,11))
print(d.groupby('bin',observed=True).agg(n=('in_horizon','size'),frac=('in_horizon','mean')))
from sklearn.metrics import brier_score_loss
print('brier',brier_score_loss(d.in_horizon,d.raw_prob),'prev',d.in_horizon.mean())
