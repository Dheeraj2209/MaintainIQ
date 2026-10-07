import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from experiments.xjtu.harness import load_context, compute_report, summary_metrics, smooth_probabilities, sustained_warning_mask
ctx=load_context().sort_values(['bearing_id','cycle']).reset_index(drop=True)
d=pd.read_csv('experiments/xjtu/results/baseline_oof.csv').sort_values(['bearing_id','cycle']).reset_index(drop=True)
y=ctx.rul_minutes.values; p=d.raw_prob.values; rp=d.rul_pred.values
def show(name,**kw):
    s=summary_metrics({**compute_report(ctx,y,p,rp,**kw)})
    print(f"{name:35s}",{k:round(s[k],3) for k in ['average_precision','precision','recall','f1','false_alarm_count','missed_failure_window_count','within_horizon_bearings','early_warning_bearings','no_warning_bearings']})
show('baseline')
for th in (0.5,0.55,0.65,0.7): show(f'th{th}',threshold=th)
for pers in (1,5,10): show(f'pers{pers}',persistence=pers)
def latch(t,raw):
    sm=smooth_probabilities(t,raw,3); w=sustained_warning_mask(t,sm,0.6,3) if sustained_warning_mask.__code__.co_argcount>2 else None
    out=np.zeros(len(t),bool)
    for b,idx in t.groupby('bearing_id').groups.items():
        idx=np.asarray(idx); out[idx]=np.maximum.accumulate(w[idx])
    return out
try: show('latch_after_first_warning',postprocess_fn=latch)
except Exception as e: print('latch err',e)
# within-bearing ranking vs pooled
from sklearn.metrics import roc_auc_score
print('pooled raw AUC',round(roc_auc_score(y<=120,p),3))
# per-bearing rank-normalised prob (non-causal oracle-ish, diagnostic only)
