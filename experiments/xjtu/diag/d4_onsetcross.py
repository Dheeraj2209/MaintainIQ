import pandas as pd, numpy as np
c=pd.read_parquet('experiments/xjtu/cache/context.parquet').sort_values(['bearing_id','cycle']).reset_index(drop=True)
d=pd.read_csv('experiments/xjtu/results/baseline_oof.csv').sort_values(['bearing_id','cycle']).reset_index(drop=True)
assert (c.cycle.values==d.cycle.values).all()
rr=np.maximum(c.h_rms_baseline_ratio,c.v_rms_baseline_ratio).values
# causal "degraded" flag: rms ratio>1.3 for current snapshot (rolling 5 median)
rrm=pd.Series(rr).groupby(c.bearing_id).transform(lambda s:s.rolling(5,min_periods=1).median()).values
deg=rrm>1.3
inh=d.in_horizon.values; w=d.warning.values
print('in-horizon rows',inh.sum(),' of which visibly degraded (rms5med>1.3):',(inh&deg).sum())
print('misses',(inh&~w).sum(),' misses pre-onset(not degraded):',(inh&~w&~deg).sum(),' misses while degraded:',(inh&~w&deg).sum())
print('FA',(~inh&w).sum(),' FA while degraded:',(~inh&w&deg).sum())
print('out-of-horizon rows degraded:',(~inh&deg).sum(), 'by bearing:',pd.Series(~inh&deg).groupby(c.bearing_id).sum().to_dict())
# in first 20 baseline rows and in-horizon
first20=c.groupby('bearing_id').cumcount().values<20
print('in-horizon rows inside baseline window (first 20):',(inh&first20).sum(),' misses there',(inh&first20&~w).sum())
# simple heuristic detector ranking: AUC of rrm
from sklearn.metrics import roc_auc_score, average_precision_score
for name,s in [('model',d.smoothed_prob.values),('rms_ratio_med5',rrm)]:
    print(name,'AUC',round(roc_auc_score(inh,s),3),'AP',round(average_precision_score(inh,s),3))
# label alternative: 'degraded within horizon' -- what fraction positives
