import pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_columns',30)
c=pd.read_parquet('experiments/xjtu/cache/context.parquet').sort_values(['bearing_id','cycle']).reset_index(drop=True)
rows=[]
for b,x in c.groupby('bearing_id'):
    r=x.rul_minutes.values
    def onset(col,thr,k=5):
        v=x[col].values; a=v>thr
        s=pd.Series(a).rolling(k,min_periods=k).sum().eq(k).values
        i=np.flatnonzero(s); return r[i[0]-k+1] if len(i) else np.nan
    rr=np.maximum(x.h_rms_baseline_ratio,x.v_rms_baseline_ratio)
    x=x.assign(rms_ratio=rr, kurt=np.maximum(x.h_kurtosis,x.v_kurtosis))
    rows.append(dict(bearing=b,cond=x.condition.iloc[0],life=len(x),
        base_h_rms=x.h_rms.iloc[:20].median(),base_v_rms=x.v_rms.iloc[:20].median(),
        base_h_kurt=x.h_kurtosis.iloc[:20].median(),
        rms_ratio_end=rr.iloc[-5:].median(), rms_ratio_at120=rr.iloc[max(len(x)-121,0)],
        onset_rms1_3=onset_r if (onset_r:=onset.__call__('h_rms_baseline_ratio',1.3)) else np.nan,
        onset_rmsmax1_3=(lambda v:(lambda s:r[np.flatnonzero(s)[0]-4] if s.any() else np.nan)(pd.Series(v>1.3).rolling(5,min_periods=5).sum().eq(5).values))(rr.values),
        onset_rmsmax2=(lambda v:(lambda s:r[np.flatnonzero(s)[0]-4] if s.any() else np.nan)(pd.Series(v>2).rolling(5,min_periods=5).sum().eq(5).values))(rr.values),
        onset_kurt4=onset('h_kurtosis',4),
    ))
print(pd.DataFrame(rows).round(2).to_string())
