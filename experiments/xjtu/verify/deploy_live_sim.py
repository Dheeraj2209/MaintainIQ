"""Live-path simulation of the combined candidate for held-out bearings (deployability lens).

1. Train the fold models exactly as combined.py (et_reg s0 classifiers seed+fold, compact300 reg 42+fold).
2. Check they reproduce cached OOF (clf_et_reg_s0.npy / reg_compact300_s0.npy).
3. Write a temp artifact laid out per INTEGRATION_PLAN.md and drive snapshots one at a time through
   PlanPredictor = src RealTimeRULPredictor with the plan's section-3 edits (compact regressor input, latch).
4. Compare live raw prob / smoothed / warning(latch) / RUL to offline arrays; time per-snapshot latency.
"""
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, '.')
from experiments.xjtu.harness import load_context, load_raw_table  # noqa: E402
from experiments.xjtu import combined as C  # noqa: E402
from src.prediction import rul_realtime as RR  # noqa: E402
from src.training.xjtu_rul import classifier_feature_columns, add_past_context  # noqa: E402

H = 120.0
META = ('bearing_id', 'condition', 'cycle', 'elapsed_minutes', 'rul_minutes', 'source_file', 'speed_rpm', 'load_kn')
ctx = load_context()
raw = load_raw_table().sort_values(['bearing_id', 'cycle']).reset_index(drop=True)
assert (raw.bearing_id.values == ctx.bearing_id.values).all()
clf_cols = classifier_feature_columns(ctx)
COMP = ["m_rms", "h_peak", "v_peak", "m_envelope_rms", "h_kurtosis", "v_kurtosis"]
reg_inputs = [col for c in COMP for col in (c, f"{c}_baseline_ratio", *(f"{c}_mean_{w}" for w in (5, 20, 60)),
              *(f"{c}_trend_{w}" for w in (20, 60)))]
Xc = ctx[clf_cols]
Xr = C.compact_features(ctx)
y = ctx.rul_minutes.to_numpy(float)
truth = y <= H
oof_p = np.load('experiments/xjtu/cache/combined/clf_et_reg_s0.npy')
oof_r = np.load('experiments/xjtu/cache/combined/reg_compact300_s0.npy')
BEAR = sorted(ctx.bearing_id.unique())


class PlanPredictor(RR.RealTimeRULPredictor):
    """RealTimeRULPredictor + INTEGRATION_PLAN.md section 3 edits, nothing else."""

    def __init__(self, path, **kw):
        super().__init__(path, **kw)
        a = self.artifact
        self.regressor_feature_set = a.get("regressor_feature_set")
        if self.regressor_feature_set not in (None, "xjtu_compact_v1"):
            raise ValueError("unknown regressor_feature_set")
        self.regressor_input_columns = a.get("regressor_input_columns", self.regressor_feature_columns)
        self.warning_latch = bool(a.get("warning_latch", False))
        self._latched = defaultdict(bool)
        self.timing = []

    def reset_machine(self, m):
        super().reset_machine(m)
        self._latched.pop(m, None)

    def _predict_from_base(self, machine_id, base, *, sample_rate_hz, speed_rpm, load_kn):
        t0 = time.perf_counter()
        with self._locks[machine_id]:
            history = self._history[machine_id]
            cycle = self._cycles[machine_id]
            self._cycles[machine_id] += 1
            snapshot = {"bearing_id": machine_id, "cycle": cycle, "elapsed_minutes": float(cycle),
                        "speed_rpm": float(speed_rpm), "load_kn": float(load_kn), **base}
            history.append(snapshot)
            bh = self._baseline_history[machine_id]
            if len(bh) < int(self.artifact.get("baseline_window", 20)):
                bh.append(snapshot)
            context_rows = {row["cycle"]: row for row in [*bh, *history]}
            context = add_past_context(pd.DataFrame(context_rows.values()))
            latest = context.iloc[[-1]].copy()
        t1 = time.perf_counter()
        needed = set(self.classifier_feature_columns) | set(
            self.regressor_input_columns if self.regressor_feature_set else self.regressor_feature_columns)
        missing = needed.difference(latest.columns)
        if missing:
            raise ValueError(f"missing {sorted(missing)}")
        Xcl = latest[self.classifier_feature_columns]
        rawp = float(np.mean([c.predict_proba(Xcl)[0, 1] for c in self.classifiers]))
        t2 = time.perf_counter()
        with self._locks[machine_id]:
            ph = self._probability_history[machine_id]
            ph.append(rawp)
            fp = float(np.median(ph))
            thr = float(self.artifact["failure_probability_threshold"])
            wh = self._warning_history[machine_id]
            wh.append(fp >= thr)
            req = int(self.artifact.get("warning_persistence_snapshots", 1))
            within = len(wh) == req and all(wh)
            latched = False
            if self.warning_latch:
                if within:
                    self._latched[machine_id] = True
                latched = self._latched[machine_id]
                within = within or latched
        if self.regressor_feature_set == "xjtu_compact_v1":
            Xreg = C.compact_features(latest)[self.regressor_feature_columns]
        else:
            Xreg = latest[self.regressor_feature_columns]
        raw_reg = float(self.regressor.predict(Xreg)[0])  # every row, for parity checking
        pred = min(H, max(0.0, raw_reg)) if within else H
        t3 = time.perf_counter()
        self.timing.append((t1 - t0, t2 - t1, t3 - t2, t3 - t0))
        return dict(raw=rawp, smooth=fp, within=within, latched=latched,
                    reg=min(H, max(0.0, raw_reg)), pred=pred)


