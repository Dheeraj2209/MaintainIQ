import sys, glob; sys.path.insert(0,'.')
import numpy as np, experiments.xjtu.combined as C
for f in sorted(glob.glob('experiments/xjtu/verify/clf_*.npy')):
    p=np.load(f); 
    for nm,rule in [('fixed',("med",dict(w=3,T=0.6,P=3))),('latch',("latch",dict(w=3,T=0.6,P=3)))]:
        m=np.zeros(len(p),bool)
        for b in C.BEARINGS: m[C.IDX[b]]=C.rule_mask(*rule,p[C.IDX[b]])
        r=C.flat(C.report(p,C.load_rul('compact300'),m))
        print(f.split('/')[-1],nm,{k:r[k] for k in ['roc_auc','average_precision','f1','false_alarm_count','missed_failure_window_count','early_warning_bearings','no_warning_bearings']})
