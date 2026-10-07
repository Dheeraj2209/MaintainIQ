"""Diagnostic ablation (lite: 1 seed, 150 trees; NOT comparable to full baseline numerically,
only to the lite-baseline row). Regressor replaced by a dummy to save time."""
import sys,json; sys.path.insert(0,'.')
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.dummy import DummyRegressor
from experiments.xjtu.harness import load_context, evaluate, summary_metrics
from src.training.xjtu_rul import classifier_feature_columns
ctx=load_context()
def mk(seed,n_jobs): return Pipeline([('i',SimpleImputer(strategy='median')),('c',ExtraTreesClassifier(n_estimators=150,min_samples_leaf=3,max_features=0.8,class_weight='balanced',n_jobs=n_jobs,random_state=seed))])
mr=lambda seed,n_jobs: DummyRegressor()
base=classifier_feature_columns
nocond=lambda t:[c for c in base(t) if c not in('speed_rpm','load_kn')]
relonly=lambda t:[c for c in nocond(t) if 'baseline_ratio' in c or '_std_' in c or '_trend_' in c]
out={}
for name,fn in [('lite_baseline',base),('no_speed_load',nocond),('relative_only_no_cond',relonly)]:
    r=evaluate(ctx,make_classifier=mk,make_regressor=mr,classifier_feature_fn=fn,ensemble_seeds=(42,),n_jobs=3)
    s=summary_metrics(r); s['n_feat']=r.get('classifier_feature_count'); out[name]=s
    fw={e['bearing_id']:(e['timing'],e['actual_rul_minutes_at_first_warning']) for e in r['first_warning_events']['per_bearing']} if 'first_warning_events' in r else None
    out[name+'_first_warn']=fw
    np.save(f'experiments/xjtu/diag/oof_{name}.npy',r['oof']['raw_prob'])
    print(name,json.dumps({k:(round(v,3) if isinstance(v,float) else v) for k,v in s.items()}),flush=True)
json.dump(out,open('experiments/xjtu/diag/ablation.json','w'),indent=1,default=str)
