"""Evaluation harness for the frozen v2 protocol (experiments/xjtu/v2/PROTOCOL.frozen.md).

Everything the protocol pre-registers as *evaluator* (indicators, state-machine replay, cost,
bootstrap, sign-flip, influence, latency floors, reference-state mapping) is imported from the
frozen ``causal.py`` and is NOT re-implemented here. This module adds the pieces the protocol
describes but ``causal.py`` does not contain:

- onset GT: recomputed in memory with the frozen ``onset_gt.py`` code and checked bit-for-bit
  against the frozen ``onset_gt.csv`` (``compute_gt`` / ``verify_gt``);
- LOBO folds (outer + inner, the order of experiments/xjtu/harness.py) and a generic LOBO loop;
- bearing-equal weighted quantiles and bearing-grouped conformal bounds (global / Mondrian with
  the 5-calibration-bearing rule, one-sided CQR) -- section 5 item 4/5;
- the pipeline state composition of section 2.2 (faulty/critical conditions, model_crit with the
  q10 5-bearing rule, peak severity only with q50 <= 60), including a fixed-point replay for the
  hysteresis candidate, whose stage-2 inputs depend on the onset row (section 2.4/2.5);
- every reporting metric of section 3 (O1-O4, F1-F5, P1-P4, 3.6 legacy, p0 calibration);
- eligibility E1-E3, the paired 1-SE tie rule with the lexicographic tie-breaks (4.2/4.3), the
  superiority gate G and product gates P1-P6 (4.4);
- the candidate grids (24 stage-1, 672 downstream) with complexity keys, D, D';
- ``compare()`` (tables beside the 4.5 dummy rows, paired differences with CI and exact p);
- reference scoring of R0 / R1 into ``results/reference_*.json``;
- self-checks (``python experiments/xjtu/v2/harness2.py --selfcheck``).

Run from the repo root:
    python experiments/xjtu/v2/harness2.py              # self-checks, then score R0/R1
    python experiments/xjtu/v2/harness2.py --selfcheck  # self-checks only
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from scipy.stats import beta as beta_dist
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score

V2 = Path(__file__).resolve().parent
if str(V2) not in sys.path:
    sys.path.insert(0, str(V2))
import causal as c  # noqa: E402  (frozen evaluator)
import onset_gt as ogt  # noqa: E402  (frozen GT code; only main() with a patched OUT is used)

ROOT = c.ROOT
RESULTS = V2 / "results"
HORIZON = 120.0
GATE_VARIANTS = ("env", "hf", "twophase", "v2x")          # G(c) robustness set (1.1.6)
REPORT_VARIANTS = ("hinge", "on")                          # reported only
FROZEN_SHA256 = {                                          # PROTOCOL header
    "causal.py": "d40b8b1144afa2dd1c4e51ea908395a9cff3763bc020c8016fc65e612239e9a7",
    "run_dummies.py": "e3eb56c33ced537b37a96855553ff222ce00e930d268a11e1da556e907422210",
    "onset_gt.csv": "649ae1060e2f2dec005507ab52ae63f843148a810ce10e21494e7ed83153d143",
}
DUMMIES = ("never", "fire_at_eligibility", "fire_at_eligibility_critical", "onset_eq_critical", "onset_only")
STAGE1_DUMMIES = ("never", "fire_at_eligibility")
R0_CAVEAT = ("R0 OOF for an inner bearing came from a model trained with the outer held-out bearing "
             "included; R0 is a fixed, untuned comparator (PROTOCOL 3.2).")


# ============================================================================ frozen artefacts
def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_frozen() -> dict[str, bool]:
    return {f: sha256(V2 / f) == h for f, h in FROZEN_SHA256.items()}


# ============================================================================ onset GT (section 1)
def compute_gt() -> pd.DataFrame:
    """Recompute the GT in memory by running the frozen onset_gt.main() with its output redirected
    to a temporary file (the frozen onset_gt.csv is never written)."""
    old = ogt.OUT
    with tempfile.TemporaryDirectory() as td:
        ogt.OUT = Path(td) / "onset_gt.csv"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ogt.main()
            out = pd.read_csv(ogt.OUT, dtype={"bearing": str}).set_index("bearing")
        finally:
            ogt.OUT = old
    return out


def verify_gt(gt: pd.DataFrame | None = None) -> bool:
    fresh = compute_gt()
    frozen = c.load_gt() if gt is None else gt
    try:
        pd.testing.assert_frame_equal(fresh, frozen, check_exact=True)
        return True
    except AssertionError:
        return False


def bearing_flags(gt: pd.DataFrame) -> pd.DataFrame:
    f = gt[["condition", "life_min", "baseline_contaminated", "short_healthy", "abrupt"]].copy()
    f["short_life"] = f.index.isin(["2_4", "1_5"])
    return f


# ============================================================================ folds and LOBO
def outer_folds(bearings) -> list[tuple[int, str, list[str]]]:
    """LeaveOneGroupOut over sorted bearing ids (same order as experiments/xjtu/harness.py)."""
    bs = sorted(bearings)
    return [(i, b, [x for x in bs if x != b]) for i, b in enumerate(bs)]


def inner_folds(train) -> list[tuple[int, str, list[str]]]:
    return outer_folds(train)


def lobo(bearings, fit_predict: Callable[[list[str], str, int], object]) -> dict[str, object]:
    """Generic LOBO loop: fit_predict(train_bearings, held_out, fold) -> per-held-out output."""
    return {b: fit_predict(tr, b, i) for i, b, tr in outer_folds(bearings)}


# ============================================================================ weighted quantiles / conformal
def bearing_equal_weights(groups: np.ndarray) -> np.ndarray:
    g = pd.Series(np.asarray(groups))
    return (1.0 / g.map(g.value_counts())).to_numpy(float)


def wquantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Weighted quantile, inverse of the weighted ECDF: smallest v with F(v) >= q."""
    v = np.asarray(values, float); w = np.asarray(weights, float)
    o = np.argsort(v, kind="mergesort"); v, w = v[o], w[o]
    cw = np.cumsum(w) / w.sum()
    return float(v[min(np.searchsorted(cw, q - 1e-12), len(v) - 1)])


Q50_BINS = (0.0, 30.0, 60.0, 120.0, np.inf)               # Mondrian bins on predicted q50 (item 5)
MIN_BIN_BEARINGS = 5


def q50_bin(q50: np.ndarray) -> np.ndarray:
    return np.digitize(np.asarray(q50, float), Q50_BINS[1:-1], right=True)   # 0..3; 0-30 incl. 30


@dataclass
class Conformal:
    """Bearing-grouped conformal quantile of a score, global or Mondrian by predicted q50.

    fit(scores, groups, q50_calib): calibration rows of the OTHER bearings only.
    quantile(level, q50_test) -> (value per test row, usable mask). A Mondrian bin with residuals
    from fewer than MIN_BIN_BEARINGS distinct bearings falls back to the global quantile and is
    marked not usable (the q10 rule of 2.2 then uses q50 for that row)."""
    binning: str = "global"
    scores: np.ndarray = field(default=None, repr=False)
    groups: np.ndarray = field(default=None, repr=False)
    bins: np.ndarray = field(default=None, repr=False)

    def fit(self, scores, groups, q50_calib=None):
        self.scores = np.asarray(scores, float); self.groups = np.asarray(groups)
        self.bins = q50_bin(q50_calib) if self.binning == "mondrian" else np.zeros(len(self.scores), int)
        return self

    def _q(self, mask, level):
        w = bearing_equal_weights(self.groups[mask])
        return wquantile(self.scores[mask], w, level), len(np.unique(self.groups[mask]))

    def quantile(self, level, q50_test):
        n = len(q50_test)
        gq, gnb = self._q(np.ones(len(self.scores), bool), level)
        val = np.full(n, gq); ok = np.full(n, gnb >= MIN_BIN_BEARINGS)
        if self.binning == "mondrian":
            tb = q50_bin(q50_test)
            for k in range(len(Q50_BINS) - 1):
                m = self.bins == k
                if not m.any():
                    continue
                q, nb = self._q(m, level)
                if nb >= MIN_BIN_BEARINGS:
                    val[tb == k] = q; ok[tb == k] = True
                else:
                    ok[tb == k] = False
        return val, ok


