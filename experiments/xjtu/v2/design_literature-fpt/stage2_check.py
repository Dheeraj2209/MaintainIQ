"""Stage-2 feasibility: post-FPT RUL regressor trained only on rows after the CAUSAL FPT (dual 3-sigma + 1.2x gate,
med5, k=5, re-arm after 30 in-band rows), LOBO over 15 bearings. Compares to constant and piecewise-linear baselines.
Features are all computable from retained state + 3 new scalars (fpt_cycle, log_hi_at_fpt, hi_peak_since_fpt)."""
import numpy as np, pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
F=pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle']);B=20;CAP=240
def zs(x):
    base=x[:B];mu=np.median(base);sd=max(1.4826*np.median(np.abs(base-mu)),0.02*abs(mu),1e-12);return (x-mu)/sd
rows=[]
for b,g in F.groupby('bearing_id',sort=False):
    g=g.reset_index(drop=True);n=len(g);rul=g.rul_minutes.to_numpy()
    rr=np.maximum(g.h_rms/np.median(g.h_rms[:B]),g.v_rms/np.median(g.v_rms[:B])).to_numpy()
    r5=pd.Series(rr).rolling(5,min_periods=1).median().to_numpy()
    zr5=pd.Series(np.maximum(zs(g.h_rms.to_numpy()),zs(g.v_rms.to_numpy()))).rolling(5,min_periods=1).median().to_numpy()
    zk5=pd.Series(np.maximum(zs(g.h_kurtosis.to_numpy()),zs(g.v_kurtosis.to_numpy()))).rolling(5,min_periods=1).median().to_numpy()
    pk=np.maximum(g.h_peak/np.median(g.h_peak[:B]),g.v_peak/np.median(g.v_peak[:B])).to_numpy()
    trig=(zr5>3)&(r5>1.2); state=0; run=0; calm=0; fpt=None; peak=0
    for i in range(n):
        if i<B: continue
        if state==0:
            run=run+1 if trig[i] else 0
            if run>=5: state=1; fpt=i; peak=r5[i]; calm=0; hfpt=np.log(r5[i])
        else:
            calm=calm+1 if (r5[i]<1.1) else 0
            if calm>=30: state=0; run=0; fpt=None; continue
        if state==1:
            peak=max(peak,r5[i]); lr=np.log(r5)
            rows.append(dict(b=b,cond=b[7],rul=rul[i],tsf=i-fpt,lhi=lr[i],dlhi=lr[i]-hfpt,lpeak=np.log(peak),
                slope10=(lr[i]-lr[max(i-9,0)])/9,slope30=(lr[i]-lr[max(i-29,0)])/29,zk=zk5[i],lpk=np.log(pk[i])))
D=pd.DataFrame(rows);X=['tsf','lhi','dlhi','lpeak','slope10','slope30','zk','lpk']
print('post-FPT rows',len(D),'per bearing',D.groupby('b').size().to_dict())
out=[]
for b in D.b.unique():
    tr=D[D.b!=b];te=D[D.b==b].copy()
    # weight each training bearing equally
    w=1.0/tr.groupby('b').b.transform('size')
    m=ExtraTreesRegressor(300,min_samples_leaf=20,max_features=0.5,n_jobs=3,random_state=0).fit(tr[X],np.minimum(tr.rul,CAP),sample_weight=w)
    te['et']=m.predict(te[X]); te['const']=np.median(np.minimum(tr.rul,CAP))
    first=tr.groupby('b').tsf.max()+1  # post-FPT life of other bearings
    te['pl']=np.clip(np.median(first)-te.tsf,0,CAP)
    out.append(te)
O=pd.concat(out);O['rc']=np.minimum(O.rul,CAP)
for k in ('const','pl','et'):
    e=(O[k]-O.rc).abs();pb=O.assign(e=e).groupby('b').e.mean()
    print(f"{k:6s} rowMAE {e.mean():6.1f}  bearing-avg MAE {pb.mean():6.1f}  MAE|rul<=60 {e[O.rul<=60].mean():6.1f}  "
          f"P(pred<=30|rul<=30) {(O[k][O.rul<=30]<=30).mean():.2f}  P(pred<=30|rul>60) {(O[k][O.rul>60]<=30).mean():.2f}  P(pred<=60|rul<=60) {(O[k][O.rul<=60]<=60).mean():.2f}")
print(O.assign(e=(O.et-O.rc).abs(), ec=(O.const-O.rc).abs()).groupby('b')[['e','ec']].mean().round(1).T.to_string())
# critical-rule reachability: combine regressor with amplitude-severity (XJTU-SY failure = amplitude > 10x normal max)
print('\ncritical rules (post-FPT rows only, LOBO preds):')
for name,mask in [('et<=30',O.et<=30),('lpk>=log5',O.lpk>=np.log(5)),('et<=30 | lpk>=log5',(O.et<=30)|(O.lpk>=np.log(5))),
                  ('et<=30 | lhi>=log3',(O.et<=30)|(O.lhi>=np.log(3))),('et<=40 | lpk>=log6',(O.et<=40)|(O.lpk>=np.log(6)))]:
    r=(mask[O.rul<=30]).mean(); fa=(mask&(O.rul>60)).sum(); fab=(mask&(O.rul>60)).groupby(O.b).sum(); 
    print(f"{name:22s} recall(rul<=30) {r:.2f}  rows rul>60 flagged {fa}  bearings w/ any early-critical {int((fab>0).sum())}  top {fab[fab>0].sort_values(ascending=False).head(4).to_dict()}")
