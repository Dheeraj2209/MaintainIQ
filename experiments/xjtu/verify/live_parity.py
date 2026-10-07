"""Live-path parity check for the combined candidate (deployability lens).

Trains the fold models for held-out bearings exactly as combined.py does
(et_reg classifier x3 seeds s0 + seed offset fold; compact300 regressor 42+fold),
wraps them in an artifact the *unmodified* src RealTimeRULPredictor can load
(regressor = Pipeline(FunctionTransformer(compact), ETR) with
regressor_feature_columns = the 30 add_past_context inputs), then streams the
bearing one snapshot at a time through predictor._predict_from_base and
compares live features / probabilities / RUL with the offline full-history
values and the cached OOF arrays used for the reported metrics.
"""
import sys, time, json
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
import experiments.xjtu.combined as C
import src.prediction.rul_realtime as RT
from src.training.xjtu_rul import add_past_context as _apc
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer
from sklearn.ensemble import ExtraTreesRegressor
import joblib

OUT = Path(__file__).resolve().parent
SRC6 = ["m_rms", "h_peak", "v_peak", "m_envelope_rms", "h_kurtosis", "v_kurtosis"]
INPUTS = [col for c in SRC6 for col in (c, f"{c}_baseline_ratio", *(f"{c}_mean_{w}" for w in (5, 20, 60)), *(f"{c}_trend_{w}" for w in (20, 60)))]

def compact_np(X):
    return C.compact_features(X).to_numpy(float)

captured = []
def apc_capture(t):
    r = _apc(t)
    captured.append(r.iloc[-1].copy())
    return r
RT.add_past_context = apc_capture

raw = pd.read_csv(REPO / "outputs" / "xjtu_features.csv").sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
META = {"bearing_id", "condition", "cycle", "elapsed_minutes", "rul_minutes", "speed_rpm", "load_kn", "source_file"}
BASE_COLS = [c for c in raw.columns if c not in META]

clf_cols = C.BASE_CLF(C.TBL)
oof_p = np.load(C.CACHE / "clf_et_reg_s0.npy")
oof_r = np.load(C.CACHE / "reg_compact300_s0.npy")
compact_off = C.compact_features(C.TBL)