def conformal_point(pred_cal, y_cal, g_cal, pred_test, alpha, binning="global"):
    """TSO / ET bounds in log1p space: q_lo = expm1(log1p(pred) + Q_alpha(e)), q_hi with Q_{1-alpha}."""
    e = np.log1p(np.asarray(y_cal, float)) - np.log1p(np.asarray(pred_cal, float))
    cf = Conformal(binning).fit(e, g_cal, pred_cal)
    lo, ok_lo = cf.quantile(alpha, pred_test)
    hi, ok_hi = cf.quantile(1 - alpha, pred_test)
    lp = np.log1p(np.asarray(pred_test, float))
    return _order(np.expm1(lp + lo), pred_test, np.expm1(lp + hi)) + (ok_lo & ok_hi,)


def conformal_cqr(lo_cal, hi_cal, y_cal, g_cal, q50_cal, lo_test, hi_test, q50_test, alpha, binning="global"):
    """HGB-q one-sided CQR (log1p space inputs lo/hi/y): q_lo = expm1(lo - Q_{1-a}(lo - y)),
    q_hi = expm1(hi + Q_{1-a}(y - hi)). q50 inputs (minutes) only define Mondrian bins."""
    y = np.log1p(np.asarray(y_cal, float))
    clo = Conformal(binning).fit(np.asarray(lo_cal) - y, g_cal, q50_cal)
    chi = Conformal(binning).fit(y - np.asarray(hi_cal), g_cal, q50_cal)
    a, ok_a = clo.quantile(1 - alpha, q50_test)
    b, ok_b = chi.quantile(1 - alpha, q50_test)
    return _order(np.expm1(np.asarray(lo_test) - a), q50_test, np.expm1(np.asarray(hi_test) + b)) + (ok_a & ok_b,)


def _order(lo, mid, hi):
    s = np.sort(np.clip(np.vstack([lo, mid, hi]).astype(float), 0, None), axis=0)
    return s[0], s[1], s[2]


def prob_within(h: float, levels, values) -> np.ndarray:
    """P(RUL <= h) from conformalized quantiles: linear in the quantile level between knots,
    linearly extrapolated beyond the outer knots, clipped to [0, 1] (section 2.3).
    levels: sequence of L quantile levels; values: array (L, n) of quantile values per row."""
    lv = np.asarray(levels, float); V = np.sort(np.asarray(values, float), axis=0)
    out = np.empty(V.shape[1])
    for j in range(V.shape[1]):
        v = V[:, j]
        if h <= v[0]:
            s = (lv[1] - lv[0]) / max(v[1] - v[0], 1e-9); p = lv[0] - (v[0] - h) * s
        elif h >= v[-1]:
            s = (lv[-1] - lv[-2]) / max(v[-1] - v[-2], 1e-9); p = lv[-1] + (h - v[-1]) * s
        else:
            p = np.interp(h, v, lv)
        out[j] = p
    return np.clip(out, 0, 1)


# ============================================================================ grids (section 5)
STAGE1_FAMILIES = (
    [("ratio", dict(G=G, k=k)) for G, k in product((1.3, 1.2), (3, 5))]
    + [("dual", dict(S=S, G=G, k=k)) for S, G, k in product((3, 4), (1.3, 1.2), (3, 5))]
)
REFS = ("self", "fallback")


def stage1_grid() -> list[dict]:
    """24 configs; complexity key = (family, config-in-listing-order, reference) (items 1-2)."""
    out = []
    for fi, (fam, prm) in enumerate(STAGE1_FAMILIES):
        for ri, ref in enumerate(REFS):
            out.append(dict(family=fam, ref=ref, **prm,
                            complexity=(0 if fam == "ratio" else 1, fi, ri)))
    for i, o in enumerate(out):
        o["grid_index"] = i
        o["name"] = stage1_name(o)
    return sorted(out, key=lambda o: o["complexity"])


def stage1_name(cfg) -> str:
    s = f"ratio{cfg['G']}/k{cfg['k']}" if cfg["family"] == "ratio" else f"dual z{cfg['S']}&r{cfg['G']}/k{cfg['k']}"
    return f"{s}/{cfg['ref']}"


def stage1_trigger(cfg: dict, ind: dict) -> np.ndarray:
    if cfg["family"] == "ratio":
        return ind["r5"] >= cfg["G"]
    return (ind["z5"] > cfg["S"]) & (ind["r5"] > cfg["G"])


STAGE2 = ([("TSO", {})]
          + [("ET", dict(min_samples_leaf=l, max_features=mf)) for l, mf in product((10, 20), (0.3, 0.5))]
          + [("HGBq", dict(min_samples_leaf=l)) for l in (20, 40)])
HYST = ("latch", "calm")
BINNING = ("global", "mondrian")
MODEL_CRIT = (("q50", None), ("q10", 0.1), ("q10", 0.2))
S1_GRID = (3.0, 2.0)
S2_GRID = (None, 8.0, 6.0, 4.0)


def downstream_grid() -> list[dict]:
    """672 combos in complexity order (items 3-8, lexicographic); grid_index = enumeration order."""
    out = []
    for gi, (h, s2i, bi, mci, s1i, s2vi) in enumerate(product(range(2), range(7), range(2), range(3), range(2), range(4))):
        fam, prm = STAGE2[s2i]
        out.append(dict(hysteresis=HYST[h], stage2=fam, stage2_params=prm, stage2_index=s2i,
                        binning=BINNING[bi], model_crit=MODEL_CRIT[mci], S1=S1_GRID[s1i], S2=S2_GRID[s2vi],
                        complexity=(h, s2i, bi, mci, s1i, s2vi), grid_index=gi))
    return out


D_STAGE1 = dict(family="ratio", G=1.3, k=3, ref="self")
DP_STAGE1 = dict(family="ratio", G=1.2, k=5, ref="self")
D_DOWNSTREAM = dict(hysteresis="latch", stage2="TSO", binning="global", model_crit=("q50", None), S1=2.0, S2=None)


# ============================================================================ pipeline states (2.2-2.5)
def episode_start(st: np.ndarray) -> np.ndarray:
    """Row of the onset of the current non-healthy episode (-1 while healthy) = fpt per row."""
    nh = np.asarray(st) >= 1
    idx = np.arange(len(nh))
    starts = nh & ~np.r_[False, nh[:-1]]
    s = np.maximum.accumulate(np.where(starts, idx, -1))
    return np.where(nh, s, -1)


def crit_from_model(pred: dict, model_crit) -> np.ndarray:
    kind, alpha = model_crit
    q50 = pred["q50"]
    if kind == "q50":
        return q50 <= 30
    lo, ok = pred[f"lo_{alpha}"], pred[f"lo_ok_{alpha}"]
    return np.where(ok, lo <= 30, q50 <= 30)