def base_of(r):
    return {c: r[c] for c in raw.columns if c not in META}


def run(b, n_jobs_pred):
    fold = BEAR.index(b) + 1
    te = np.flatnonzero(ctx.bearing_id.to_numpy() == b)
    tr = np.flatnonzero(ctx.bearing_id.to_numpy() != b)
    t0 = time.perf_counter()
    clfs = [C.et_reg_clf(s + fold, 3).fit(Xc.iloc[tr], truth[tr]) for s in (42, 200, 1000)]
    late = tr[truth[tr]]
    reg = C._compact_reg(42 + fold).fit(Xr.to_numpy(float)[late], y[late])
    fit_s = time.perf_counter() - t0
    p_off = np.mean([c.predict_proba(Xc.iloc[te])[:, 1] for c in clfs], axis=0)
    r_off = np.clip(reg.predict(Xr.to_numpy(float)[te]), 0, H)
    print(f"{b} fold {fold}: fit {fit_s:.0f}s; reproduce cached OOF prob maxdiff "
          f"{np.max(np.abs(p_off - oof_p[te])):.2e}, reg maxdiff {np.max(np.abs(r_off - oof_r[te])):.2e}", flush=True)
    for c in clfs:
        c.set_params(classifier__n_jobs=n_jobs_pred)
    reg.set_params(n_jobs=n_jobs_pred)
    art = {"classifiers": clfs, "regressor": reg, "classifier_feature_columns": clf_cols,
           "regressor_feature_columns": list(Xr.columns), "feature_columns": list(Xr.columns),
           "regressor_input_columns": reg_inputs, "regressor_feature_set": "xjtu_compact_v1",
           "warning_latch": True, "artifact_schema_version": 2,
           "conformal_error_90_minutes": 48.4, "model_version": "verify", "prognostic_horizon_minutes": H,
           "failure_probability_threshold": 0.6, "probability_smoothing_window": 3,
           "warning_persistence_snapshots": 3, "baseline_window": 20, "sample_rate_hz": 25600.0}
    path = Path(tempfile.mkdtemp()) / "art.joblib"
    joblib.dump(art, path)
    size_mb = path.stat().st_size / 1e6
    pr = PlanPredictor(path)
    rows = raw.iloc[te]
    # Unmodified src predictor with the new artifact: plan claims it fails loudly.
    try:
        old = RR.RealTimeRULPredictor(path)
        r0 = rows.iloc[0]
        old._predict_from_base("x", base_of(r0), sample_rate_hz=25600.0,
                               speed_rpm=r0.speed_rpm, load_kn=r0.load_kn)
        old_msg = "old predictor ACCEPTED new artifact"
    except Exception as e:  # noqa: BLE001
        old_msg = f"old predictor rejected: {type(e).__name__}: {str(e)[:100]}"
    out = []
    for _, r in rows.iterrows():
        out.append(pr._predict_from_base("m-" + b, base_of(r), sample_rate_hz=25600.0,
                                         speed_rpm=r.speed_rpm, load_kn=r.load_kn))
    L = pd.DataFrame(out)
    sm = pd.Series(oof_p[te]).rolling(3, min_periods=1).median().to_numpy()
    warn = (pd.Series(sm >= 0.6).astype(int).rolling(3, min_periods=3).sum() == 3).to_numpy()
    latch = np.maximum.accumulate(warn)
    live_rul_off = np.where(latch, oof_r[te], H)
    res = dict(bearing=b, n=len(te), artifact_mb=round(size_mb, 1), old_predictor=old_msg,
               raw_prob_maxdiff=float(np.max(np.abs(L.raw - oof_p[te]))),
               smooth_maxdiff=float(np.max(np.abs(L.smooth - sm))),
               warning_mismatches=int(np.sum(L.within.to_numpy() != latch)),
               reg_maxdiff=float(np.max(np.abs(L.reg - oof_r[te]))),
               live_rul_maxdiff=float(np.max(np.abs(L.pred - live_rul_off))),
               first_warning_index=int(np.argmax(L.within)) if L.within.any() else None,
               first_warning_true_rul=float(y[te][np.argmax(L.within)]) if L.within.any() else None,
               latched_rows_beyond_current_rule=int(np.sum(latch & ~warn)))
    T = np.array(pr.timing) * 1e3
    names = ["context", "classifier", "post+regressor", "total"]
    res["latency_ms_median"] = dict(zip(names, np.round(np.median(T, 0), 1).tolist()))
    res["latency_ms_p95"] = dict(zip(names, np.round(np.quantile(T, .95, 0), 1).tolist()))
    print(json.dumps(res, indent=1), flush=True)
    return res


if __name__ == "__main__":
    nj = int(sys.argv[1])
    bs = sys.argv[2:]
    allres = [run(b, nj) for b in bs]
    json.dump(allres, open(f'experiments/xjtu/verify/deploy_live_sim_nj{nj}.json', 'w'), indent=1)
