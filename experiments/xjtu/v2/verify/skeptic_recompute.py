# Independent recompute (no causal.py import) of per-bearing C from final_timeline.csv states.
import numpy as np, pandas as pd, itertools
tl = pd.read_csv('experiments/xjtu/v2/results/final_timeline.csv')
gt = pd.read_csv('experiments/xjtu/v2/onset_gt.csv')
pb = pd.read_csv('experiments/xjtu/v2/results/final_per_bearing.csv')
print(gt.columns.tolist())
M = {'healthy':0,'degrading':1,'faulty':2,'critical':3}
def eps(nh):
    out=[];s=None
    for i,v in enumerate(nh):
        if v and s is None: s=i
        if not v and s is not None: out.append((s,i-1)); s=None
    if s is not None: out.append((s,len(nh)-1))
    return out
def cost(st,rul,te,tl_,glr,w=5):
    ci=np.where(st>=3)[0]
    lead=rul[ci[0]] if len(ci) else np.nan
    LC=1.0 if not len(ci) else max(0,30-lead)/30
    MC=int((not len(ci)) or lead<10); EP=int(len(ci)>0 and lead>60)
    ECh=min(((st>=3)&(rul>60)).sum()/60,4)
    nh=st>=1; e=eps(nh)
    FD=sum(1 for s,_ in e if s<te); FDh=min(nh[:max(te,0)].sum()/60,8)
    after=[s for s,x in e if x>=tl_]; td=after[0] if after else len(st)
    LD=min(max(0,td-tl_)/60,4); EFh=min(((st>=2)&(rul>120)).sum()/60,4)
    return dict(C=10*LC+w*EP+.5*ECh+.5*FD+.25*FDh+LD+.25*EFh,MC=MC,EP=EP)
g=gt.set_index('bearing')
res={}
for col in ['state','D_state','R0_state']:
    rows={}
    for b,d in tl.groupby('bearing'):
        d=d.sort_values('cycle'); st=d[col].map(M).values; rul=d.rul_true.values.astype(float)
        r=g.loc[b]
        rows[b]={w:cost(st,rul,int(r.gt_early_row),int(r.gt_late_row),float(r.gt_late_rul),w) for w in (3,5,10)}
    res[col]=rows
bs=sorted(res['state'])
for col in res:
    C=np.array([res[col][b][5]['C'] for b in bs])
    print(col, 'C5=%.3f C3=%.3f C10=%.3f MC=%d EP=%d'%(C.mean(),np.mean([res[col][b][3]['C'] for b in bs]),np.mean([res[col][b][10]['C'] for b in bs]),sum(res[col][b][5]['MC'] for b in bs),sum(res[col][b][5]['EP'] for b in bs)))
# compare with per_bearing
for m,col in [('N','state'),('D','D_state'),('R0','R0_state')]:
    p=pb[pb.model==m].set_index('bearing').C.reindex(bs).values
    mine=np.array([res[col][b][5]['C'] for b in bs])
    print(m,'maxabs diff vs per_bearing.csv',np.nanmax(np.abs(p-mine)) if len(p) else 'n/a')
def boot(x,seed,reps=2000):
    rng=np.random.default_rng(seed); idx=rng.integers(0,len(x),(reps,len(x))); m=x[idx].mean(1); return np.percentile(m,[2.5,97.5])
def bootrs(x,seed=1,reps=20000):
    rng=np.random.RandomState(seed); m=np.array([x[rng.randint(0,len(x),len(x))].mean() for _ in range(reps)]); return np.percentile(m,[2.5,97.5])
def sf(d):
    s=np.array(list(itertools.product([1,-1],repeat=len(d)))); return ((s*np.abs(d)).mean(1)<=d.mean()+1e-12).mean()
V={k:np.array([res[c][b][5]['C'] for b in bs]) for k,c in [('N','state'),('D','D_state'),('R0','R0_state')]}
for k in V: print(k, V[k].mean(), boot(V[k],20261006), bootrs(V[k]))
for a,b_ in [('N','D'),('N','R0'),('D','R0')]:
    d=V[a]-V[b_]; print(a,'-',b_, round(d.mean(),3), boot(d,20261006).round(2), bootrs(d).round(2), 'p',round(sf(d),4), 'SE',round(d.std(ddof=1)/np.sqrt(15),3))
print(pd.DataFrame({k:V[k] for k in V},index=bs).round(3))