def pipeline_states(trigger, k, ind, predict_fn: Callable[[np.ndarray], dict], model_crit=("q50", None),
                    S1=2.0, S2=None, hysteresis="latch", m=c.M_ESC, pre_onset="provisional",
                    max_iter=100) -> dict:
    """Full state machine for one bearing.

    predict_fn(fpt_row) -> dict of per-row arrays (NaN where fpt_row < 0, i.e. O = 0; the healthy
    state has no RUL, section 2.3): 'q50' and, when model_crit uses q10, 'lo_<alpha>' and
    'lo_ok_<alpha>' (the 5-bearing usability mask). Stage-2 features depend on the onset row, and
    under the 'calm' hysteresis a clear is only allowed while max_state_reached == degrading, so the
    onset rows depend on the escalation path. Solved by fixed-point iteration: start from the
    stage-1-only path, predict, replay, and repeat until the per-row fpt is unchanged (escalation can
    only suppress clears that lie after the escalation, so earlier rows never change and this
    terminates).

    pre_onset: how the stage-2 components of the faulty/critical conditions are evaluated while
    O = 0. PROTOCOL 2.2 has the three counters run concurrently from row 20 so that O, faulty and
    critical can fire in the same snapshot (and the latency floors in 1.3 assume this), while 2.3
    publishes no RUL while O = 0. 'provisional' (default) evaluates stage 2 with a provisional onset
    at the current row (tso = 0) for the conditions only; the published q50 ('q50_published') stays
    null while healthy. 'none' leaves stage-2 conditions false until O = 1 (earliest stage-2 critical
    is then onset + m - 1)."""
    r5, p5 = ind["r5"], ind["p5"]
    hyst = hysteresis == "calm"
    st = c.replay(trigger, k, hysteresis=hyst, r5=r5)
    for it in range(max_iter):
        fpt = episode_start(st)
        pred = predict_fn(np.where(fpt >= 0, fpt, np.arange(len(fpt))) if pre_onset == "provisional" else fpt)
        q50 = np.asarray(pred["q50"], float)
        sev_f = (r5 >= S1) | ((p5 >= S2) if S2 is not None else False)
        rul_f = q50 <= 60
        crit_rul = crit_from_model(pred, model_crit)
        crit_sev = ((p5 >= S2) & (q50 <= 60)) if S2 is not None else np.zeros(len(r5), bool)
        st_new = c.replay(trigger, k, rul_f | sev_f, crit_rul | crit_sev, m=m, hysteresis=hyst, r5=r5)
        if np.array_equal(episode_start(st_new), fpt):
            break
        st = st_new
    else:
        raise RuntimeError("pipeline_states did not converge")
    reason = entry_reasons(st_new, rul_f, crit_rul)
    pub = np.where(st_new >= 1, q50, np.nan)
    return dict(states=st_new, pred=pred, q50_published=pub, fpt=episode_start(st_new), iterations=it + 1, **reason)


def entry_reasons(st, rul_faulty, crit_rul) -> dict:
    """reason_code at the first faulty-or-above and first critical entry (2.2): 'rul' if the
    stage-2 component is true at the entry row, else 'severity'."""
    ci = np.where(st >= 3)[0]; fi = np.where(st >= 2)[0]
    rc = None if not len(ci) else ("rul" if crit_rul[ci[0]] else "severity")
    if not len(fi):
        rf = None
    elif st[fi[0]] >= 3:
        rf = rc
    else:
        rf = "rul" if rul_faulty[fi[0]] else "severity"
    return dict(crit_reason=rc, faulty_reason=rf)


def tso_predict_fn(L: float) -> Callable[[np.ndarray], dict]:
    def f(fpt):
        idx = np.arange(len(fpt), dtype=float)
        return {"q50": np.where(fpt >= 0, np.maximum(0.0, L - (idx - fpt)), np.nan)}
    return f


def causal_fpt(d: pd.DataFrame, cfg: dict = D_STAGE1) -> int:
    """First onset row of a stage-1 config under the hard latch (-1 if never)."""
    ind = c.indicators(d, cfg["ref"])
    st = c.replay(stage1_trigger(cfg, ind), cfg["k"])
    nz = np.where(st >= 1)[0]
    return int(nz[0]) if len(nz) else -1


# ============================================================================ metrics (section 3)
def demotion_stats(st: np.ndarray) -> dict:
    """F5 / E3. A demotion is any drop in the displayed state except the allowed
    degrading -> healthy clear (2.4). Fraction over non-healthy rows."""
    st = np.asarray(st); prev = np.r_[0, st[:-1]]
    drop = st < prev
    allowed = (prev == 1) & (st == 0)
    dem = drop & ~allowed
    within = dem & (st >= 1)
    nh = max(int((st >= 1).sum()), 1)
    return dict(demotions=int(dem.sum()), demotions_within_episode=int(within.sum()),
                escalated_to_healthy=int((dem & (st == 0)).sum()), demotion_fraction=float(dem.sum() / nh))


def clopper_pearson(k: int, n: int, conf=0.95) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    a = (1 - conf) / 2
    lo = 0.0 if k == 0 else float(beta_dist.ppf(a, k, n - k + 1))
    hi = 1.0 if k == n else float(beta_dist.ppf(1 - a, k + 1, n - k))
    return lo, hi


def p1_baselines(feats, gt) -> dict[str, dict]:
    """P1 comparators fit inside each outer fold (3.5): constant = bearing-equal median true RUL of
    the training bearings' post-onset rows, TSO = max(0, L - tso) with L = median post-onset life of
    the training bearings. Onset = D's causal stage 1 (ratio 1.3/k3/self/latch); rows before the
    held-out bearing's causal onset get tso = 0 (prediction L)."""
    fpt = {b: causal_fpt(d) for b, d in feats.items()}
    out = {}
    for _, b, tr in outer_folds(feats):
        vals, grp, lives = [], [], []
        for t in tr:
            if fpt[t] < 0:
                continue
            r = feats[t].rul_minutes.to_numpy(float)[fpt[t]:]
            vals.append(r); grp.append(np.full(len(r), t)); lives.append(r[0])
        v = np.concatenate(vals); g = np.concatenate(grp)
        const = wquantile(v, bearing_equal_weights(g), 0.5)
        L = float(np.median(lives))
        n = len(feats[b]); idx = np.arange(n)
        tso = np.maximum(0, idx - fpt[b]) if fpt[b] >= 0 else np.zeros(n)
        out[b] = dict(const=np.full(n, const), tso=np.maximum(0.0, L - tso), L=L, const_value=const, fpt=fpt[b])
    return out


