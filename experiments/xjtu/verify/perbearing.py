import sys, json
import numpy as np, pandas as pd
sys.path.insert(0, '.')
from sklearn.metrics import average_precision_score, roc_auc_score
import experiments.xjtu.combined as C

TBL, Y, TRUTH, IDX, B = C.TBL, C.Y, C.TRUTH, C.IDX, C.BEARINGS
base = pd.read_csv('experiments/xjtu/results/baseline_oof.csv')
assert (base.bearing_id.values == TBL.bearing_id.values).all() and (base.cycle.values == TBL.cycle.values).all()

def sm(p):
    out = np.zeros(len(p))
    for b in B: out[IDX[b]] = C.roll_median(p[IDX[b]], 3)
    return out
def mask(p, fam, prm):
    m = np.zeros(len(p), bool)
    for b in B: m[IDX[b]] = C.rule_mask(fam, prm, p[IDX[b]])
    return m
PROD = ("med", dict(w=3, T=0.6, P=3)); LATCH = ("latch", dict(w=3, T=0.6, P=3))

def perb(p, rul, m):
    s = sm(p); rows = {}
    for b in B:
        i = IDX[b]; t = TRUTH[i]
        ap = average_precision_score(t, s[i]) if 0 < t.sum() < len(i) else np.nan
        auc = roc_auc_score(t, s[i]) if 0 < t.sum() < len(i) else np.nan
        rows[b] = dict(ap=ap, auc=auc, fa=int((m[i] & ~t).sum()), miss=int((~m[i] & t).sum()),
                       tp=int((m[i] & t).sum()), mae=float(np.abs(rul[i][t] - Y[i][t]).mean()),
                       n_pre=int((~t).sum()))
    return pd.DataFrame(rows).T

bp, br = base.raw_prob.values, base.rul_pred.values
B0 = perb(bp, br, mask(bp, *PROD))
out = {}
fn_warn = pd.read_csv('experiments/xjtu/results/combined_oof.csv').warning.values
for ss in ['s0', 's1', 's2']:
    p = C.load_probs('et_reg', ss); r = C.load_rul('compact300', ss)
    bprod = C.load_probs('prod', ss)
    Bs = perb(bprod, br, mask(bprod, *PROD))  # same-seed baseline classifier
    for rule_name, m in [('fixed', mask(p, *PROD)), ('latch', mask(p, *LATCH))] + ([('fully_nested', fn_warn)] if ss == 's0' else []):
        F = perb(p, r, m)
        for ref_name, R in [('baseline_s0', B0), ('baseline_same_seed', Bs)]:
            d = F - R
            key = f'{ss}/{rule_name}/vs_{ref_name}'
            out[key] = {
                'ap_better/worse': [int((d.ap > 0).sum()), int((d.ap < 0).sum())],
                'auc_better/worse': [int((d.auc > 0).sum()), int((d.auc < 0).sum())],
                'fa_better/worse/tie': [int((d.fa < 0).sum()), int((d.fa > 0).sum()), int((d.fa == 0).sum())],
                'miss_better/worse': [int((d.miss < 0).sum()), int((d.miss > 0).sum())],
                'mae_better/worse': [int((d.mae < 0).sum()), int((d.mae > 0).sum())],
                'fa_total': [int(R.fa.sum()), int(F.fa.sum())], 'miss_total': [int(R.miss.sum()), int(F.miss.sum())],
                'fa_delta_per_bearing': d.fa.astype(int).to_dict(),
                'ap_delta_per_bearing': d.ap.round(3).to_dict(),
                'mae_delta_per_bearing': d.mae.round(2).to_dict(),
            }
            if ss == 's0' and ref_name == 'baseline_s0':
                print(key); print(pd.concat([R[['ap','fa','miss','mae']].add_prefix('b_'), F[['ap','fa','miss','mae']].add_prefix('c_')], axis=1).round(3).to_string())
json.dump(out, open('experiments/xjtu/verify/perbearing.json', 'w'), indent=1, default=float)
for k, v in out.items():
    print(k, {kk: vv for kk, vv in v.items() if 'per_bearing' not in kk})

# bootstrap over bearings (s0, fixed & latch & fully nested): pooled AP delta, FA delta, MAE delta, F1 delta
rng = np.random.default_rng(0)
def pooled(p, rul, m, bs):
    i = np.concatenate([IDX[b] for b in bs]); t = TRUTH[i]; s = sm(p)[i]
    tp = (m[i] & t).sum(); fp = (m[i] & ~t).sum(); fn = (~m[i] & t).sum()
    return average_precision_score(t, s), fp, 2*tp/(2*tp+fp+fn), np.abs(rul[i][t]-Y[i][t]).mean()
p = C.load_probs('et_reg', 's0'); r = C.load_rul('compact300', 's0')
bm = mask(bp, *PROD)
sm_b = sm(bp); sm_c = sm(p)
for name, m in [('fixed', mask(p, *PROD)), ('latch', mask(p, *LATCH)), ('fully_nested', fn_warn)]:
    ds = []
    for _ in range(2000):
        bs = list(rng.choice(B, len(B), replace=True))
        i = np.concatenate([IDX[b] for b in bs]); t = TRUTH[i]
        apd = average_precision_score(t, sm_c[i]) - average_precision_score(t, sm_b[i])
        fad = (m[i] & ~t).sum() - (bm[i] & ~t).sum()
        f1 = lambda mm: 2*(mm[i]&t).sum()/(2*(mm[i]&t).sum()+(mm[i]&~t).sum()+(~mm[i]&t).sum())
        maed = np.abs(r[i][t]-Y[i][t]).mean() - np.abs(br[i][t]-Y[i][t]).mean()
        ds.append((apd, fad, f1(m)-f1(bm), maed))
    ds = np.array(ds)
    q = lambda c: [round(float(x), 4) for x in np.percentile(ds[:, c], [2.5, 50, 97.5])] + [round(float((ds[:, c] > 0).mean()), 3)]
    print(f'bootstrap {name}: AP delta [2.5,50,97.5,P>0] {q(0)}  FA delta {q(1)}  F1 delta {q(2)}  MAE delta {q(3)}')
