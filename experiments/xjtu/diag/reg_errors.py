import numpy as np, pandas as pd
o = pd.read_csv('experiments/xjtu/results/baseline_oof.csv')
L = o.groupby('bearing_id').cycle.max()+1
print('lifetimes', L.to_dict())
ih = o[o.rul_minutes<=120].copy()
ih['err']=ih.rul_pred-ih.rul_minutes; ih['ae']=ih.err.abs()
print('overall MAE',ih.ae.mean(),'bias',ih.err.mean(), 'pred mean',ih.rul_pred.mean(),'pred std',ih.rul_pred.std(),'true std',ih.rul_minutes.std())
print('corr pred/true', np.corrcoef(ih.rul_pred, ih.rul_minutes)[0,1])
# slope of pred on true
b=np.polyfit(ih.rul_minutes, ih.rul_pred,1); print('pred = %.3f*true + %.2f'%tuple(b))
print('constant-60 MAE', (ih.rul_minutes-60).abs().mean(), ' const-median', (ih.rul_minutes-ih.rul_minutes.median()).abs().mean())
ih['band']=pd.cut(ih.rul_minutes,[-1,10,30,60,90,120])
print(ih.groupby('band').agg(n=('ae','size'),mae=('ae','mean'),bias=('err','mean'),pred_mean=('rul_pred','mean')))
g=ih.groupby('bearing_id').agg(n=('ae','size'),mae=('ae','mean'),bias=('err','mean'),pred_mean=('rul_pred','mean'),pred_std=('rul_pred','std'),corr=('rul_pred',lambda s: np.corrcoef(s, ih.loc[s.index,'rul_minutes'])[0,1]))
g['life']=L; print(g.round(2))
# per bearing per band
print(ih.pivot_table(index='bearing_id',columns='band',values='err',aggfunc='mean',observed=False).round(1))
# error vs. warning state (live only regresses when warning active)
w=ih[ih.warning]; print('MAE when warning active', w.ae.mean(), len(w), ' inactive', ih[~ih.warning].ae.mean())
# RUL pred on out-of-horizon rows (what regressor does pre-onset)
oh=o[o.rul_minutes>120]; print('out-of-horizon rul_pred mean',oh.rul_pred.mean(), 'frac <60', (oh.rul_pred<60).mean())
