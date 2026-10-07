import numpy as np, pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.model_selection import cross_val_score, StratifiedKFold
f = pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle'])
meta={'bearing_id','condition','cycle','elapsed_minutes','rul_minutes','speed_rpm','load_kn','source_file'}
feats=[c for c in f.columns if c not in meta]
# constant / near constant
nun=f[feats].nunique(); std=f[feats].std()
print('constant cols', list(nun[nun<=1].index))
cv=(std/f[feats].abs().mean()).sort_values()
print('lowest CV:\n', cv.head(12).round(5))
# dominant freq distribution
for ax in 'hvm':
    print(ax,'dominant_freq top values', f[f'{ax}_dominant_freq'].round(1).value_counts().head(6).to_dict())
print('h_mean per bearing (DC offset):', f.groupby('bearing_id').h_mean.median().round(4).to_dict())
# healthy-state scale differences: first 20 snapshots
early=f.groupby('bearing_id').head(20)
for c in ['h_rms','v_rms','m_rms','h_kurtosis','h_envelope_rms','h_spectral_centroid','h_energy_0_500_hz_ratio','cross_axis_rms_ratio','h_envelope_energy_100_200_hz_ratio','h_dominant_freq','h_mean','v_mean']:
    s=early.groupby('bearing_id')[c].median(); sc=early.groupby('condition')[c].median()
    print(f'{c:38s} healthy median by bearing min {s.min():.4g} max {s.max():.4g} ratio {s.max()/max(s.min(),1e-12):.2f}; by cond {sc.round(4).to_dict()}')
# identity leakage: can healthy-only (first 20 rows) features identify bearing? Use within-bearing time split: train first 10, test next 10
tr=f.groupby('bearing_id').head(10); te=f.groupby('bearing_id').nth(list(range(10,20)))
m=ExtraTreesClassifier(300,n_jobs=3,random_state=0).fit(tr[feats],tr.bearing_id)
print('bearing-ID acc (train cycles0-9 -> test 10-19), raw features:', (m.predict(te[feats])==te.bearing_id).mean())
imp=pd.Series(m.feature_importances_,feats).sort_values(ascending=False); print('top ID features', imp.head(10).round(3).to_dict())
# condition id from features
dimless=[c for c in feats if any(t in c for t in ("_kurtosis","_skewness","_crest_factor","_shape_factor","_impulse_factor","_clearance_factor","_energy_","_spectral_entropy","cross_axis_"))]
m2=ExtraTreesClassifier(300,n_jobs=3,random_state=0).fit(tr[dimless],tr.bearing_id)
print('bearing-ID acc dimensionless only:', (m2.predict(te[dimless])==te.bearing_id).mean())
imp2=pd.Series(m2.feature_importances_,dimless).sort_values(ascending=False); print('top ID dimless', imp2.head(8).round(3).to_dict())
# ratio of failure-time to healthy rms per bearing (already in onset). correlation of features with rul inside horizon, pooled vs within-bearing
ih=f[f.rul_minutes<=120]
rows=[]
for c in feats:
    pooled=ih[[c,'rul_minutes']].corr(method='spearman').iloc[0,1]
    wb=ih.groupby('bearing_id').apply(lambda d: d[[c,'rul_minutes']].corr(method='spearman').iloc[0,1]).median()
    rows.append((c,pooled,wb))
r=pd.DataFrame(rows,columns=['f','pooled_spearman','median_within_bearing']).dropna()
r['abs']=r.median_within_bearing.abs(); print(r.sort_values('abs',ascending=False).head(15).round(3).to_string())
