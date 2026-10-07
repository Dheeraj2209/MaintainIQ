import pandas as pd, numpy as np
pd.set_option('display.width',250)
c=pd.read_parquet('experiments/xjtu/cache/context.parquet').sort_values(['bearing_id','cycle']).reset_index(drop=True)
feats=['h_spectral_entropy','v_spectral_entropy','v_energy_500_1000_hz_ratio','v_envelope_energy_100_200_hz_ratio','h_kurtosis','cross_axis_correlation','h_rms','m_rms_baseline_ratio','h_spectral_entropy_std_60']
g=c.groupby('bearing_id')
early=c[(c.rul_minutes>120)]  # healthy-ish
rows=[]
for f in feats:
    hb=g.apply(lambda x:x[f].iloc[:20].median())
    late=g.apply(lambda x:x.loc[x.rul_minutes<=30,f].median())
    shift=(late-hb)
    rows.append(dict(feat=f,between_bearing_healthy_sd=hb.std(),median_abs_within_shift=shift.abs().median(),
        snr=shift.abs().median()/hb.std(), cond_healthy_medians=hb.groupby(c.groupby('bearing_id').condition.first()).median().round(3).to_dict(),
        frac_bearings_shift_up=(shift>0).mean()))
print(pd.DataFrame(rows).round(3).to_string())
# Healthy-segment probability by condition: rows with rul>400
d=pd.read_csv('experiments/xjtu/results/baseline_oof.csv').sort_values(['bearing_id','cycle']).reset_index(drop=True)
print(d[d.rul_minutes>400].groupby('bearing_id').smoothed_prob.describe()[['mean','50%','max']].round(2))
print('train prevalence by cond', (c.rul_minutes<=120).groupby(c.condition).mean().round(3).to_dict())
# early first-20 rows probability per bearing (healthy start)
print(d.groupby('bearing_id').apply(lambda x:x.smoothed_prob.iloc[:20].mean()).round(2).to_dict())
