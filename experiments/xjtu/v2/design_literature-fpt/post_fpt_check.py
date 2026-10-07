"""Feasibility: (a) literature FPT variants incl. Li-2015 kurtosis-3sigma and a dual-gate variant;
(b) is end-of-life reachable from an amplitude HI (XJTU-SY failure criterion = amplitude > 10x normal-stage max);
(c) post-FPT RUL: causal exponential-HI extrapolation vs piecewise-linear constant baselines, LOBO."""
import numpy as np, pandas as pd
F = pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle']); B=20
def first_run(mask,k):
    r=0
    for i,m in enumerate(mask):
        r=r+1 if m else 0
        if r>=k: return i
    return None
def zs(x):
    base=x[:B]; mu=np.median(base); sd=max(1.4826*np.median(np.abs(base-mu)),0.02*abs(mu),1e-12); return (x-mu)/sd
def zstd(x):
    base=x[:B]; mu=base.mean(); sd=max(base.std(ddof=1),1e-12); return (x-mu)/sd
rows=[]; traj={}
for b,g in F.groupby('bearing_id',sort=False):
    g=g.reset_index(drop=True); n=len(g); rul=g.rul_minutes.to_numpy(); post=np.arange(n)>=B
    rr=np.maximum(g.h_rms/np.median(g.h_rms[:B]), g.v_rms/np.median(g.v_rms[:B])).to_numpy()
    pk=np.maximum(g.h_peak/np.max(g.h_peak[:B]), g.v_peak/np.max(g.v_peak[:B])).to_numpy()
    r5=pd.Series(rr).rolling(5,min_periods=1).median().to_numpy()
    zk_std=np.maximum(zstd(g.h_kurtosis.to_numpy()),zstd(g.v_kurtosis.to_numpy()))
    zr_std=np.maximum(zstd(g.h_rms.to_numpy()),zstd(g.v_rms.to_numpy()))
    zr=np.maximum(zs(g.h_rms.to_numpy()),zs(g.v_rms.to_numpy()))
    zr5=pd.Series(zr).rolling(5,min_periods=1).median().to_numpy()
    zr_std5=pd.Series(zr_std).rolling(5,min_periods=1).median().to_numpy()
    o={'bearing':b,'life':n}
    def rec(name,mask,k):
        i=first_run(mask&post,k); o[name]=None if i is None else int(rul[i]); return i
    rec('li2015_kurt3s_l2',zk_std>3,3)
    rec('rms3s_std_l2',zr_std>3,3)
    rec('rms3s_std_med5_k5',zr_std5>3,5)
    i_dual=rec('dual_z3_ratio1.2_med5_k5',(zr5>3)&(r5>1.2),5)
    rec('dual_z3_ratio1.15_med5_k5',(zr5>3)&(r5>1.15),5)
    o['peakratio_end']=round(float(pk[-1]),1); o['rmsratio_end']=round(float(rr[-1]),2)
    o['peak10x_rul']=next((int(rul[i]) for i in range(B,n) if pk[i]>=10),None)
    o['rms_ratio_at_rul30']=round(float(r5[np.argmin(np.abs(rul-30))]),2) if n>30 else None
    rows.append(o); traj[b]=(rul,r5,i_dual)
R=pd.DataFrame(rows); pd.set_option('display.width',250); print(R.to_string(index=False))
R.to_csv('experiments/xjtu/v2/design_literature-fpt/post_fpt_check.csv',index=False)
# (c) post-FPT RUL using dual detector FPT. Exponential HI model: log r(t)=a+b*(t-tf), predict time when r hits D.
# D chosen leave-one-bearing-out as median end-of-life r5 of the other bearings (failure threshold learned from history).
endr={b:traj[b][1][-1] for b in traj}
res=[]
for b,(rul,r5,i) in traj.items():
    if i is None: continue
    D=np.median([np.log(v) for k,v in endr.items() if k!=b])
    # constant baseline: post-FPT life of other bearings (median)
    others=[traj[k][0][traj[k][2]] for k in traj if k!=b and traj[k][2] is not None]
    medlife=np.median(others)
    for j in range(i,len(rul)):
        t=np.arange(i,j+1); y=np.log(np.maximum(r5[i:j+1],1e-6))
        w=min(len(t),30); t2=t[-w:]; y2=y[-w:]
        if len(t2)>=3 and np.ptp(t2)>0:
            bb,aa=np.polyfit(t2-t2[0],y2,1)
        else: bb=0
        cur=y[-1]
        pred=(D-cur)/bb if bb>1e-4 else 1e9
        pred=float(np.clip(pred,0,600))
        pl=max(medlife-(j-i),0)   # piecewise-linear: median post-FPT life minus time since FPT
        res.append((b,rul[j],pred,pl,min(rul[j],600)))
E=pd.DataFrame(res,columns=['b','rul','exp','pl','rulc'])
for name in ('exp','pl'):
    e=(E[name]-E.rulc).abs(); 
    print(name,'MAE all post-FPT',round(e.mean(),1),'| MAE rul<=60',round(e[E.rul<=60].mean(),1),
          '| per-bearing median MAE',round(E.assign(e=e).groupby('b').e.mean().median(),1))
# reachability: fraction of true rul<=30 rows predicted <=30
for name in ('exp','pl'):
    m=E.rul<=30; print(name,'P(pred<=30 | rul<=30)',round((E[name][m]<=30).mean(),2),' P(pred<=30 | rul>60)',round((E[name][E.rul>60]<=30).mean(),2))
# ratio-based critical: r5 > Rc reached when rul<=30?
for Rc in (2,3,4):
    tp=fp=0; tot=0
    for b,(rul,r5,i) in traj.items():
        tp+=((r5>Rc)&(rul<=30)).sum(); tot+=(rul<=30).sum(); fp+=((r5>Rc)&(rul>60)).sum()
    print('ratio>',Rc,'recall on rul<=30',round(tp/tot,2),' rows with rul>60 flagged',fp)
