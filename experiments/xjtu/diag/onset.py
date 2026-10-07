import numpy as np, pandas as pd
f = pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle'])
o = pd.read_csv('experiments/xjtu/results/baseline_oof.csv')
rows=[]
for b,g in f.groupby('bearing_id'):
    L=len(g)
    base=g.m_rms.iloc[:20].median(); r=(g.m_rms/base).to_numpy()
    bk=g.h_kurtosis.iloc[:20].median()
    # onset: first cycle with ratio>1.3 sustained 3 snapshots
    above=r>1.3; on=None
    for i in range(L-2):
        if above[i:i+3].all(): on=i;break
    oo=o[o.bearing_id==b]; ih=oo[oo.rul_minutes<=120]
    rows.append(dict(bearing=b,life=L,base_mrms=round(base,3),final_ratio=round(r[-1],2),max_ratio=round(r.max(),2),
       onset_rul=None if on is None else L-1-on, ratio_at_rul120=round(r[max(L-121,0)],2) if L>121 else np.nan,
       ratio_at_rul60=round(r[max(L-61,0)],2),
       mae=round((ih.rul_pred-ih.rul_minutes).abs().mean(),1), bias=round((ih.rul_pred-ih.rul_minutes).mean(),1),
       first_warn=oo[oo.warning].rul_minutes.max()))
d=pd.DataFrame(rows); print(d.to_string())
print('corr(onset_rul, bias)', d[['onset_rul','bias']].astype(float).corr().iloc[0,1])
