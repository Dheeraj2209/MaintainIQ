"""Check candidate causal state rules: pre-onset false episodes (degrading), and RUL at critical firing."""
import numpy as np, pandas as pd
d = pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle'])
on = pd.read_csv('experiments/xjtu/v2/design_product-risk/onset_check.csv').set_index('bearing')
B=20; out=[]; crit_rows=[]
def cp_onset(logx):
    # hindsight 2-segment mean-shift changepoint on log HI (full trajectory, non-causal), min seg 3
    n=len(logx); best=(np.inf,None); cs=np.cumsum(logx); cs2=np.cumsum(logx**2)
    for k in range(3,n-2):
        l=cs2[k-1]-cs[k-1]**2/k; r=(cs2[-1]-cs2[k-1])-(cs[-1]-cs[k-1])**2/(n-k)
        if l+r<best[0]: best=(l+r,k)
    return best[1]
for b,g in d.groupby('bearing_id'):
    g=g.reset_index(drop=True); rul=g.rul_minutes.values
    hi=np.maximum(g.h_rms.values/g.h_rms.iloc[:B].median(), g.v_rms.values/g.v_rms.iloc[:B].median())
    c5=pd.Series(hi).rolling(5,min_periods=1).median().values
    # degrading rule: c5>=1.3 for 3 consecutive -> episodes
    flag=c5>=1.3; s=pd.Series(flag); runlen=s.groupby((~s).cumsum()).cumsum().values; alarm=runlen>=3
    gt=on.loc[b,'rul@self1.3']
    pre = alarm & (rul>gt) if not np.isnan(gt) else alarm
    ep = int(((pre[1:]) & (~pre[:-1])).sum() + pre[0])
    k=cp_onset(np.log(hi)); cp_rul=float(rul[k])
    # critical rule A: c5>=3 ; rule B: c5>=2 and c5/c5[t-10]>=1.5 (acceleration) ; rule C: c5>=5
    lag=pd.Series(c5).shift(10).bfill().values
    rules={'A_x3':c5>=3,'B_x2_acc':(c5>=2)&(c5/lag>=1.5),'C_x5':c5>=5}
    row=dict(bearing=b,gt_rul=gt,cp_rul=cp_rul,pre_onset_episodes=ep,pre_onset_rows=int(pre.sum()))
    for nm,f in rules.items():
        row['first_'+nm]=float(rul[np.argmax(f)]) if f.any() else np.nan
        crit_rows.append(dict(bearing=b,rule=nm,n=int(f.sum()),le30=int((f&(rul<=30)).sum()),le60=int((f&(rul<=60)).sum())))
    out.append(row)
pd.set_option('display.width',250)
print(pd.DataFrame(out).to_string(index=False))
cr=pd.DataFrame(crit_rows); agg=cr.groupby('rule')[['n','le30','le60']].sum(); agg['prec30']=agg.le30/agg.n; agg['prec60']=agg.le60/agg.n
print(agg)
# per-bearing macro precision
cr['p30']=cr.le30/cr.n.replace(0,np.nan); print(cr.groupby('rule').p30.agg(['mean','min']))
