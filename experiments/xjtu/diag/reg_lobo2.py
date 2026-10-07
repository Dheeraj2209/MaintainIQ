import sys, time; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import HuberRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from experiments.xjtu.harness import load_context
ctx = load_context().sort_values(['bearing_id','cycle']).reset_index(drop=True)
y = ctx.rul_minutes.to_numpy(float); ih = y<=120; g = ctx.bearing_id.to_numpy()
# compact, scale-free health-indicator set
src=['m_rms','h_peak','v_peak','m_envelope_rms','h_kurtosis','v_kurtosis']
X=pd.DataFrame(index=ctx.index)
for c in src:
    r=ctx[f'{c}_baseline_ratio'].clip(lower=1e-3)
    X[f'log_{c}_ratio']=np.log(r)
    for w in (5,20,60): X[f'{c}_mean{w}_ratio']=np.log((ctx[f'{c}_mean_{w}']/ (ctx[c]/r)).clip(lower=1e-3))
    # log-rate: trend / current level
    for w in (20,60): X[f'{c}_reltrend{w}']=ctx[f'{c}_trend_{w}']/ctx[c].abs().clip(lower=1e-9)
X['run_max_log_mrms']=X.groupby(g)['log_m_rms_ratio'].cummax()   # causal; needs running-max state live
def run(name, make, cols):
    t=time.time(); pred=np.full(len(y),np.nan)
    for fold,(tr,te) in enumerate(LeaveOneGroupOut().split(X,y,g),start=1):
        trm=tr[ih[tr]]; m=make(fold); m.fit(X.iloc[trm][cols], y[trm]); pred[te]=np.clip(m.predict(X.iloc[te][cols]),0,120)
    e=pred[ih]-y[ih]; pb=pd.Series(np.abs(e)).groupby(g[ih]).mean()
    print(f'{name:45s} MAE {np.abs(e).mean():6.2f} RMSE {np.sqrt((e**2).mean()):6.2f} slope {np.polyfit(y[ih],pred[ih],1)[0]:.3f} worst {pb.max():.1f} median-bearing {pb.median():.1f} ({time.time()-t:.0f}s)',flush=True)
allc=list(X.columns); nomax=[c for c in allc if c!='run_max_log_mrms']
run('ET leaf3 compact log-ratio set', lambda f: ExtraTreesRegressor(300,min_samples_leaf=3,max_features=0.8,n_jobs=3,random_state=42+f), nomax)
run('ET leaf30 compact log-ratio set', lambda f: ExtraTreesRegressor(300,min_samples_leaf=30,max_features=0.5,n_jobs=3,random_state=42+f), nomax)
run('ET leaf30 compact + running max', lambda f: ExtraTreesRegressor(300,min_samples_leaf=30,max_features=0.5,n_jobs=3,random_state=42+f), allc)
run('Huber linear compact', lambda f: make_pipeline(StandardScaler(),HuberRegressor(max_iter=500)), nomax)