summary = {}
for bearing in sys.argv[1:]:
    fold = C.BEARINGS.index(bearing) + 1
    tr, te = C.FOLDS[fold - 1]
    assert set(C.TBL.bearing_id.iloc[te]) == {bearing}
    t0 = time.perf_counter()
    clfs = []
    for s in C.SEEDSETS["s0"]:
        m = C.et_reg_clf(s + fold); m.fit(C.TBL[clf_cols].iloc[tr], C.TRUTH[tr]); clfs.append(m)
    late = tr[C.TRUTH[tr]]
    etr = ExtraTreesRegressor(n_estimators=300, min_samples_leaf=300, max_features=0.5, random_state=42 + fold, n_jobs=3)
    etr.fit(compact_off.iloc[late].to_numpy(float), C.Y[late])
    reg = Pipeline([("compact", FunctionTransformer(compact_np)), ("regressor", etr)])
    fit_s = time.perf_counter() - t0
    Xc = C.TBL[clf_cols]
    bounds = {c: [float(Xc[c].iloc[tr].quantile(.005)), float(Xc[c].iloc[tr].quantile(.995))] for c in clf_cols}
    art = {"classifiers": clfs, "regressor": reg, "classifier_feature_columns": clf_cols,
           "regressor_feature_columns": INPUTS, "feature_columns": INPUTS, "feature_bounds_99pct": bounds,
           "conformal_error_90_minutes": 48.42, "model_version": "parity", "prognostic_horizon_minutes": 120.0,
           "failure_probability_threshold": 0.6, "probability_smoothing_window": 3,
           "warning_persistence_snapshots": 3, "baseline_window": 20, "sample_rate_hz": 25600.0,
           "health_thresholds_minutes": {"critical": 30.0, "faulty": 60.0, "degrading": 120.0}}
    ap = OUT / f"artifact_{bearing}.joblib"
    joblib.dump(art, ap)
    pred = RT.RealTimeRULPredictor(ap)
    rows = raw[raw.bearing_id == bearing].reset_index(drop=True)
    captured.clear()
    lat, results = [], []
    for _, r in rows.iterrows():
        base = {c: float(r[c]) for c in BASE_COLS}
        t1 = time.perf_counter()
        res = pred._predict_from_base("m1", base, sample_rate_hz=25600.0, speed_rpm=float(r.speed_rpm), load_kn=float(r.load_kn))
        lat.append(time.perf_counter() - t1)
        results.append(res)
    live = pd.DataFrame(captured).reset_index(drop=True)
    off = C.TBL.iloc[te].reset_index(drop=True)
    # feature diffs
    fcols = list(dict.fromkeys(clf_cols + INPUTS))
    d = (live[fcols].astype(float) - off[fcols].astype(float)).abs()
    rel = d / off[fcols].astype(float).abs().clip(lower=1e-9)
    worst = rel.max().sort_values(ascending=False).head(5)
    cl, co = C.compact_features(live), compact_off.iloc[te].reset_index(drop=True)
    dcomp = ((cl - co).abs() / co.abs().clip(lower=1e-9)).max().max()
    # predictions from live features (regressor evaluated on every row, not only when warned)
    live_p = np.mean([m.predict_proba(live[clf_cols])[:, 1] for m in clfs], axis=0)
    live_r = np.clip(reg.predict(live[INPUTS]), 0, 120)
    raw_live = np.array([x["raw_failure_within_horizon_probability"] for x in results])
    sm_live = np.array([x["failure_within_horizon_probability"] for x in results])
    warn_live = np.array([x["rul_estimate_kind"] == "point_estimate" for x in results])
    rul_live_pred = np.array([x["predicted_rul_minutes"] for x in results])
    # offline post-processing
    sm_off = pd.Series(oof_p[te]).rolling(3, min_periods=1).median().to_numpy()
    above = pd.Series(sm_off >= 0.6)
    warn_off = above.rolling(3, min_periods=3).sum().eq(3).to_numpy()
    latch_off = np.maximum.accumulate(warn_off)
    latch_live = np.maximum.accumulate(warn_live)
    yb = C.Y[te]
    fw = lambda w: (float(yb[np.argmax(w)]) if w.any() else None)
    summary[bearing] = {
        "fold": fold, "n": len(rows), "fit_seconds": round(fit_s, 1),
        "max_rel_feature_diff_clf_and_inputs": float(rel.max().max()),
        "worst_features": {k: float(v) for k, v in worst.items()},
        "max_rel_compact_diff": float(dcomp),
        "max_abs_rawprob_live_vs_oof": float(np.abs(raw_live - oof_p[te]).max()),
        "max_abs_rawprob_batch_live_feats_vs_oof": float(np.abs(live_p - oof_p[te]).max()),
        "max_abs_smoothed_live_vs_offline": float(np.abs(sm_live - sm_off).max()),
        "warning_mask_mismatches": int((warn_live != warn_off).sum()),
        "latched_mask_mismatches": int((latch_live != latch_off).sum()),
        "first_warning_rul_live": fw(warn_live), "first_warning_rul_offline": fw(warn_off),
        "max_abs_rul_live_feats_vs_oof": float(np.abs(live_r - oof_r[te]).max()),
        "max_abs_rul_reported_vs_oof_on_warned_rows": float(np.abs(rul_live_pred[warn_live] - oof_r[te][warn_live]).max()) if warn_live.any() else None,
        "latency_ms": {"p50": 1000 * float(np.median(lat)), "p95": 1000 * float(np.percentile(lat, 95)), "max": 1000 * float(np.max(lat))},
        "min_rul_pred_in_horizon_rows": float(oof_r[te][C.TRUTH[te]].min()) if C.TRUTH[te].any() else None,
    }
    print(json.dumps({bearing: summary[bearing]}, indent=1), flush=True)
json.dump(summary, open(OUT / f"live_parity_{'_'.join(sys.argv[1:])}.json", "w"), indent=1)
