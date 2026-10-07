import sys, time; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from sklearn.model_selection import LeaveOneGroupOut
from experiments.xjtu.harness import load_context, default_make_regressor
from src.training.xjtu_rul import feature_columns, classifier_feature_columns
ctx = load_context().sort_values(['bearing_id','cycle']).reset_index(drop=True)
y = ctx.rul_minutes.to_numpy(float); ih = y<=120; g = ctx.bearing_id.to_numpy()
allf = feature_columns(ctx); clf = classifier_feature_columns(ctx)
print('regressor feats', len(allf), 'elapsed in?', 'elapsed_minutes' in allf)
def run(name, feats, train_mask=lambda yt: yt<=120, target=lambda yt: yt, inv=lambda p:p, n_est=350):
    t=time.time(); pred=np.full(len(y),np.nan)
    for fold,(tr,te) in enumerate(LeaveOneGroupOut().split(ctx,y,g),start=1):
        m=default_make_regressor(42+fold,3); m.set_params(regressor__n_estimators=n_est)
        trm=tr[train_mask(y[tr])]
        m.fit(ctx.iloc[trm][feats], target(y[trm]))
        pred[te]=np.clip(inv(m.predict(ctx.iloc[te][feats])),0,120)
    e=pred[ih]-y[ih]; pb=pd.Series(np.abs(e)).groupby(g[ih]).mean()
    print(f'{name:40s} MAE {np.abs(e).mean():6.2f} RMSE {np.sqrt((e**2).mean()):6.2f} bias {e.mean():6.2f} slope {np.polyfit(y[ih],pred[ih],1)[0]:.3f} worst-bearing {pb.max():.1f} median-bearing {pb.median():.1f} ({time.time()-t:.0f}s)', flush=True)
    return pred
noel=[c for c in allf if c not in ('elapsed_minutes',)]
p0=run('baseline (all 342, in-horizon train)', allf)
run('drop elapsed_minutes', noel)
run('classifier dimensionless feats (203)', clf)
run('all rows, target min(rul,120)', allf, train_mask=lambda yt: np.ones(len(yt),bool), target=lambda yt: np.minimum(yt,120))
run('rows rul<=240, target min(rul,120)', allf, train_mask=lambda yt: yt<=240, target=lambda yt: np.minimum(yt,120))
run('dimensionless, rows<=240 clip120', clf, train_mask=lambda yt: yt<=240, target=lambda yt: np.minimum(yt,120))
