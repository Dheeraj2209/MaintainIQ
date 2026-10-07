import sys; sys.path.insert(0,'.')
import pandas as pd, numpy as np, re
from src.training.xjtu_rul import classifier_feature_columns, make_classifier
c=pd.read_parquet('experiments/xjtu/cache/context.parquet').sort_values(['bearing_id','cycle']).reset_index(drop=True)
cols=classifier_feature_columns(c); y=c.rul_minutes.values<=120
print('n clf features',len(cols))
m=make_classifier(42); m.set_params(classifier__n_jobs=3); m.fit(c[cols],y)
imp=pd.Series(m.named_steps['classifier'].feature_importances_,cols).sort_values(ascending=False)
print(imp.head(30).round(4).to_string())
def fam(n):
    if n in('speed_rpm','load_kn'):return 'operating_cond'
    ctx=('baseline_ratio' if 'baseline_ratio' in n else 'ctx_roll' if re.search(r'_(mean|std|trend)_\d+$',n) else 'raw')
    base=re.sub(r'_(baseline_ratio|baseline_delta|mean_\d+|std_\d+|trend_\d+)$','',n)
    base=re.sub(r'^[hvm]_','',base)
    kind=('energy_band' if 'energy' in base else base)
    return f'{kind}|{ctx}'
f=imp.groupby(imp.index.map(fam)).sum().sort_values(ascending=False)
print(f.round(4).to_string())
ctx=imp.groupby(imp.index.map(lambda n: 'operating_cond' if n in('speed_rpm','load_kn') else 'baseline_ratio' if 'baseline_ratio' in n else 'rolling' if re.search(r'_(mean|std|trend)_\d+$',n) else 'raw_snapshot')).sum()
print(ctx.round(3))
imp.to_csv('experiments/xjtu/diag/clf_importance.csv')
