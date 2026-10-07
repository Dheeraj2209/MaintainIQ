"""Per-snapshot latency breakdown: candidate fold artifact vs production artifact."""
import sys, time
from pathlib import Path
import numpy as np, pandas as pd, joblib
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO))
import __main__
from src.training.xjtu_rul import add_past_context
import experiments.xjtu.combined as C
def compact_np(X):
    return C.compact_features(X).to_numpy(float)
__main__.compact_np = compact_np  # pickled under __main__ by live_parity.py
raw = pd.read_csv(REPO / "outputs/xjtu_features.csv")
rows = raw[raw.bearing_id == "Bearing3_1"].sort_values("cycle").reset_index(drop=True)
ctx_raw = pd.concat([rows.iloc[:20], rows.iloc[1000:1060]])
def tm(f, n=30):
    ts = []
    for _ in range(n):
        t = time.perf_counter(); f(); ts.append(time.perf_counter() - t)
    return 1000 * np.median(ts)
ctx = add_past_context(ctx_raw); latest = ctx.iloc[[-1]]
print("add_past_context(80 rows) ms", round(tm(lambda: add_past_context(ctx_raw)), 1))
for name, path in [("candidate", REPO / "experiments/xjtu/verify/artifact_Bearing2_3.joblib"), ("production", REPO / "models/xjtu_rul_model.joblib")]:
    a = joblib.load(path)
    Xc = latest[a["classifier_feature_columns"]]; Xr = latest[a["regressor_feature_columns"]]
    for nj in (None, 1):
        for m in a["classifiers"]:
            if nj: m.steps[-1][1].set_params(n_jobs=1)
        if nj: a["regressor"].steps[-1][1].set_params(n_jobs=1)
        nj_desc = a["classifiers"][0].steps[-1][1].n_jobs
        pc = tm(lambda: [m.predict_proba(Xc) for m in a["classifiers"]])
        pr = tm(lambda: a["regressor"].predict(Xr))
        depth = np.mean([e.tree_.node_count for e in a["classifiers"][0].steps[-1][1].estimators_])
        print(f"{name} n_jobs={nj_desc}: 3x predict_proba {pc:.1f} ms, regressor {pr:.1f} ms, mean clf nodes/tree {depth:.0f}, size MB {path.stat().st_size/1e6:.1f}")
