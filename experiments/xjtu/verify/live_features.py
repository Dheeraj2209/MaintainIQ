"""Feature-level parity: live retained context (first-20 baseline + last max_history) vs offline full history.
Re-implements RealTimeRULPredictor's context construction exactly (dict-dedup by cycle, add_past_context)."""
import sys, time
from collections import deque
import numpy as np, pandas as pd
sys.path.insert(0, '.')
from experiments.xjtu.harness import load_context, load_raw_table
from experiments.xjtu.combined import compact_features
from src.training.xjtu_rul import add_past_context, classifier_feature_columns, BASELINE_WINDOW, ROLLING_WINDOWS

ctx = load_context()
raw = load_raw_table().sort_values(['bearing_id', 'cycle']).reset_index(drop=True)
clf_cols = classifier_feature_columns(ctx)
comp_off = compact_features(ctx)
inputs = sorted({c for c in ctx.columns if any(c == s or c.startswith(s + '_') for s in
          ["m_rms", "h_peak", "v_peak", "m_envelope_rms", "h_kurtosis", "v_kurtosis"])} )
META = ['bearing_id', 'condition', 'cycle', 'elapsed_minutes', 'rul_minutes', 'source_file']
max_history = max(ROLLING_WINDOWS)
bearings = sys.argv[1:] or sorted(raw.bearing_id.unique())
worst = {}
t_ctx = []
for b in bearings:
    rows = raw[raw.bearing_id == b]
    hist = deque(maxlen=max_history); basel = []
    off_idx = np.flatnonzero(ctx.bearing_id.to_numpy() == b)
    max_clf = max_comp = 0.0; worst_col = None
    for k, (_, r) in enumerate(rows.iterrows()):
        base = {c: r[c] for c in rows.columns if c not in META and c not in ('speed_rpm', 'load_kn')}
        snap = {'bearing_id': 'm', 'cycle': k, 'elapsed_minutes': float(k),
                'speed_rpm': float(r.speed_rpm), 'load_kn': float(r.load_kn), **base}
        hist.append(snap)
        if len(basel) < BASELINE_WINDOW:
            basel.append(snap)
        crow = {x['cycle']: x for x in [*basel, *hist]}
        t0 = time.perf_counter()
        live = add_past_context(pd.DataFrame(crow.values())).iloc[[-1]]
        t_ctx.append(time.perf_counter() - t0)
        off = ctx.iloc[[off_idx[k]]]
        a = live[clf_cols].to_numpy(float); o = off[clf_cols].to_numpy(float)
        d = np.abs(a - o) / np.maximum(np.abs(o), 1e-12)
        if np.nanmax(d) > max_clf:
            max_clf = float(np.nanmax(d)); worst_col = clf_cols[int(np.nanargmax(d))]
        lc = compact_features(live.reset_index(drop=True)).to_numpy(float)
        oc = comp_off.iloc[[off_idx[k]]].to_numpy(float)
        max_comp = max(max_comp, float(np.nanmax(np.abs(lc - oc) / np.maximum(np.abs(oc), 1e-12))))
    worst[b] = (len(rows), max_clf, worst_col, max_comp)
    print(b, worst[b], flush=True)
print('add_past_context per snapshot ms: median %.1f p95 %.1f' % (1e3*np.median(t_ctx), 1e3*np.quantile(t_ctx, .95)))
