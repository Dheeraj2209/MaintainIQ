"""Quick univariate screen of rs_* context features (pooled AUC vs y<=120, diagnostic only)."""
import sys, importlib.util; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score
spec=importlib.util.spec_from_file_location('exp','experiments/xjtu/exp_raw-signal-features.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
t=m.build_table(); y=(t.rul_minutes<=120).to_numpy()
print(t.shape)
rows=[]
for c in [c for c in t.columns if c.startswith('rs_') and (c.endswith('__rel_mean5') or c.endswith('__raw_mean5'))]:
    x=t[c].to_numpy(); a=roc_auc_score(y,x)
    # within-bearing spearman with rul inside horizon
    sp=[]
    for b,g in t[t.rul_minutes<=120].groupby('bearing_id'):
        if g[c].std()>0: sp.append(g[c].corr(g.rul_minutes,method='spearman'))
    rows.append((c,max(a,1-a),average_precision_score(y,x if a>=.5 else -x),np.nanmedian(sp)))
r=pd.DataFrame(rows,columns=['f','auc','ap','wb_spearman']).sort_values('auc',ascending=False)
print(r.head(30).to_string()); print(r.tail(8).to_string())
