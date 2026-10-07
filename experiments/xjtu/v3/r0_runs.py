"""R0 = the production XJTU-SY architecture, retrained out-of-fold on the v3 folds (like-for-like reference).

Uses experiments/xjtu/harness.py unchanged (same factories, features, seeds, smoothing/threshold/
persistence as src/training/xjtu_rul.py). Writes per-row OOF files in the baseline_oof.csv format that the
frozen causal.states_r / harness2.reference_inputs consume:

  cache/r0_pooled_lobo_oof.csv   LOBO over all 32 bearings (XJTU + FEMTO), harness fold numbering
  cache/r0_femto_lobo_oof.csv    LOBO over the 17 FEMTO bearings only (inner-selection reference for FEMTO->XJTU)
  cache/r0_xjtu_to_femto.csv     fit on all 15 XJTU, predict the 17 FEMTO bearings (production final-model seeds)
  cache/r0_femto_to_xjtu.csv     fit on all 17 FEMTO, predict the 15 XJTU bearings
The XJTU-only LOBO reference is experiments/xjtu/results/baseline_oof.csv (v2 R0; reproduces models/ report).

    python experiments/xjtu/v3/r0_runs.py [--n-jobs 2] [--only pooled,femto,transfer]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
V3 = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from experiments.xjtu import harness as H  # noqa: E402
from src.training.xjtu_rul import (CLASSIFIER_ENSEMBLE_SEEDS, PROGNOSTIC_HORIZON_MINUTES,  # noqa: E402
                                   add_past_context, classifier_feature_columns, feature_columns)

CACHE = V3 / "cache"
NJ = int(sys.argv[sys.argv.index("--n-jobs") + 1]) if "--n-jobs" in sys.argv else 2
ONLY = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else ["pooled", "femto", "transfer"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def fit_apply(src: pd.DataFrame, tgt: pd.DataFrame) -> dict:
    """Production final-model recipe (train_and_export after LOBO): classifier seeds 42/200/1000, regressor
    seed 42, regressor trained on in-horizon rows, RUL clipped to [0, 120]; warning post-processing from
    harness.compute_report (smoothing 3, threshold, persistence)."""
    src = src.sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    tgt = tgt.sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    cf, rf = classifier_feature_columns(src), feature_columns(src)
    y = src.rul_minutes.astype(float).to_numpy(); inh = y <= PROGNOSTIC_HORIZON_MINUTES
    probs = np.mean([H.default_make_classifier(s, NJ).fit(src[cf], inh).predict_proba(tgt[cf])[:, 1]
                     for s in CLASSIFIER_ENSEMBLE_SEEDS], axis=0)
    reg = H.default_make_regressor(42, NJ).fit(src[rf].iloc[np.flatnonzero(inh)], y[inh])
    rul = np.clip(reg.predict(tgt[rf]), 0, PROGNOSTIC_HORIZON_MINUTES)
    return H.compute_report(tgt, tgt.rul_minutes.astype(float).to_numpy(), probs, rul)


def main():
    P = pd.read_csv(CACHE / "combined_features.csv")
    ctx = add_past_context(P)
    fem = ctx.bearing_id.str.startswith("FEMTO_")
    out = {}
    if "pooled" in ONLY:
        t = time.time()
        r = H.evaluate(ctx, n_jobs=NJ, verbose=True)
        H.save_oof(r, CACHE / "r0_pooled_lobo_oof.csv"); out["pooled"] = H.summary_metrics(r)
        log("pooled LOBO done", round(time.time() - t), "s")
    if "femto" in ONLY:
        t = time.time()
        r = H.evaluate(ctx[fem].reset_index(drop=True), n_jobs=NJ, verbose=True)
        H.save_oof(r, CACHE / "r0_femto_lobo_oof.csv"); out["femto_lobo"] = H.summary_metrics(r)
        log("FEMTO LOBO done", round(time.time() - t), "s")
    if "transfer" in ONLY:
        for name, s, tg in (("xjtu_to_femto", ~fem, fem), ("femto_to_xjtu", fem, ~fem)):
            t = time.time()
            r = fit_apply(ctx[s], ctx[tg])
            H.save_oof(r, CACHE / f"r0_{name}.csv"); out[name] = H.summary_metrics(r)
            log(name, "done", round(time.time() - t), "s")
    # sanity: XJTU-only LOBO reference must be the v2 R0 file
    prev = {}
    p = V3 / "results" / "r0_runs_summary.json"
    if p.exists():
        prev = json.loads(p.read_text())
    prev.update(out)
    p.write_text(json.dumps(prev, indent=1, default=float))
    log(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
