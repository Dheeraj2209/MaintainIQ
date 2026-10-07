import numpy as np, pandas as pd
d=pd.read_csv('experiments/xjtu/diag/fault_freq_features.csv')
base=pd.read_csv('outputs/xjtu_features.csv')[['bearing_id','cycle','m_rms','h_peak','v_peak','h_kurtosis','h_envelope_energy_100_200_hz_ratio']]
d=d.merge(base,on=['bearing_id','cycle'])
fault={'Bearing1_1':'BPFO','Bearing1_2':'BPFO','Bearing1_3':'BPFO','Bearing1_4':'FTF','Bearing1_5':'BPFI/BPFO','Bearing2_1':'BPFI','Bearing2_2':'BPFO','Bearing2_3':'FTF','Bearing2_4':'BPFO','Bearing2_5':'BPFO','Bearing3_1':'BPFO','Bearing3_2':'mixed','Bearing3_3':'BPFI','Bearing3_4':'BPFI','Bearing3_5':'BPFO'}
def onset(s, base_med, k=3.0, run=3):
    a=(s>k*base_med).to_numpy()
    for i in range(len(a)-run+1):
        if a[i:i+run].all(): return i
    return None
cols=['env_BPFO_sum_snr','env_BPFI_sum_snr','env_BSF_sum_snr','env_FTF_sum_snr','env_shaft_1x_snr','shaft_1x_rel','bandkurt_3000_6000']
rows=[]
for b,g in d.groupby('bearing_id'):
    g=g.sort_values('cycle'); r={'bearing':b,'fault':fault[b],'life':g.rul_minutes.max()+1}
    first=g.head(20)
    # m_rms onset (1.3x)
    i=onset(g.m_rms, first.m_rms.median(),1.3); r['rms_onset_rul']=None if i is None else int(g.rul_minutes.iloc[i])
    for c in ['env_BPFO_sum_snr','env_BPFI_sum_snr','env_FTF_sum_snr','env_BSF_sum_snr']:
        m=np.maximum(g['h_'+c],g['v_'+c]); bm=np.maximum(first['h_'+c],first['v_'+c]).median()
        i=onset(m,bm,2.0); r[c.split('_')[1]+'_onset_rul']=None if i is None else int(g.rul_minutes.iloc[i])
        r[c.split('_')[1]+'_base']=round(bm,1)
        late=g[g.rul_minutes<=30]; r[c.split('_')[1]+'_last30']=round(np.maximum(late['h_'+c],late['v_'+c]).median(),1)
    # peak ratio vs failure criterion (10x healthy max amplitude)
    Ah=max(first.h_peak.max(), first.v_peak.max()); pk=np.maximum(g.h_peak,g.v_peak)/Ah
    r['peak/Ah_final']=round(pk.iloc[-1],2); r['peak/Ah@rul60']=round(pk[g.rul_minutes==60].iloc[0],2) if (g.rul_minutes==60).any() else None
    r['peak/Ah@rul120']=round(pk[g.rul_minutes==120].iloc[0],2) if (g.rul_minutes==120).any() else None
    rows.append(r)
print(pd.DataFrame(rows).to_string())
# within-horizon spearman with rul (within bearing median) of new features vs old
ih=d[d.rul_minutes<=120]
new=[c for c in d.columns if c.startswith(('h_','v_')) and c not in ('h_peak','v_peak','h_kurtosis','h_envelope_energy_100_200_hz_ratio')]
res=[]
for c in new+['m_rms','h_envelope_energy_100_200_hz_ratio']:
    wb=ih.groupby('bearing_id').apply(lambda x: x[[c,'rul_minutes']].corr(method='spearman').iloc[0,1]).median()
    res.append((c,wb))
r=pd.DataFrame(res,columns=['f','median_within_spearman']).dropna(); r['a']=r.iloc[:,1].abs()
print(r.sort_values('a',ascending=False).head(20).round(3).to_string())
# pre-horizon (rul 120..240) separation: ratio of feature median in 120<rul<=240 vs first-20 baseline
