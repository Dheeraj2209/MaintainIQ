"""Feasibility check: where do literature-style FPT definitions land per XJTU-SY bearing?
Causal detectors (3-sigma on commissioning baseline, k consecutive) vs offline ground truth
(two-segment changepoint on log-RMS, and permanent-exceedance). Read-only on repo data."""
import numpy as np, pandas as pd, json, sys
F = pd.read_csv('outputs/xjtu_features.csv').sort_values(['bearing_id','cycle'])
B = 20
rows = []; detail = {}
def first_run(mask, k):
    run = 0
    for i, m in enumerate(mask):
        run = run + 1 if m else 0
        if run >= k: return i  # index at which alarm confirms
    return None
for b, g in F.groupby('bearing_id', sort=False):
    g = g.reset_index(drop=True); n = len(g); rul = g.rul_minutes.to_numpy()
    out = {'bearing': b, 'life': n}
    # HI: per-channel rms, kurtosis
    for ch in ('h', 'v'):
        pass
    rms = np.maximum(g.h_rms.to_numpy()/np.median(g.h_rms[:B]), g.v_rms.to_numpy()/np.median(g.v_rms[:B]))
    # 3-sigma per channel with robust sigma from commissioning (MAD*1.4826, floor 2% of median)
    def z(col):
        x = g[col].to_numpy(); base = x[:B]; mu = np.median(base)
        sd = max(1.4826*np.median(np.abs(base-mu)), 0.02*abs(mu), 1e-12)
        return (x-mu)/sd
    zr = np.maximum(z('h_rms'), z('v_rms'))
    zk = np.maximum(z('h_kurtosis'), z('v_kurtosis'))
    # causal smoothing: trailing median of 5
    zr5 = pd.Series(zr).rolling(5, min_periods=1).median().to_numpy()
    r5 = pd.Series(rms).rolling(5, min_periods=1).median().to_numpy()
    zk5 = pd.Series(zk).rolling(5, min_periods=1).median().to_numpy()
    idx = np.arange(n); post = idx >= B
    def rec(name, mask, k):
        i = first_run(mask & post, k)
        out[name] = None if i is None else int(rul[i])
        # count how many confirmed-onset rows later have HI back in band (instability)
    rec('fpt_3s_k3', zr > 3, 3)
    rec('fpt_3s_k5', zr > 3, 5)
    rec('fpt_3s_med5_k5', zr5 > 3, 5)
    rec('fpt_6s_med5_k5', zr5 > 6, 5)
    rec('fpt_ratio1.3_med5_k5', r5 > 1.3, 5)
    rec('fpt_rms_or_kurt_3s_med5_k5', (zr5 > 3) | (zk5 > 3), 5)
    rec('fpt_ratio1.5_med5_k5', r5 > 1.5, 5)
    rec('crit_ratio2', r5 > 2.0, 3); rec('crit_ratio3', r5 > 3.0, 3); rec('crit_ratio5', r5 > 5.0, 3)
    # Offline GT 1: two-segment fit of log(rms ratio): constant before tau, linear after (hinge), least squares, full trajectory
    y = np.log(np.maximum(rms, 1e-6)); y_s = pd.Series(y).rolling(5, center=True, min_periods=1).median().to_numpy()
    best = (np.inf, None)
    for tau in range(5, n-3):
        t = np.clip(idx - tau, 0, None).astype(float)
        X = np.c_[np.ones(n), t]; coef, *_ = np.linalg.lstsq(X, y_s, rcond=None)
        sse = ((X@coef - y_s)**2).sum()
        if sse < best[0] and coef[1] > 0: best = (sse, tau)
    out['gt_hinge_rul'] = None if best[1] is None else int(rul[best[1]])
    # Offline GT 2: permanent exceedance of centred-median HI over robust whole-life healthy level
    # healthy level = median of lowest-HI 30% of life (non-causal, independent of the commissioning window)
    lvl = np.median(np.sort(rms)[:max(5, int(0.3*n))])
    hi_c = pd.Series(rms/lvl).rolling(5, center=True, min_periods=1).median().to_numpy()
    above = hi_c > 1.3
    # earliest t such that above holds for >=90% of all rows from t to end
    frac = np.cumsum(above[::-1])[::-1] / (n - idx)
    cand = np.where((frac >= 0.9) & above)[0]
    out['gt_perm_rul'] = int(rul[cand[0]]) if len(cand) else None
    out['commission_contaminated'] = bool(np.median(rms[:B]) > 1.15*np.median(np.sort(rms)[:max(5,int(0.3*n))]) )
    # in-band excursions (false FPT): number of 3s_med5_k5 'alarm episodes' that end before the final one
    m = (zr5 > 3) & post
    eps = np.diff(np.r_[0, m.astype(int), 0]); starts = np.where(eps == 1)[0]; ends = np.where(eps == -1)[0]
    long_eps = [(s, e) for s, e in zip(starts, ends) if e - s >= 5]
    out['n_episodes_3s_med5_k5'] = len(long_eps)
    rows.append(out)
R = pd.DataFrame(rows)
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
print(R.to_string(index=False))
R.to_csv('experiments/xjtu/v2/design_literature-fpt/fpt_check.csv', index=False)
