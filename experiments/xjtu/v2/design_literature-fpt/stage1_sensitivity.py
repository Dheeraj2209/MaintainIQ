"""Stage-1 sensitivity: causal dual-gate detector (z_rms>S sigma on robust commissioning stats AND rms ratio>G, med5,
k consecutive; exit to healthy after 30 consecutive rows with ratio<1.1). Scored against the offline GT interval
from fpt_check.csv (gt_perm_rul .. gt_hinge_rul). Reports false onsets (episodes that later revert),
delay vs GT-late, and pre-failure lead time of the final onset."""
import numpy as np, pandas as pd, itertools
F=pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle']);B=20
GT=pd.read_csv('experiments/xjtu/v2/design_literature-fpt/fpt_check.csv').set_index('bearing')
def zs(x):
    base=x[:B];mu=np.median(base);sd=max(1.4826*np.median(np.abs(base-mu)),0.02*abs(mu),1e-12);return (x-mu)/sd
pre={}
for b,g in F.groupby('bearing_id',sort=False):
    g=g.reset_index(drop=True)
    rr=np.maximum(g.h_rms/np.median(g.h_rms[:B]),g.v_rms/np.median(g.v_rms[:B])).to_numpy()
    pre[b]=(g.rul_minutes.to_numpy(),pd.Series(rr).rolling(5,min_periods=1).median().to_numpy(),
            pd.Series(np.maximum(zs(g.h_rms.to_numpy()),zs(g.v_rms.to_numpy()))).rolling(5,min_periods=1).median().to_numpy())
res=[]
for S,G,k in itertools.product((3,4,6),(1.1,1.2,1.3),(3,5,8)):
    false_on=0; leads=[]; delays=[]; never=0; per={}
    for b,(rul,r5,z5) in pre.items():
        trig=(z5>S)&(r5>G); st=0;run=0;calm=0;onsets=[];final=None
        for i in range(B,len(rul)):
            if st==0:
                run=run+1 if trig[i] else 0
                if run>=k: st=1;onsets.append(i);calm=0
            else:
                calm=calm+1 if r5[i]<1.1 else 0
                if calm>=30: st=0;run=0
        if st==1: final=onsets[-1]
        false_on+=len(onsets)-(1 if st==1 else 0)
        if final is None: never+=1; leads.append(0); continue
        L=int(rul[final]); leads.append(L); delays.append(GT.loc[b,'gt_perm_rul']-L); per[b]=L
    res.append(dict(S=S,G=G,k=k,reverted_onsets=false_on,never=never,median_lead=np.median(leads),min_lead=min(leads),
                    median_delay_vs_gt_perm=np.median(delays),n_lead_lt15=sum(l<15 for l in leads)))
R=pd.DataFrame(res);print(R.to_string(index=False))