def evaluate(name: str, states: dict, feats: dict, gt: pd.DataFrame, *, k: int | None = None,
             rul_pred: dict | None = None, q_lo: dict | None = None, q_hi: dict | None = None,
             prob: dict | None = None, reasons: dict | None = None, p0: dict | None = None,
             baselines: dict | None = None, interval_note: str = "") -> dict:
    """All protocol metrics for one model given its per-bearing state arrays (outer predictions).

    k: stage-1 persistence (eligibility row 20+k-1 for O1); None for models without a stage-1 k.
    rul_pred/q_lo/q_hi: per-bearing per-row arrays (NaN where not published).
    prob: failure_within_horizon_probability per row (3.6). reasons: {b: {crit_reason, faulty_reason}}.
    p0: {b: p0 value used while O = 0} (2.3 calibration)."""
    bs = sorted(states)
    S = {w: c.score(states, feats, gt, w_ep=w) for w in c.W_EP_SENS}
    P = S[c.W_EP_PRIMARY].copy()
    for w in c.W_EP_SENS:
        P[f"C_w{int(w)}"] = S[w].C
    for v in GATE_VARIANTS + REPORT_VARIANTS:
        sv = c.score(states, feats, gt, variant=v)
        P[f"C_{v}"] = sv.C; P[f"FD_{v}"] = sv.FD; P[f"FDh_{v}"] = sv.FDh; P[f"LD_{v}"] = sv.LD
        P[f"delay_{v}"] = sv.delay
    flags = bearing_flags(gt)
    elig = None if k is None else c.COMMISSION + k - 1
    extra = []
    for b in bs:
        st = np.asarray(states[b]); d = feats[b]; rul = d.rul_minutes.to_numpy(float); n = len(st)
        te, tl = c.gt_variant_rows(gt, b, n, "primary")
        row = {"bearing": b}
        # O1 delay against max(t_late, eligible_row(k))
        dl = P.at[b, "det_lead"]
        tdet = None if np.isnan(dl) else int(n - 1 - dl)
        ref_late = tl if elig is None else max(tl, elig)
        row["t_det_row"] = tdet
        row["O1_delay_min"] = (np.nan if tdet is None else
                               0 if te <= tdet <= ref_late else (tdet - ref_late if tdet > ref_late else tdet - te))
        # O2 pre-onset hours (uncapped) and against t_on
        row["pre_onset_hours"] = te / 60.0
        row["FDh_uncapped"] = float((st[:te] >= 1).sum()) / 60.0
        # F1/F2 entries and reasons
        rs = (reasons or {}).get(b, {})
        row["crit_bin"] = c.entry_bin(P.at[b, "crit_lead"]); row["faulty_bin"] = c.entry_bin(P.at[b, "faulty_lead"])
        row["crit_reason"] = rs.get("crit_reason", "rul" if not np.isnan(P.at[b, "crit_lead"]) else None)
        row["faulty_reason"] = rs.get("faulty_reason", "rul" if not np.isnan(P.at[b, "faulty_lead"]) else None)
        # F4 uncapped hours
        row["ECh_uncapped"] = float(((st >= 3) & (rul > 60)).sum()) / 60.0
        row["EFh_uncapped"] = float(((st >= 2) & (rul > 120)).sum()) / 60.0
        row.update(demotion_stats(st))
        # first onset row (for P2/P4)
        on = np.where(st >= 1)[0]
        fpt = int(on[0]) if len(on) else -1
        row["fpt_row"] = fpt
        if rul_pred is not None:
            rp = np.asarray(rul_pred[b], float)
            own = st >= 1
            common = np.arange(n) >= tl
            row["P1_mae_own"] = _mae(rp, rul, own)
            row["P1_mae_own_le60"] = _mae(rp, rul, own & (rul <= 60))
            row["P1_mae_common"] = _mae(rp, rul, common)
            row["P1_mae_common_le60"] = _mae(rp, rul, common & (rul <= 60))
            if baselines is not None:
                for key in ("const", "tso"):
                    bp = baselines[b][key]
                    row[f"P1_{key}_mae_own"] = _mae(bp, rul, own)
                    row[f"P1_{key}_mae_common"] = _mae(bp, rul, common)
                    row[f"P1_{key}_mae_common_le60"] = _mae(bp, rul, common & (rul <= 60))
            le30 = (rul <= 30) & ~np.isnan(rp)
            row["P3_late_rate"] = float((rp[le30] > rul[le30]).mean()) if le30.any() else np.nan
            row["n_rul_le30"] = int(le30.sum())
            row["q50_at_onset"] = float(rp[fpt]) if fpt >= 0 else np.nan
        if q_lo is not None:
            lo = np.asarray(q_lo[b], float); hi = np.asarray(q_hi[b], float) if q_hi is not None else None
            row["q10_at_onset"] = float(lo[fpt]) if fpt >= 0 else np.nan
            row["P2a_cover_at_fpt"] = (np.nan if fpt < 0 or np.isnan(lo[fpt]) else int(rul[fpt] >= lo[fpt]))
            post60 = np.where((st >= 1) & (rul <= 60))[0]
            j = int(post60[0]) if len(post60) else -1
            row["P2b_cover_first_le60"] = (np.nan if j < 0 or np.isnan(lo[j]) else int(rul[j] >= lo[j]))
            own = (st >= 1) & ~np.isnan(lo)
            if hi is not None and own.any():
                row["P2_row_coverage"] = float(((rul >= lo) & (rul <= hi))[own].mean())
                row["P2_row_lower_coverage"] = float((rul >= lo)[own].mean())
                row["P2_mean_width"] = float((hi - lo)[own].mean())
        if p0 is not None and b in p0:
            o0 = (st == 0) & (np.arange(n) >= c.COMMISSION)
            y = (rul <= HORIZON)[o0].astype(float)
            row["p0"] = p0[b]; row["p0_obs_frac"] = float(y.mean()) if o0.any() else np.nan
            row["p0_brier"] = float(((p0[b] - y) ** 2).mean()) if o0.any() else np.nan
        extra.append(row)
    E = pd.DataFrame(extra).set_index("bearing")
    per = P.join(E).join(flags)
    out = dict(model=name, per_bearing=per, summary=summarize(per, gt), k=k)
    if prob is not None:
        out["legacy_120"] = legacy_metrics(states, feats, prob)
    if q_lo is not None:
        out["coverage"] = coverage_summary(per, interval_note)
    if rul_pred is not None:
        out["rul"] = rul_summary(per)
    return out


def _mae(p, y, mask):
    m = np.asarray(mask, bool) & ~np.isnan(p)
    return float(np.abs(p[m] - y[m]).mean()) if m.any() else np.nan


def summarize(per: pd.DataFrame, gt: pd.DataFrame) -> dict:
    s = {}
    for w in c.W_EP_SENS:
        col = f"C_w{int(w)}"
        s[col] = float(per[col].mean()); s[col + "_ci95"] = c.boot_ci(per[col].to_numpy())
    for col in ("C1", "LC", "ECh", "FDh", "LD", "EFh"):
        s[col + "_mean"] = float(per[col].mean()); s[col + "_ci95"] = c.boot_ci(per[col].to_numpy())
    s["LC_sum"] = float(per.LC.sum())
    for v in GATE_VARIANTS + REPORT_VARIANTS:
        s[f"C_{v}"] = float(per[f"C_{v}"].mean())
    s["C_drop_3_1_3_2"] = float(per.C.drop(["3_1", "3_2"], errors="ignore").mean())
    s["C_unflagged8"] = float(per.C[~per.baseline_contaminated].mean())
    s["C_flagged7"] = float(per.C[per.baseline_contaminated].mean())
    # F3
    s["MC_events"] = int(per.MC.sum()); s["MC_bearings"] = sorted(per.index[per.MC == 1])
    s["EP_events"] = int(per.EP.sum())
    s["EP_bearings"] = [f"{b}@{int(per.at[b, 'crit_lead'])}" for b in per.index[per.EP == 1]]
    # F1/F2 bins with names and reason codes
    def _entry(b, kind):
        lead = per.at[b, kind + "_lead"]
        return b if np.isnan(lead) else f"{b}@{int(lead)}({per.at[b, kind + '_reason']})"
    for kind in ("crit", "faulty"):
        s[f"F_{kind}_entry"] = {bn: [_entry(b, kind) for b in per.index[per[kind + "_bin"] == bn]]
                                for bn in ("never", "<10 (miss)", "10-60 (useful)", ">60 (early page)")}
    # F4 uncapped, by reason
    s["F4_ECh_uncapped_total"] = float(per.ECh_uncapped.sum())
    s["F4_EFh_uncapped_total"] = float(per.EFh_uncapped.sum())
    s["F4_ECh_by_reason"] = per.groupby(per.crit_reason.fillna("none")).ECh_uncapped.sum().to_dict()
    s["F4_EFh_by_reason"] = per.groupby(per.faulty_reason.fillna("none")).EFh_uncapped.sum().to_dict()
    # F5
    s["F5_demotion_fraction_mean"] = float(per.demotion_fraction.mean())
    s["F5_demotions_total"] = int(per.demotions.sum())
    s["F5_bearings_with_demotion"] = sorted(per.index[per.demotions > 0])
    # O1
    dl = per.O1_delay_min
    s["O1_delay_min"] = dl.round(1).to_dict()
    s["O1_n_abs_delay_le15"] = int((dl.abs() <= 15).sum())
    s["O1_delay_median"] = float(dl.median()) if dl.notna().any() else np.nan
    # O2
    pre_h = per.pre_onset_hours.sum()
    s["O2_FD_episodes"] = int(per.FD.sum()); s["O2_FD_bearings"] = sorted(per.index[per.FD > 0])
    s["O2_FD_per_1000_pre_onset_h"] = float(per.FD.sum() / pre_h * 1000) if pre_h else np.nan
    s["O2_FDh_uncapped_total"] = float(per.FDh_uncapped.sum())
    s["O2_nonhealthy_h_per_1000_pre_onset_h"] = float(per.FDh_uncapped.sum() / pre_h * 1000) if pre_h else np.nan
    s["O2_vs_t_on"] = dict(FD=int(per.FD_on.sum()), FDh_capped_mean=float(per.FDh_on.mean()),
                           bearings=sorted(per.index[per.FD_on > 0]))
    # O3
    sc = per[per.MO_scorable == 1]
    s["O3_missed_onsets"] = int(sc.MO.sum()); s["O3_missed_bearings"] = sorted(sc.index[sc.MO == 1])
    s["O3_structurally_undetectable"] = sorted(per.index[per.MO_scorable == 0])
    # O4
    lead = per.det_lead
    s["O4_lead_median"] = float(lead.median()) if lead.notna().any() else np.nan
    s["O4_lead_min"] = float(lead.min()) if lead.notna().any() else np.nan
    s["O4_n_lead_ge30"] = int((lead >= 30).sum())
    if "p0" in per:
        s["p0_brier_bearing_mean"] = float(per.p0_brier.mean())
        s["p0_calibration"] = per[["p0", "p0_obs_frac"]].round(4).to_dict("index")
    return s


