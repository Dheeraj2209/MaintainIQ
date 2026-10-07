import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
import experiments.xjtu.combined as C
Y,T,IDX,B=C.Y,C.TRUTH,C.IDX,C.BEARINGS
base=pd.read_csv('experiments/xjtu/results/baseline_oof.csv').rul_pred.values
r=C.load_rul('compact300','s0')
yt=Y[T]
print('in-horizon rows',T.sum(),'y mean',yt.mean().round(2),'median',np.median(yt))
for nm,pred in [('const60',np.full(len(Y),60.0)),('baseline',base),('candidate',r)]:
    e=np.abs(pred[T]-yt); print(nm,'MAE',e.mean().round(2),'err90',np.percentile(e,90).round(2),'pred range',pred[T].min().round(1),pred[T].max().round(1),'pred std',pred[T].std().round(1))
# LOBO-honest constant: median of in-horizon y of other bearings
c=np.zeros(len(Y))
for b in B:
    o=np.concatenate([IDX[x] for x in B if x!=b]); c[IDX[b]]=np.median(Y[o][T[o]])
e=np.abs(c[T]-yt); print('lobo const MAE',e.mean().round(2))
corr=lambda p: np.corrcoef(p[T],yt)[0,1]
print('corr baseline',round(corr(base),3),'candidate',round(corr(r),3))
# per-bearing candidate vs const
w=0
for b in B:
    i=IDX[b]; t=T[i]
    mc=np.abs(r[i][t]-Y[i][t]).mean(); mk=np.abs(c[i][t]-Y[i][t]).mean(); w+=mc<mk
print('bearings where candidate beats LOBO constant:',w,'/15')
