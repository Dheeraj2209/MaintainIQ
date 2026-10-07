import pandas as pd, numpy as np
pd.set_option('display.width',250); pd.set_option('display.max_columns',30)
d=pd.read_csv('experiments/xjtu/results/baseline_oof.csv').sort_values(['bearing_id','cycle']).reset_index(drop=True)
d['fa']=d.warning&~d.in_horizon; d['miss']=~d.warning&d.in_horizon
print(d.groupby('bearing_id')[['fa','miss','warning']].sum().T)
# trajectories: mean smoothed prob in RUL bins
bins=[-1,15,30,60,90,120,150,180,240,400,800,1500,3000]
d['rb']=pd.cut(d.rul_minutes,bins)
t=d.pivot_table(index='bearing_id',columns='rb',values='smoothed_prob',aggfunc='mean',observed=False)
print(t.round(2).to_string())
# fraction above threshold
t2=d.assign(a=d.smoothed_prob>=0.6).pivot_table(index='bearing_id',columns='rb',values='a',aggfunc='mean',observed=False)
print(t2.round(2).to_string())