def legacy_metrics(states, feats, prob) -> dict:
    """3.6 (comparison only): AP/ROC-AUC of the probability vs RUL <= 120 and P/R/F1/false alarms
    for alarm = state >= faulty and alarm = state >= degrading."""
    y = np.concatenate([feats[b].rul_minutes.to_numpy(float) <= HORIZON for b in sorted(states)])
    p = np.concatenate([np.asarray(prob[b], float) for b in sorted(states)])
    st = np.concatenate([np.asarray(states[b]) for b in sorted(states)])
    within_auc, within_ap = [], []
    for b in sorted(states):
        yb = feats[b].rul_minutes.to_numpy(float) <= HORIZON
        if 0 < yb.sum() < len(yb):
            within_auc.append(roc_auc_score(yb, prob[b])); within_ap.append(average_precision_score(yb, prob[b]))
    out = dict(ap_pooled=float(average_precision_score(y, p)), auc_pooled=float(roc_auc_score(y, p)),
               auc_within_bearing_mean=float(np.mean(within_auc)), ap_within_bearing_mean=float(np.mean(within_ap)),
               n_bearings_with_both_classes=len(within_auc))
    for lvl, nm in ((2, "faulty"), (1, "degrading")):
        a = st >= lvl
        out[f"alarm_ge_{nm}"] = dict(precision=float(precision_score(y, a, zero_division=0)),
                                     recall=float(recall_score(y, a, zero_division=0)),
                                     f1=float(f1_score(y, a, zero_division=0)),
                                     false_alarm_rows=int((a & ~y).sum()), missed_rows=int((~a & y).sum()))
    return out


def coverage_summary(per: pd.DataFrame, note: str = "", nominal: float = 0.90) -> dict:
    out = {"note": note, "nominal": nominal}
    for col in ("P2a_cover_at_fpt", "P2b_cover_first_le60"):
        v = per[col].dropna()
        k, n = int(v.sum()), int(len(v))
        lo, hi = clopper_pearson(k, n)
        out[col] = dict(covered=k, n=n, rate=(k / n if n else np.nan), cp95=(lo, hi),
                        nominal_inside_cp=(bool(lo <= nominal <= hi) if n else None),
                        failed=sorted(v.index[v == 0]))
    if "P2_row_coverage" in per:
        out["row_coverage_bearing_mean"] = float(per.P2_row_coverage.mean())
        out["row_lower_coverage_bearing_mean"] = float(per.P2_row_lower_coverage.mean())
        out["mean_width_bearing_mean"] = float(per.P2_mean_width.mean())
    out["P4_q10_q50_at_onset"] = per[["fpt_row", "q10_at_onset", "q50_at_onset"]].round(2).to_dict("index")
    return out


def rul_summary(per: pd.DataFrame) -> dict:
    cols = [x for x in per.columns if x.startswith("P1_")]
    out = {x: float(per[x].mean()) for x in cols}
    out["P3_late_rate_bearing_mean"] = float(per.P3_late_rate.mean())
    out["note"] = ("P1 bearing-averaged MAE (minutes); own = rows the model shows non-healthy, common = rows "
                   "at/after the GT late edge; const/tso comparators fit per outer fold on D's causal onset.")
    return out


# ============================================================================ paired stats and gates (4.4)
def per_bearing_cost(ev: dict, col: str = "C") -> pd.Series:
    return ev["per_bearing"][col]


def paired(evX: dict, evY: dict) -> dict:
    """Paired differences X - Y on the outer bearings: mean, bootstrap CI, exact sign-flip p,
    influence, and every G(c) sensitivity."""
    PX, PY = evX["per_bearing"], evY["per_bearing"]
    d = (PX.C - PY.C)
    out = dict(X=evX["model"], Y=evY["model"], mean=float(d.mean()), ci95=c.boot_ci(d.to_numpy()),
               signflip_p=c.signflip_p(d.to_numpy()), per_bearing=d.round(4).to_dict())
    out["influence"] = c.influence_signs(d)
    out["w3"] = float((PX.C_w3 - PY.C_w3).mean()); out["w10"] = float((PX.C_w10 - PY.C_w10).mean())
    for v in GATE_VARIANTS + REPORT_VARIANTS:
        out[f"gt_{v}"] = float((PX[f"C_{v}"] - PY[f"C_{v}"]).mean())
    unf = ~PX.baseline_contaminated
    out["unflagged8"] = float(d[unf].mean()); out["flagged7"] = float(d[~unf].mean())
    for col, sel in (("abrupt", PX.abrupt), ("short_healthy", PX.short_healthy), ("short_life", PX.short_life)):
        out[f"{col}_true"] = float(d[sel].mean()) if sel.any() else np.nan
        out[f"{col}_false"] = float(d[~sel].mean()) if (~sel).any() else np.nan
    out["by_condition"] = d.groupby(PX.condition).mean().to_dict()
    return out


def gate_G(pr: dict) -> dict:
    """Superiority gate G(X better than Y) from a paired() result."""
    a = pr["ci95"][1] < 0
    b = pr["signflip_p"] < 0.05
    cc = {"w3": pr["w3"] < 0, "w10": pr["w10"] < 0}
    cc.update({f"gt_{v}": pr[f"gt_{v}"] < 0 for v in GATE_VARIANTS})
    cc.update({f"loo{kk}": v < 0 for kk, v in pr["influence"].items() if kk != "full"})
    cc["unflagged8"] = pr["unflagged8"] < 0
    return dict(G_a=a, G_b=b, G_c=all(cc.values()), G_c_items=cc,
                G_c_failed=[kk for kk, v in cc.items() if not v], passed=bool(a and b and all(cc.values())))


def eligibility(cand: pd.DataFrame, r0: pd.DataFrame, dummies: dict[str, pd.DataFrame], bearings=None,
                stage: int = 2) -> dict:
    """E1-E3 on the given bearings. cand/r0/dummies: per-bearing frames with C_w3/C_w5/C_w10, C1,
    MC, EP (and 'demotions' for E3)."""
    bs = list(bearings) if bearings is not None else list(cand.index)
    X = cand.loc[bs]
    if stage == 1:
        e2 = {nm: bool(X.C1.mean() < dummies[nm].loc[bs].C1.mean()) for nm in STAGE1_DUMMIES}
        return dict(E1=True, E2=all(e2.values()), E2_items=e2, E3=True, eligible=all(e2.values()))
    R = r0.loc[bs]
    e1 = (X.EP.sum() - R.EP.sum()) <= (R.MC.sum() - X.MC.sum())
    e2 = {f"{nm}@w{int(w)}": bool(X[f"C_w{int(w)}"].mean() < dummies[nm].loc[bs][f"C_w{int(w)}"].mean())
          for nm in DUMMIES for w in c.W_EP_SENS}
    e3 = int(X["demotions"].sum()) == 0 if "demotions" in X else True
    return dict(E1=bool(e1), E2=all(e2.values()), E2_failed=[kk for kk, v in e2.items() if not v],
                E3=bool(e3), eligible=bool(e1 and all(e2.values()) and e3))


def select(cands: list[dict], metric: str = "C", fallback: dict | None = None) -> dict:
    """Paired 1-SE selection (4.3 step 3) among eligible candidates.

    cands: dicts with 'per_bearing' (frame with metric, EP, ECh), 'eligible' (bool), 'complexity'
    (tuple), 'grid_index', and optionally 'S2' (None/8/6/4). Tie set: mean(d) <= SE(d) of the paired
    difference to the best; then complexity, fewer EP, smaller capped ECh, more conservative S2
    (off > 8 > 6 > 4), lower grid index. Returns the choice and diagnostics; if none is eligible,
    returns fallback (D) with fell_back=True."""
    el = [x for x in cands if x["eligible"]]
    if not el:
        return dict(choice=fallback, fell_back=True, n_eligible=0, tie_size=0)
    means = np.array([x["per_bearing"][metric].mean() for x in el])
    best = el[int(np.argmin(means))]
    ties = []
    for x in el:
        d = (x["per_bearing"][metric] - best["per_bearing"][metric]).to_numpy()
        if d.mean() <= c.paired_se(d) + 1e-12:
            ties.append(x)

    def s2rank(x):
        s2 = x.get("S2")
        return 0.0 if s2 is None else 1.0 + (10.0 - float(s2))   # off first, then 8, 6, 4

    def key(x):
        pb = x["per_bearing"]
        ep = float(pb["EP"].sum()) if "EP" in pb else 0.0
        ec = float(pb["ECh"].sum()) if "ECh" in pb else 0.0
        return (tuple(x["complexity"]), ep, ec, s2rank(x), x["grid_index"])

    ch = min(ties, key=key)
    return dict(choice=ch, fell_back=False, n_eligible=len(el), tie_size=len(ties),
                best_mean=float(means.min()), choice_mean=float(ch["per_bearing"][metric].mean()))


