"""Load the frozen v2 evaluator + v2 stage-2 code on the pooled XJTU + FEMTO table, without editing any v2 file.

Nothing is re-implemented. The frozen modules are imported and only their DATA SOURCES are redirected
in memory (see DEVIATIONS.md V1-V4):
  * causal.load_features / causal.load_gt -> pooled 32-bearing feats and the v3 copy of the frozen GT rules
    (pooled_validate.pooled_gt, whose 15 XJTU rows equal the frozen onset_gt.csv exactly);
  * build_post-onset-rul.py is imported as module `bp` while pandas.read_csv redirects
    outputs/xjtu_features.csv -> cache/combined_features.csv (stage-2 extra channels) and
    experiments/xjtu/results/baseline_oof.csv -> an import-time placeholder (bp.R0F is re-set explicitly
    to the like-for-like R0 of each run before any selection; set_R0F);
  * causal.signflip_p: exact enumeration for n <= 20 (frozen code path), seeded Monte-Carlo sign-flip with
    2^20 draws for n > 20 (2^32 patterns are infeasible) -- V7;
  * harness2.bearing_flags: short_life = life <= 60 min (reproduces {2_4, 1_5} on XJTU, adds FEMTO_2_7) -- V9.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
V3 = Path(__file__).resolve().parent
V2 = ROOT / "experiments" / "xjtu" / "v2"
for p in (str(ROOT), str(V2), str(V3)):
    if p not in sys.path:
        sys.path.insert(0, p)

import causal as c  # noqa: E402  frozen
import harness2 as h  # noqa: E402
import pooled_validate as pv  # noqa: E402

CACHE = V3 / "cache"
COMBINED = CACHE / "combined_features.csv"
R0_FILES = dict(
    pooled=CACHE / "r0_pooled_lobo_oof.csv",
    xjtu_lobo=ROOT / "experiments/xjtu/results/baseline_oof.csv",
    femto_lobo=CACHE / "r0_femto_lobo_oof.csv",
    xjtu_to_femto=CACHE / "r0_xjtu_to_femto.csv",
    femto_to_xjtu=CACHE / "r0_femto_to_xjtu.csv",
)

FROZEN_OK_AT_LOAD = h.check_frozen()
assert all(FROZEN_OK_AT_LOAD.values()), FROZEN_OK_AT_LOAD

_P = pd.read_csv(COMBINED)
GT_FULL = pv.pooled_gt(_P)                       # includes no_onset
GT = GT_FULL.drop(columns="no_onset")
FEATS = pv.feats_from(_P)
_frozen_gt = pd.read_csv(V2 / "onset_gt.csv", dtype={"bearing": str}).set_index("bearing")
pd.testing.assert_frame_equal(GT.loc[_frozen_gt.index, _frozen_gt.columns], _frozen_gt, check_exact=True,
                              check_dtype=False)
del _P

c.load_features = lambda: FEATS
c.load_gt = lambda: GT.copy()

# ---- V7: Monte-Carlo sign-flip above n = 20 (exact path unchanged at n <= 20)
_signflip_exact = c.signflip_p
MC_SIGNFLIP_DRAWS, MC_SIGNFLIP_SEED = 2 ** 20, 20261006


def signflip_p(diff):
    d = np.asarray(diff, float)
    if len(d) <= 20:
        return _signflip_exact(d)
    rng = np.random.default_rng(MC_SIGNFLIP_SEED)
    a, obs, hit, done = np.abs(d), d.mean() + 1e-12, 0, 0
    while done < MC_SIGNFLIP_DRAWS:
        m = min(65536, MC_SIGNFLIP_DRAWS - done)
        s = rng.integers(0, 2, size=(m, len(d))) * 2.0 - 1.0
        hit += int(((s * a).mean(axis=1) <= obs).sum()); done += m
    return float((hit + 1) / (MC_SIGNFLIP_DRAWS + 1))


c.signflip_p = signflip_p


# ---- V9: short_life flag by life (reporting strata only)
def bearing_flags(gt):
    f = gt[["condition", "life_min", "baseline_contaminated", "short_healthy", "abrupt"]].copy()
    f["short_life"] = f.life_min <= 60
    return f


h.bearing_flags = bearing_flags


# ---- import bp with redirected inputs
def _placeholder_r0() -> Path:
    x = pd.read_csv(R0_FILES["xjtu_lobo"])
    rows = []
    for b, d in FEATS.items():
        if b.startswith("FEMTO_"):
            rows.append(pd.DataFrame(dict(bearing_id="FEMTO_Bearing" + b[6:], cycle=d.cycle, condition=d.condition,
                                          rul_minutes=d.rul_minutes, in_horizon=d.rul_minutes <= 120, raw_prob=0.0,
                                          smoothed_prob=0.0, warning=False, rul_pred=120.0)))
    p = CACHE / "_import_placeholder_r0.csv"
    pd.concat([x] + rows, ignore_index=True).to_csv(p, index=False)
    return p


def load_bp(n_jobs: int = 1):
    os.environ["OMP_NUM_THREADS"] = str(n_jobs)
    orig_rc, ph = pd.read_csv, _placeholder_r0()

    def rc(path, *a, **k):
        s = str(path).replace("\\", "/")
        if s.endswith("outputs/xjtu_features.csv"):
            path = COMBINED
        elif s.endswith("experiments/xjtu/results/baseline_oof.csv"):
            path = ph
        return orig_rc(path, *a, **k)

    argv = list(sys.argv)
    sys.argv = [argv[0], "--part", "explo"]          # bp reads --part at import; n_jobs is overridden below
    pd.read_csv = rc
    try:
        spec = importlib.util.spec_from_file_location("bp", V2 / "build_post-onset-rul.py")
        bp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bp)
    finally:
        pd.read_csv = orig_rc
        sys.argv = argv
    bp.NJOBS = n_jobs
    os.environ["OMP_NUM_THREADS"] = str(n_jobs)
    assert bp.FEATS is FEATS and len(bp.BS) == 32 and len(bp.FOLDS_DEF) == 32
    assert bp.c is c and bp.h is h
    bp.R0F = None                                     # must be set per run (set_R0F)
    return bp


def r0_states(key: str) -> dict:
    return c.states_r(pd.read_csv(R0_FILES[key]))


def set_R0F(bp, key: str, bearings) -> None:
    st = r0_states(key)
    bp.R0F = bp.cost_frame({b: st[b] for b in bearings}, list(bearings))


XJTU = sorted(b for b in FEATS if not b.startswith("FEMTO_"))
FEMTO = sorted(b for b in FEATS if b.startswith("FEMTO_"))