def gates_P(evX: dict, evR0: dict, evD: dict, ev_dummies: dict[str, dict]) -> dict:
    PX = evX["per_bearing"]
    g = gate_G(paired(evX, evR0))
    el = eligibility(PX, evR0["per_bearing"], {k: v["per_bearing"] for k, v in ev_dummies.items()})
    res = dict(P1=g["passed"], P1_detail=g,
               P2=bool(PX.EP.sum() <= evR0["per_bearing"].EP.sum()),
               P3=bool(PX.MC.sum() <= evR0["per_bearing"].MC.sum()),
               P4=bool(el["E1"] and el["E2"]), P4_detail=el,
               P5=bool(evX["summary"]["O3_missed_onsets"] <= evD["summary"]["O3_missed_onsets"]),
               P6=bool(PX.demotions.sum() == 0))
    res["passed"] = all(res[f"P{i}"] for i in range(1, 7))
    res["failed"] = [f"P{i}" for i in range(1, 7) if not res[f"P{i}"]]
    return res


# ============================================================================ compare()
TABLE_COLS = ("C_w5", "C_w3", "C_w10", "C1", "LC_sum", "MC", "EP", "ECh", "FD", "FDh", "LD", "EFh", "MO",
              "C_env", "C_hf", "C_twophase", "C_v2x", "C_drop")


def table_row(ev: dict) -> dict:
    P = ev["per_bearing"]
    return dict(model=ev["model"], C_w5=P.C_w5.mean(), C_w3=P.C_w3.mean(), C_w10=P.C_w10.mean(), C1=P.C1.mean(),
                LC_sum=P.LC.sum(), MC=int(P.MC.sum()), EP=int(P.EP.sum()), ECh=P.ECh.mean(), FD=int(P.FD.sum()),
                FDh=P.FDh.mean(), LD=P.LD.mean(), EFh=P.EFh.mean(), MO=int(P.MO.sum()),
                C_env=P.C_env.mean(), C_hf=P.C_hf.mean(), C_twophase=P.C_twophase.mean(), C_v2x=P.C_v2x.mean(),
                C_drop=P.C.drop(["3_1", "3_2"], errors="ignore").mean(),
                C_ci95=tuple(round(x, 3) for x in c.boot_ci(P.C_w5.to_numpy())))


def compare(evs: dict[str, dict], pairs=(), dummies: dict[str, dict] | None = None, show=True) -> dict:
    """Primary table (with the 4.5 dummy rows) and paired differences with bootstrap CI and exact
    sign-flip p. evs/dummies: name -> evaluate() output; pairs: (X, Y) names."""
    allev = {**(dummies or {}), **evs}
    T = pd.DataFrame([table_row(e) for e in allev.values()]).set_index("model")
    prs = {f"{x}-{y}": paired(allev[x], allev[y]) for x, y in pairs}
    if show:
        with pd.option_context("display.width", 250, "display.max_columns", 40):
            print(T.round(3).to_string())
        for k, p in prs.items():
            print(f"{k:28s} mean {p['mean']:+.3f}  CI95 [{p['ci95'][0]:+.3f}, {p['ci95'][1]:+.3f}]  "
                  f"sign-flip p {p['signflip_p']:.4f}  w3 {p['w3']:+.3f}  w10 {p['w10']:+.3f}  "
                  f"unflagged8 {p['unflagged8']:+.3f}")
    return dict(table=T, paired=prs)


# ============================================================================ references (3.2)
def reference_inputs(path: Path) -> dict:
    """R0/R1 OOF mapped into the protocol semantics. States via the frozen causal.states_r (healthy
    unless warning; critical <= 30, faulty <= 60, degrading <= 120 on rul_pred; no ratchet).
    Published RUL = rul_pred (3.5). Interval = the band the served model publishes while warning:
    [max(0, pred - e90), min(120, pred + e90)], e90 = 0.9 'higher' quantile of |pred - true| on the
    OOF rows with RUL <= 120 (how xjtu_rul computes conformal_error_90; pooled, not bearing-grouped).
    Probability = smoothed_prob (the served failure_within_horizon_probability)."""
    oof = pd.read_csv(path)
    states = c.states_r(oof)
    oof["b"] = oof.bearing_id.str.replace("Bearing", "", regex=False)
    oof = oof.sort_values(["b", "cycle"])
    late = oof.rul_minutes <= HORIZON
    e90 = float(np.quantile(np.abs(oof.rul_minutes[late] - oof.rul_pred[late]), 0.90, method="higher"))
    rp, lo, hi, pr = {}, {}, {}, {}
    for b, d in oof.groupby("b"):
        p = d.rul_pred.to_numpy(float); w = d.warning.to_numpy(bool)
        rp[b] = p; pr[b] = d.smoothed_prob.to_numpy(float)
        lo[b] = np.where(w, np.maximum(0, p - e90), np.nan); hi[b] = np.where(w, np.minimum(HORIZON, p + e90), np.nan)
    return dict(states=states, rul_pred=rp, q_lo=lo, q_hi=hi, prob=pr, e90=e90)


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return jsonable(o.reset_index().to_dict("records"))
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


def score_references(feats, gt, show=True) -> dict:
    RESULTS.mkdir(parents=True, exist_ok=True)
    base = p1_baselines(feats, gt)
    ev_d = {nm: evaluate(nm, st, feats, gt, k=3) for nm, st in c.dummy_states(feats, k=3).items()}
    evs, warm = {}, {}
    for nm, fn in (("R0", "baseline_oof.csv"), ("R1", "combined_oof.csv")):
        inp = reference_inputs(ROOT / "experiments/xjtu/results" / fn)
        note = (f"served symmetric band pred +/- e90 (e90 = {inp['e90']:.2f} min, pooled OOF, nominal 90% "
                "two-sided); lower edge ~ a 5% bound, not a bearing-grouped q10")
        evs[nm] = evaluate(nm, inp["states"], feats, gt, k=None, rul_pred=inp["rul_pred"], q_lo=inp["q_lo"],
                           q_hi=inp["q_hi"], prob=inp["prob"], baselines=base, interval_note=note)
        evs[nm]["e90"] = inp["e90"]
        # sensitivity: PROTOCOL 2.1/3.1 say warm-up rows are healthy for every model; the frozen
        # 4.5 mapping (causal.states_r) does not gate them, and the served model does not either.
        gated = {b: np.where(np.arange(len(s)) < c.COMMISSION, 0, s) for b, s in inp["states"].items()}
        warm[nm] = evaluate(nm + "_warmup_gated", gated, feats, gt, k=None)
        warm[nm]["warmup_rows_nonhealthy"] = {b: int((s[:c.COMMISSION] >= 1).sum())
                                             for b, s in inp["states"].items() if (s[:c.COMMISSION] >= 1).any()}
    cmp = compare(evs, pairs=(("R1", "R0"),) + tuple((r, dn) for r in ("R0", "R1") for dn in DUMMIES),
                  dummies=ev_d, show=show)
    for nm in ("R0", "R1"):
        el = eligibility(evs[nm]["per_bearing"], evs["R0"]["per_bearing"], {k: v["per_bearing"] for k, v in ev_d.items()})
        doc = dict(
            model=nm, protocol="experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2)", frozen_hashes_ok=check_frozen(),
            source=("experiments/xjtu/results/baseline_oof.csv (experiments/xjtu/harness.py LOBO, reproduces "
                    "models/xjtu_rul_evaluation.json)" if nm == "R0" else
                    "experiments/xjtu/results/combined_oof.csv (experiments/xjtu/combined.py, fully nested rule)"),
            mapping="causal.states_r: healthy unless warning; critical<=30, faulty<=60, degrading<=120 on rul_pred; no ratchet",
            caveats=[R0_CAVEAT] if nm == "R0" else [
                "R1 cannot express critical in practice: min rul_pred = %.2f > 30 (PROTOCOL 3.2); context only, "
                "never a gate comparator." % float(min(np.nanmin(v) for v in reference_inputs(
                    ROOT / "experiments/xjtu/results/combined_oof.csv")["rul_pred"].values()))],
            summary=evs[nm]["summary"], legacy_120_comparison_only=evs[nm]["legacy_120"],
            rul_comparison_only=evs[nm]["rul"], coverage_descriptive=evs[nm]["coverage"],
            eligibility_vs_R0_and_dummies=el,
            paired_vs_dummies={dn: cmp["paired"][f"{nm}-{dn}"] for dn in DUMMIES},
            warmup_gated_sensitivity=dict(summary=warm[nm]["summary"],
                                          warmup_rows_nonhealthy=warm[nm]["warmup_rows_nonhealthy"],
                                          C_w5=float(warm[nm]["per_bearing"].C_w5.mean())),
            per_bearing=evs[nm]["per_bearing"],
        )
        if nm == "R1":
            doc["paired_R1_minus_R0"] = cmp["paired"]["R1-R0"]
            doc["G_R1_better_than_R0"] = gate_G(cmp["paired"]["R1-R0"])
        (RESULTS / f"reference_{nm}.json").write_text(json.dumps(jsonable(doc), indent=2), encoding="utf-8")
        evs[nm]["per_bearing"].to_csv(RESULTS / f"reference_{nm}_per_bearing.csv")
    dsum = {k: dict(summary=v["summary"], per_bearing=v["per_bearing"]) for k, v in ev_d.items()}
    (RESULTS / "reference_dummies.json").write_text(json.dumps(jsonable(dsum), indent=2), encoding="utf-8")
    cmp["table"].to_csv(RESULTS / "reference_table.csv")
    return dict(evs=evs, dummies=ev_d, compare=cmp, warm=warm)


# ============================================================================ self-checks
def selfcheck(feats=None, gt=None, verbose=True) -> dict:
    feats = feats or c.load_features(); gt = c.load_gt() if gt is None else gt
    res = {}

    def ok(name, cond, detail=""):
        res[name] = bool(cond)
        if verbose:
            print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")

    fr = check_frozen()
    ok("frozen_hashes", all(fr.values()), str(fr))
    ok("gt_recompute_equals_frozen", verify_gt(gt))
    bs = sorted(feats)
    ok("15_bearings_9216_rows", len(bs) == 15 and sum(len(d) for d in feats.values()) == 9216)
    ok("outer_fold_order", [b for _, b, _ in outer_folds(bs)] == bs and all(len(t) == 14 for *_, t in outer_folds(bs)))
    life = {b: len(feats[b]) for b in bs}
    scor = (gt.gt_late_rul >= c.MO_MIN_GT_LATE_RUL)
    ok("MO_excludes_1_4_2_4_1_5", sorted(gt.index[~scor]) == ["1_4", "1_5", "2_4"])

    # --- degenerate detector 1: fires (critical) at snapshot 0, raw state array -------------------
    fire0 = {b: np.full(life[b], 3) for b in bs}
    e = evaluate("fire_at_0", fire0, feats, gt)["per_bearing"]
    rul0 = {b: feats[b].rul_minutes.iloc[0] for b in bs}
    exp_ep = sum(1 for b in bs if rul0[b] > 60)
    ok("fire0_LC0_MC0", e.LC.sum() == 0 and e.MC.sum() == 0)
    ok("fire0_EP_equals_bearings_with_RUL0_gt60", e.EP.sum() == exp_ep == 13, f"EP={e.EP.sum()}")
    ok("fire0_FD_all15_LD0_MO0", e.FD.sum() == 15 and e.LD.sum() == 0 and e.MO.sum() == 0)
    exp_fdh = np.array([min(gt.at[b, "gt_early_row"] / 60, 8) for b in bs])
    ok("fire0_FDh", np.allclose(e.FDh.to_numpy(), exp_fdh))
    exp_ech = np.array([min((feats[b].rul_minutes > 60).sum() / 60, 4) for b in bs])
    ok("fire0_ECh", np.allclose(e.ECh.to_numpy(), exp_ech))
    ok("fire0_O1_delay_negative_or_zero", (e.O1_delay_min <= 0).all())
    # --- degenerate detector 2: never fires ---------------------------------------------------------
    ev_never = evaluate("never", {b: np.zeros(life[b], int) for b in bs}, feats, gt)
    e = ev_never["per_bearing"]
    exp_ld = np.array([min((life[b] - gt.at[b, "gt_late_row"]) / 60, 4) for b in bs])
    ok("never_LC1_MC15_EP0", e.LC.sum() == 15 and e.MC.sum() == 15 and e.EP.sum() == 0)
    ok("never_FD0_FDh0_EFh0", e.FD.sum() == 0 and e.FDh.sum() == 0 and e.EFh.sum() == 0)
    ok("never_LD", np.allclose(e.LD.to_numpy(), exp_ld))
    ok("never_MO_12", e.MO.sum() == 12 and ev_never["summary"]["O3_missed_onsets"] == 12)
    ok("never_C_w_invariant", np.allclose(e.C_w3, e.C_w10))
    ok("never_C1", np.allclose(e.C1.to_numpy(), 10 * e.MO.to_numpy() + exp_ld))
    # --- replay through the state machine: trigger always true, k=3 => onset at row 22 -------------
    for k, row in ((3, 22), (5, 24)):
        st = c.replay(np.ones(100, bool), k)
        ok(f"replay_onset_row_k{k}", np.argmax(st >= 1) == row and (st[:row] == 0).all())
    st = c.replay(np.ones(100, bool), 3, np.ones(100, bool), np.ones(100, bool))
    ok("concurrent_counters_healthy_to_critical_row22", st[21] == 0 and st[22] == 3)
    st = c.replay(np.r_[np.zeros(30, bool), np.ones(70, bool)], 3, None, np.r_[np.zeros(20, bool), np.ones(80, bool)])
    ok("crit_counter_runs_before_onset", np.argmax(st >= 1) == 32 and st[32] == 3)
    # --- reproduce the frozen 4.5 numbers -----------------------------------------------------------
    frozen = pd.read_csv(V2 / "results/dummies/summary.csv").set_index("model")
    refs = {"R0": c.states_r(pd.read_csv(ROOT / "experiments/xjtu/results/baseline_oof.csv")),
            "R1": c.states_r(pd.read_csv(ROOT / "experiments/xjtu/results/combined_oof.csv")),
            **c.dummy_states(feats, k=3)}
    bad = []
    for nm, stt in refs.items():
        r = table_row(evaluate(nm, stt, feats, gt))
        f = frozen.loc[nm]
        for a, b_ in (("C_w5", "C"), ("C_w3", "C_wEP3"), ("C_w10", "C_wEP10"), ("C1", "C1"), ("LC_sum", "LC_sum"),
                      ("MC", "MC"), ("EP", "EP"), ("ECh", "ECh"), ("FD", "FD"), ("FDh", "FDh"), ("LD", "LD"),
                      ("EFh", "EFh"), ("MO", "MO"), ("C_env", "C_env"), ("C_hf", "C_hf"),
                      ("C_twophase", "C_twophase"), ("C_v2x", "C_v2x"), ("C_drop", "C_drop31_32")):
            if not np.isclose(r[a], f[b_], rtol=0, atol=1e-9):
                bad.append(f"{nm}.{a}={r[a]} vs {f[b_]}")
    ok("reproduces_frozen_4.5_table", not bad, "; ".join(bad[:5]))
    # --- statistics -----------------------------------------------------------------------------------
    d = np.array([-1.0, -2.0, 0.5])
    brute = np.mean([np.mean(np.array(s) * np.abs(d)) <= d.mean() + 1e-12 for s in product([1, -1], repeat=3)])
    ok("signflip_matches_bruteforce_n3", np.isclose(c.signflip_p(d), brute))
    ok("signflip_all_negative_n15", np.isclose(c.signflip_p(-np.arange(1, 16.0)), 1 / 2 ** 15))
    ok("boot_ci_deterministic", c.boot_ci(np.arange(15.0)) == c.boot_ci(np.arange(15.0)))
    ok("clopper_pearson_edges", clopper_pearson(0, 10)[0] == 0 and clopper_pearson(10, 10)[1] == 1
       and np.isclose(clopper_pearson(10, 10)[0], 0.025 ** (1 / 10)))
    # --- weighted quantile / conformal 5-bearing rule ----------------------------------------------
    v = np.r_[np.zeros(100), np.ones(1)]; g = np.array(["a"] * 100 + ["b"])
    ok("bearing_equal_weighted_median", wquantile(v, bearing_equal_weights(g), 0.5) == 0.0
       and wquantile(v, bearing_equal_weights(g), 0.51) == 1.0)
    rng = np.random.default_rng(0)
    gg = np.repeat([f"b{i}" for i in range(6)], 50); sc = rng.normal(size=300)
    q50c = np.r_[np.full(200, 10.0), np.full(100, 200.0)]                # bin 0-30 has 4 bearings only
    cf = Conformal("mondrian").fit(sc, gg, q50c)
    val, okm = cf.quantile(0.1, np.array([10.0, 200.0]))
    gval, _ = Conformal("global").fit(sc, gg).quantile(0.1, np.array([10.0]))
    ok("mondrian_bin_lt5_bearings_falls_back_to_global", (not okm[0]) and val[0] == gval[0] and not okm[1])
    lo, mid, hi, okc = conformal_point(np.full(300, 50.0), np.full(300, 50.0), gg, np.array([40.0]), 0.1)
    ok("conformal_point_zero_residual", np.isclose(lo[0], 40) and np.isclose(hi[0], 40) and okc[0])
    pw = prob_within(120, (0.05, 0.1, 0.5, 0.9, 0.95), np.array([[10, 400], [20, 500], [60, 600], [100, 700], [110, 800]]))
    ok("prob_within_monotone_clipped", np.isclose(pw[0], 1.0) and pw[1] == 0.0
       and np.isclose(prob_within(60, (0.05, 0.1, 0.5, 0.9, 0.95), np.array([[10], [20], [60], [100], [110]]))[0], 0.5))
    # --- selection rule ----------------------------------------------------------------------------
    idx = [f"x{i}" for i in range(14)]
    base_c = np.linspace(1, 3, 14)
    mk = lambda cst, cx, gi, ep=0, s2=None, el=True: dict(  # noqa: E731
        per_bearing=pd.DataFrame({"C": cst, "EP": ep, "ECh": 0.0}, index=idx), complexity=cx, grid_index=gi,
        S2=s2, eligible=el)
    noise = np.tile([0.3, -0.3], 7)                       # paired diff mean 0.02, SE ~0.08 -> tie
    cands = [mk(base_c + 0.02 + noise, (1,), 0), mk(base_c, (2,), 1), mk(base_c + 5 + noise, (0,), 2),
             mk(base_c - 9, (0,), 3, el=False), mk(base_c + 0.02, (0,), 4)]   # constant +0.02: SE 0 -> no tie
    s = select(cands)
    ok("select_paired_1se_prefers_simpler_tie", s["choice"]["grid_index"] == 0 and s["tie_size"] == 2
       and s["n_eligible"] == 4, f"tie={s['tie_size']} choice={s['choice']['grid_index']}")
    s = select([mk(base_c, (0,), 7, ep=1), mk(base_c, (0,), 8, ep=0)])
    ok("select_tiebreak_fewer_EP", s["choice"]["grid_index"] == 8)
    s = select([mk(base_c, (0,), 5, s2=4.0), mk(base_c, (0,), 6, s2=None)])
    ok("select_tiebreak_conservative_S2", s["choice"]["grid_index"] == 6)
    s = select([mk(base_c, (0,), 0, el=False)], fallback={"name": "D"})
    ok("select_fallback_to_D", s["fell_back"] and s["choice"]["name"] == "D")
    ok("grid_sizes", len(stage1_grid()) == 24 and len(downstream_grid()) == 672)
    dg = downstream_grid()
    dmatch = [x for x in dg if x["hysteresis"] == "latch" and x["stage2"] == "TSO" and x["binning"] == "global"
              and x["model_crit"] == ("q50", None) and x["S1"] == 2.0 and x["S2"] is None]
    ok("D_in_downstream_grid", len(dmatch) == 1)
    ok("D_in_stage1_grid", any(x["family"] == "ratio" and x["G"] == 1.3 and x["k"] == 3 and x["ref"] == "self"
                               for x in stage1_grid()))
    # --- pipeline state composition + fixed point -------------------------------------------------
    n = 200
    r5 = np.r_[np.ones(40), np.full(20, 1.5), np.ones(60), np.full(80, 1.6)]     # transient, then real onset
    ind = dict(r5=r5, z5=np.zeros(n), p5=np.ones(n))
    trig = r5 >= 1.3
    out = pipeline_states(trig, 3, ind, tso_predict_fn(500.0), hysteresis="calm")
    st = out["states"]
    ok("calm_clears_transient_and_reonsets", st[61] == 1 and (st[95:100] == 0).all() and st[122] == 1
       and out["fpt"][150] == 122)
    out2 = pipeline_states(trig, 3, ind, tso_predict_fn(40.0), hysteresis="calm")
    ok("calm_clear_suppressed_after_escalation_fixed_point",
       out2["states"][99] >= 2 and out2["fpt"][150] == 42 and out2["iterations"] >= 2,
       f"iterations={out2['iterations']}")
    zero = lambda f: {"q50": np.where(f >= 0, 0.0, np.nan)}  # noqa: E731
    flat = dict(r5=np.ones(100), z5=np.zeros(100), p5=np.ones(100))
    sp = pipeline_states(np.ones(100, bool), 3, flat, zero, pre_onset="provisional")["states"]
    sn = pipeline_states(np.ones(100, bool), 3, flat, zero, pre_onset="none")["states"]
    ok("pre_onset_provisional_allows_same_snapshot_critical", sp[21] == 0 and sp[22] == 3
       and sn[22] == 1 and np.argmax(sn >= 3) == 24)
    ok("pipeline_no_demotion", demotion_stats(out2["states"])["demotions"] == 0)
    out3 = pipeline_states(trig, 3, ind, tso_predict_fn(100.0), hysteresis="latch")
    ok("latch_tso_critical_at_q50_le30", np.argmax(out3["states"] >= 3) == 42 + 70 + 2
       and out3["crit_reason"] == "rul")
    r0d = demotion_stats(refs["R0"]["2_3"])
    ok("R0_has_demotions_ratchet_has_none", sum(demotion_stats(s)["demotions"] for s in refs["R0"].values()) > 0
       and all(demotion_stats(s)["demotions"] == 0 for s in refs["onset_eq_critical"].values()), str(r0d))
    ok("all_selfchecks", all(res.values()))
    return res


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    feats = c.load_features(); gt = c.load_gt()
    res = selfcheck(feats, gt)
    (RESULTS / "harness2_selfcheck.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    if not all(res.values()):
        print("SELF-CHECK FAILED:", [k for k, v in res.items() if not v])
        sys.exit(1)
    if "--selfcheck" in argv:
        return
    out = score_references(feats, gt)
    for nm in ("R0", "R1"):
        s = out["evs"][nm]["summary"]
        print(f"\n{nm}: C {s['C_w5']:.3f} CI {tuple(round(x, 3) for x in s['C_w5_ci95'])} MC {s['MC_events']} "
              f"{s['MC_bearings']} EP {s['EP_events']} {s['EP_bearings']} missed onsets {s['O3_missed_bearings']}")
        print(f"   legacy AP {out['evs'][nm]['legacy_120']['ap_pooled']:.3f} AUC {out['evs'][nm]['legacy_120']['auc_pooled']:.3f}; "
              f"warm-up-gated C {out['warm'][nm]['per_bearing'].C_w5.mean():.3f} "
              f"(non-healthy warm-up rows {out['warm'][nm]['warmup_rows_nonhealthy']})")


if __name__ == "__main__":
    main()
