"""Stage-2 track (post-onset RUL families + interval methods) under the frozen v2 protocol.

PROTOCOL: experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2). Deviations / interpretation choices were
written (dated) BEFORE any model was fit: results/post-onset-rul.deviations.txt (DV1-DV13).

Everything the protocol froze (indicators, replay, cost, bootstrap, sign-flip, influence) comes from
causal.py; conformal, grids, pipeline state composition, metrics, eligibility, selection and gates come
from harness2.py. This file adds only: the stage-2 features and learners (section 5 item 4), the
nested LOBO driver (4.3), and reporting.

Variants (DV10):
  D, D'          fixed references (computed FIRST, DV12)
  N              full nested protocol: stage 1 selected by C1, downstream (672) by C   -- confirmatory
  N_simplest     stage 1 fixed to ratio 1.3/k3/self, downstream nested                 -- exploratory
  N_oracle       onset latched at GT t_on (uses GT -> diagnostic upper bound only)     -- exploratory
  D_oracle       D's downstream with the oracle onset                                  -- exploratory

Run from repo root:  python experiments/xjtu/v2/build_post-onset-rul.py [--pilot-only]
"""
from __future__ import annotations

import os

import sys as _sys

PART = _sys.argv[_sys.argv.index("--part") + 1] if "--part" in _sys.argv else "N"
NJOBS = 1 if PART == "explo" else 3                       # DV14
os.environ["OMP_NUM_THREADS"] = str(NJOBS)

import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor  # noqa: E402

V2 = Path(__file__).resolve().parent
sys.path.insert(0, str(V2))
import causal as c  # noqa: E402
import harness2 as h  # noqa: E402

RES = V2 / "results"
CACHE = V2 / "cache" / "post-onset-rul"
OUT_JSON = RES / "post-onset-rul.json"
OUT_CSV = RES / "post-onset-rul_per_bearing.csv"
DEV_FILE = RES / "post-onset-rul.deviations.txt"
T0 = time.time()
CAP_H, PILOT_LIMIT_H = 3.0, 2.5
ALPHAS = (0.05, 0.1, 0.2)
PRE_ONSET = "provisional"                                  # DV1
FEAT_NAMES = ["tso", "log_r5", "dlog_r5_fpt", "log_peak_r5_since_fpt", "slope10_log_r5", "slope30_log_r5",
              "kurt_z5", "log_p5", "log_env_ratio"]


def log(*a):
    print(f"[{(time.time() - T0) / 60:6.1f} min]", *a, flush=True)


# ============================================================================ data + causal features
FEATS = c.load_features()
GT = c.load_gt()
BS = sorted(FEATS)
RUL = {b: FEATS[b].rul_minutes.to_numpy(float) for b in BS}
NROW = {b: len(FEATS[b]) for b in BS}


def _load_extra():
    cols = ["bearing_id", "cycle", "rul_minutes", "h_kurtosis", "v_kurtosis", "h_envelope_rms", "v_envelope_rms"]
    f = pd.read_csv(c.ROOT / "outputs/xjtu_features.csv", usecols=cols)
    f["b"] = f.bearing_id.str.replace("Bearing", "", regex=False)
    f = f.sort_values(["b", "cycle"])
    out = {b: d.reset_index(drop=True) for b, d in f.groupby("b", sort=True)}
    for b in BS:
        assert np.array_equal(out[b].rul_minutes.to_numpy(float), RUL[b]), b
    return out


EXTRA = _load_extra()


def robust_z(x):
    med = float(np.median(x[:c.COMMISSION]))
    mad = float(np.median(np.abs(x[:c.COMMISSION] - med)))
    return (x - med) / max(1.4826 * mad, 0.02 * abs(med), 1e-12)


def ols_slope(y, w):
    s = pd.Series(y)
    t = pd.Series(np.arange(len(y), dtype=float))
    cov = s.rolling(w, min_periods=2).cov(t)
    var = t.rolling(w, min_periods=2).var()
    return (cov / var).fillna(0.0).to_numpy()


IND = {ref: {b: c.indicators(FEATS[b], ref) for b in BS} for ref in h.REFS}


def _static(ref, b):
    ind = IND[ref][b]; e = EXTRA[b]
    r5 = ind["r5"]; lr = np.log(r5)
    kz = c.trailing_median(np.maximum(robust_z(e.h_kurtosis.to_numpy(float)), robust_z(e.v_kurtosis.to_numpy(float))))
    env = np.maximum(e.h_envelope_rms.to_numpy(float) / np.median(e.h_envelope_rms.to_numpy(float)[:c.COMMISSION]),
                     e.v_envelope_rms.to_numpy(float) / np.median(e.v_envelope_rms.to_numpy(float)[:c.COMMISSION]))
    return dict(r5=r5, lr=lr, s10=ols_slope(lr, 10), s30=ols_slope(lr, 30), kz=kz, lp=np.log(ind["p5"]), le=np.log(env))


STAT = {ref: {b: _static(ref, b) for b in BS} for ref in h.REFS}


def seg_X(ref, b, f):
    """Features for rows f..n-1 with onset row f (all causal; DV4)."""
    S = STAT[ref][b]; r = slice(f, NROW[b])
    return np.column_stack([np.arange(NROW[b] - f, dtype=float), S["lr"][r], S["lr"][r] - S["lr"][f],
                            np.log(np.maximum.accumulate(S["r5"][r])), S["s10"][r], S["s30"][r], S["kz"][r],
                            S["lp"][r], S["le"][r]])


def prov_X(ref, b):
    """Provisional features: onset = current row (tso 0, delta 0, peak = current r5). Equal to seg_X(f=i) at row i."""
    S = STAT[ref][b]; n = NROW[b]
    return np.column_stack([np.zeros(n), S["lr"], np.zeros(n), S["lr"], S["s10"], S["s30"], S["kz"], S["lp"], S["le"]])


# ============================================================================ stage-2 learners (section 5 item 4)
def fit_model(si, train, fpts, ref, seed):
    fam, prm = h.STAGE2[si]
    if fam == "TSO":
        lives = [RUL[b][fpts[b]] for b in train if fpts[b] >= 0]
        return dict(fam="TSO", L=float(np.median(lives)))
    Xs, ys, ws = [], [], []
    for b in train:
        f = fpts[b]
        if f < 0:
            continue
        y = RUL[b][f:]
        Xs.append(seg_X(ref, b, f)); ys.append(y); ws.append(np.full(len(y), 1.0 / len(y)))
    X, y, w = np.vstack(Xs), np.concatenate(ys), np.concatenate(ws)
    if fam == "ET":
        m = ExtraTreesRegressor(n_estimators=200, min_samples_leaf=prm["min_samples_leaf"],
                                max_features=prm["max_features"], n_jobs=NJOBS, random_state=seed)
        m.fit(X, np.log1p(np.minimum(y, 600.0)), sample_weight=w)
        return dict(fam="ET", m=m)
    ms = []
    for q in (0.1, 0.5, 0.9):
        m = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=200, learning_rate=0.05,
                                          max_leaf_nodes=15, min_samples_leaf=prm["min_samples_leaf"],
                                          early_stopping=False, random_state=seed)
        ms.append(m.fit(X, np.log1p(y), sample_weight=w))
    return dict(fam="HGBq", ms=ms)


def raw_pred(M, X):
    """(1, n) point prediction in minutes (TSO/ET) or (3, n) lo/mid/hi in log1p space (HGB-q)."""
    if M["fam"] == "TSO":
        return np.maximum(0.0, M["L"] - X[:, 0])[None, :]
    if M["fam"] == "ET":
        return np.expm1(M["m"].predict(X))[None, :]
    return np.vstack([m.predict(X) for m in M["ms"]])


# ============================================================================ stage-1 specs
def s1_spec(cfg):
    if cfg == "oracle":
        trig = {b: np.arange(NROW[b]) >= int(GT.at[b, "gt_on_row"]) for b in BS}
        k, ref, key = 1, "self", "oracle_t_on"
    else:
        ref, k, key = cfg["ref"], cfg["k"], h.stage1_name(cfg)
        trig = {b: h.stage1_trigger(cfg, IND[ref][b]) for b in BS}
    fpt = {}
    for b in BS:
        nz = np.where(c.replay(trig[b], k) >= 1)[0]
        fpt[b] = int(nz[0]) if len(nz) else -1
    return dict(key=key, cfg=cfg, ref=ref, k=k, trig=trig, fpt=fpt, ind=IND[ref])


# ============================================================================ per-fold stage-2 container
class FoldStage2:
    """Lazily fitted stage-2 models for one training set and one stage-1 spec.

    inner[si][j]: model trained on train minus j (inner LOBO); oof[si][j] = (raw, y) on j's post-FPT rows;
    refit[si]: model on all of train. Conformal for inner bearing i uses the OOF of the other inner bearings;
    for the outer (refit) prediction, the OOF of all training bearings (4.3 steps 2 and 4)."""

    def __init__(self, train, s1, seed, need_refit=True):
        self.train, self.s1, self.seed, self.need_refit = list(train), s1, seed, need_refit
        self.inner, self.oof, self.refit = {}, {}, {}
        self.raw_cache, self.pred_cache, self.cal_cache = {}, {}, {}
        self.n_fits = 0

    def ensure(self, si, need_inner=False):
        if si in self.oof:
            if need_inner and si not in self.inner:      # inner models were dropped; refit (deterministic seeds)
                self.inner[si] = {j: fit_model(si, [x for x in self.train if x != j], self.s1["fpt"], self.s1["ref"],
                                               self.seed) for j in self.train}
            return
        ref, fpt = self.s1["ref"], self.s1["fpt"]
        self.inner[si], self.oof[si] = {}, {}
        for j in self.train:
            M = fit_model(si, [x for x in self.train if x != j], fpt, ref, self.seed)
            self.n_fits += 0 if M["fam"] == "TSO" else (1 if M["fam"] == "ET" else 3)
            self.inner[si][j] = M
            if fpt[j] >= 0:
                self.oof[si][j] = (raw_pred(M, seg_X(ref, j, fpt[j])), RUL[j][fpt[j]:])
        if self.need_refit:
            self.refit[si] = fit_model(si, self.train, fpt, ref, self.seed)
            self.n_fits += 0 if h.STAGE2[si][0] == "TSO" else (1 if h.STAGE2[si][0] == "ET" else 3)

    def drop_inner(self):
        self.inner = {}
        self.raw_cache = {k: v for k, v in self.raw_cache.items() if k[1] == "outer"}
        self.pred_cache = {k: v for k, v in self.pred_cache.items() if k[3] == "outer"}

    def cal(self, si, exclude):
        key = (si, exclude)
        if key not in self.cal_cache:
            items = [(j, *self.oof[si][j]) for j in self.train if j != exclude and j in self.oof[si]]
            raw = np.hstack([r for _, r, _ in items]); y = np.concatenate([yy for _, _, yy in items])
            g = np.concatenate([np.full(len(yy), j) for j, _, yy in items])
            self.cal_cache[key] = (raw, y, g)
        return self.cal_cache[key]

    def _raw(self, si, role, b, fptarr):
        M = self.refit[si] if role == "outer" else self.inner[si][role]
        ref = self.s1["ref"]
        pk = (si, role, b, "prov")
        if pk not in self.raw_cache:
            self.raw_cache[pk] = raw_pred(M, prov_X(ref, b))
        out = self.raw_cache[pk].copy()
        idx = np.arange(len(fptarr)); post = fptarr != idx
        for f in np.unique(fptarr[post]):
            sk = (si, role, b, int(f))
            if sk not in self.raw_cache:
                self.raw_cache[sk] = raw_pred(M, seg_X(ref, b, int(f)))
            rows = np.where(post & (fptarr == f))[0]
            out[:, rows] = self.raw_cache[sk][:, rows - f]
        return out

    def predict(self, si, binning, b, fptarr, role):
        """role = inner bearing id (inner prediction for that bearing) or 'outer'."""
        key = (si, binning, b, role, hash(np.asarray(fptarr).tobytes()))
        if key in self.pred_cache:
            return self.pred_cache[key]
        self.ensure(si, need_inner=(role != "outer"))
        raw = self._raw(si, role, b, np.asarray(fptarr))
        craw, y, g = self.cal(si, None if role == "outer" else role)
        out = {}
        for a in ALPHAS:
            if h.STAGE2[si][0] == "HGBq":
                lo, mid, hi, ok = h.conformal_cqr(craw[0], craw[2], y, g, np.expm1(craw[1]), raw[0], raw[2],
                                                  np.expm1(raw[1]), a, binning)
            else:
                lo, mid, hi, ok = h.conformal_point(craw[0], y, g, raw[0], a, binning)
            out[f"lo_{a}"], out[f"hi_{a}"], out[f"lo_ok_{a}"] = lo, hi, ok
            if a == 0.1:
                out["q50"] = mid
        self.pred_cache[key] = out
        return out


# ============================================================================ costs
def cost_frame(states, bearings):
    rows = []
    for b in bearings:
        rul = RUL[b]; te, tl = c.gt_variant_rows(GT, b, NROW[b], "primary"); lr = float(rul[tl])
        r = c.bearing_cost(states[b], rul, te, tl, lr, c.W_EP_PRIMARY)
        row = dict(bearing=b, C=r["C"], C_w5=r["C"], C1=r["C1"], LC=r["LC"], MC=r["MC"], EP=r["EP"], ECh=r["ECh"],
                   FD=r["FD"], LD=r["LD"], crit_lead=r["crit_lead"])
        for w in (3.0, 10.0):
            row[f"C_w{int(w)}"] = c.bearing_cost(states[b], rul, te, tl, lr, w)["C"]
        row["demotions"] = h.demotion_stats(states[b])["demotions"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("bearing")


R0_STATES = c.states_r(pd.read_csv(c.ROOT / "experiments/xjtu/results/baseline_oof.csv"))
R0F = cost_frame(R0_STATES, BS)
DUMMY_STATES = c.dummy_states(FEATS, k=3)
DUMF = {nm: cost_frame(st, BS) for nm, st in DUMMY_STATES.items()}

S1GRID = h.stage1_grid()
S1SPECS = {h.stage1_name(cfg): s1_spec(cfg) for cfg in S1GRID}
S1COST = {}
for _nm, _sp in S1SPECS.items():
    S1COST[_nm] = cost_frame({b: c.replay(_sp["trig"][b], _sp["k"]) for b in BS}, BS)
D_KEY = h.stage1_name(h.D_STAGE1)
DP_KEY = h.stage1_name(h.DP_STAGE1)
ORACLE = s1_spec("oracle")


def spec_by_key(key):
    return ORACLE if key == ORACLE["key"] else S1SPECS[key]


def select_stage1(train):
    cands = []
    for cfg in S1GRID:
        nm = h.stage1_name(cfg)
        el = h.eligibility(S1COST[nm], R0F, DUMF, train, stage=1)
        cands.append(dict(per_bearing=S1COST[nm].loc[train], eligible=el["eligible"], complexity=cfg["complexity"],
                          grid_index=cfg["grid_index"], S2=None, name=nm))
    sel = h.select(cands, "C1", fallback=dict(name=D_KEY))
    return sel


# ============================================================================ downstream evaluation
def d_states_for(F: FoldStage2, d, b, role):
    s1 = F.s1

    def pf(fpt):
        return F.predict(d["stage2_index"], d["binning"], b, fpt, role)
    return h.pipeline_states(s1["trig"][b], s1["k"], s1["ind"][b], pf, model_crit=d["model_crit"], S1=d["S1"],
                             S2=d["S2"], hysteresis=d["hysteresis"], pre_onset=PRE_ONSET)


def eval_inner(F: FoldStage2, grid):
    cands = []
    for d in grid:
        st = {i: d_states_for(F, d, i, i)["states"] for i in F.train}
        fr = cost_frame(st, F.train)
        el = h.eligibility(fr, R0F, DUMF, F.train)
        cands.append(dict(per_bearing=fr, eligible=el["eligible"], elig=el, complexity=d["complexity"],
                          grid_index=d["grid_index"], S2=d["S2"], cfg=d, states=st))
    return cands


def d_name(d):
    mc = d["model_crit"][0] if d["model_crit"][1] is None else f"q10@a{d['model_crit'][1]}"
    prm = ",".join(f"{k}={v}" for k, v in d["stage2_params"].items())
    return (f"{d['hysteresis']}|{d['stage2']}({prm})|{d['binning']}|crit:{mc}|S1={d['S1']}|"
            f"S2={'off' if d['S2'] is None else d['S2']}")


FULL_GRID = h.downstream_grid()
D_DOWN = [x for x in FULL_GRID if x["hysteresis"] == "latch" and x["stage2"] == "TSO" and x["binning"] == "global"
          and x["model_crit"] == ("q50", None) and x["S1"] == 2.0 and x["S2"] is None][0]


def shrink_grid(grid, level):
    """Pre-set, outcome-blind shrink order (5.1)."""
    g = grid
    if level >= 1:
        g = [x for x in g if x["S2"] in (None, 6.0)]
    if level >= 2:
        g = [x for x in g if x["model_crit"] in (("q50", None), ("q10", 0.1))]
    if level >= 3:
        g = [x for x in g if x["stage2"] != "ET" or x["stage2_params"] == dict(min_samples_leaf=20, max_features=0.3)]
    return g


def p0_from(states, train):
    fr = []
    for b in train:
        st = states[b]; o0 = (st == 0) & (np.arange(len(st)) >= c.COMMISSION)
        if o0.any():
            fr.append(float((RUL[b][o0] <= h.HORIZON).mean()))
    return float(np.mean(fr)) if fr else np.nan


def outer_publish(F, d, b, p0):
    out = d_states_for(F, d, b, "outer")
    st = out["states"]; pred = out["pred"]; nh = st >= 1
    q50 = np.where(nh, pred["q50"], np.nan)
    lo = np.where(nh, pred["lo_0.1"], np.nan); hi = np.where(nh, pred["hi_0.1"], np.nan)
    V = np.vstack([pred["lo_0.05"], pred["lo_0.1"], pred["q50"], pred["hi_0.1"], pred["hi_0.05"]])
    pw = h.prob_within(h.HORIZON, (0.05, 0.1, 0.5, 0.9, 0.95), V)
    prob = np.where(nh, pw, p0)
    return dict(states=st, q50=q50, lo=lo, hi=hi, prob=prob, reasons=dict(crit_reason=out["crit_reason"],
                faulty_reason=out["faulty_reason"]), fpt=out["fpt"])


# ============================================================================ fold driver
FOLD_MEMO: dict = {}
F_MEMO: dict = {}


def get_F(fold, train, s1key, seed, need_refit=True):
    key = (fold, s1key)
    if key not in F_MEMO:
        F_MEMO[key] = FoldStage2(train, spec_by_key(s1key), seed, need_refit)
    return F_MEMO[key]


def run_fold(fold, held, train, s1key, grid, grid_tag):
    """Nested downstream selection for one outer fold with a given stage-1 spec; returns held-out output."""
    mk = (fold, s1key, grid_tag)
    if mk in FOLD_MEMO:
        return FOLD_MEMO[mk]
    t = time.time()
    F = get_F(fold, train, s1key, 42 + fold)
    cands = eval_inner(F, grid)
    sel = h.select(cands, "C", fallback=None)
    res = dict(fold=fold, held_out=held, stage1=s1key, n_candidates=len(cands), n_eligible=sel["n_eligible"],
               tie_size=sel["tie_size"], fell_back=sel["fell_back"])
    if sel["fell_back"]:
        res["note"] = "no eligible downstream candidate: fold uses D (4.2)"
    else:
        ch = sel["choice"]; d = ch["cfg"]
        p0 = p0_from(ch["states"], train)
        pub = outer_publish(F, d, held, p0)
        res.update(choice=d_name(d), choice_cfg=dict(d), p0=p0, pub=pub, inner_best_C=sel["best_mean"],
                   inner_choice_C=sel["choice_mean"], choice_inner_EP=int(ch["per_bearing"].EP.sum()),
                   choice_inner_MC=int(ch["per_bearing"].MC.sum()),
                   eligible_reasons_failed=dict(pd.Series([("E1" if not x["elig"]["E1"] else "") +
                                                           ("E2" if not x["elig"]["E2"] else "") +
                                                           ("E3" if not x["elig"]["E3"] else "")
                                                           for x in cands]).value_counts()))
    res["seconds"] = time.time() - t
    res["n_fits"] = F.n_fits
    F.drop_inner()
    FOLD_MEMO[mk] = res
    return res


def run_fixed(fold, held, train, s1key, d):
    """Fixed downstream config d (D, D', D_oracle): no selection; conformal still nested (14 inner OOF)."""
    F = get_F(fold, train, s1key, 42 + fold)
    F.ensure(d["stage2_index"])
    st_tr = {i: d_states_for(F, d, i, i)["states"] for i in train}
    p0 = p0_from(st_tr, train)
    pub = outer_publish(F, d, held, p0)
    F.drop_inner()
    return dict(fold=fold, held_out=held, stage1=s1key, choice=d_name(d), choice_cfg=dict(d), p0=p0, pub=pub,
                inner_choice_C=float(cost_frame(st_tr, train).C.mean()))


# ============================================================================ assemble + evaluate a variant
BASE = h.p1_baselines(FEATS, GT)


def assemble(name, folds, kmap, fallback_D=None):
    st, q50, lo, hi, prob, reasons, p0 = {}, {}, {}, {}, {}, {}, {}
    for r in folds:
        b = r["held_out"]
        pub = r.get("pub")
        if pub is None:                       # fold fell back to D
            pub = fallback_D[b]["pub"]; r["p0"] = fallback_D[b]["p0"]
        st[b], q50[b], lo[b], hi[b], prob[b] = pub["states"], pub["q50"], pub["lo"], pub["hi"], pub["prob"]
        reasons[b] = pub["reasons"]; p0[b] = r["p0"]
    ev = h.evaluate(name, st, FEATS, GT, k=None, rul_pred=q50, q_lo=lo, q_hi=hi, prob=prob, reasons=reasons,
                    p0=p0, baselines=BASE, interval_note="bearing-grouped conformal [q10, q90], alpha 0.1 (DV7)")
    # O1 with each fold's own stage-1 k (eligible row 20 + k - 1)
    per = ev["per_bearing"]
    for b in per.index:
        k = kmap[b]; n = NROW[b]; te, tl = c.gt_variant_rows(GT, b, n, "primary")
        dl = per.at[b, "det_lead"]; ref_late = max(tl, c.COMMISSION + k - 1)
        tdet = None if np.isnan(dl) else int(n - 1 - dl)
        per.at[b, "O1_delay_min"] = (np.nan if tdet is None else 0 if te <= tdet <= ref_late else
                                     (tdet - ref_late if tdet > ref_late else tdet - te))
        per.at[b, "stage1_k"] = k
    ev["summary"] = h.summarize(per, GT)
    ev["states"] = st
    return ev


def fold_table(folds):
    keep = ("fold", "held_out", "stage1", "stage1_n_eligible", "stage1_tie_size", "stage1_fell_back", "choice",
            "n_candidates", "n_eligible", "tie_size", "fell_back", "note", "p0", "inner_best_C", "inner_choice_C",
            "choice_inner_EP", "choice_inner_MC", "eligible_reasons_failed", "seconds", "n_fits")
    return [{k: r[k] for k in keep if k in r} for r in folds]


def variant_doc(ev, folds, refs):
    out = dict(summary=ev["summary"], legacy_120_comparison_only=ev.get("legacy_120"),
               rul_comparison_only=ev.get("rul"), coverage=ev.get("coverage"), folds=fold_table(folds))
    P = ev["per_bearing"]
    for r in folds:
        if "pub" in r or r.get("fell_back"):
            P.loc[r["held_out"], "fold_choice"] = r.get("choice", "D (fallback)")
            P.loc[r["held_out"], "inner_choice_C"] = r.get("inner_choice_C", np.nan)
    out["inner_vs_outer"] = dict(inner_choice_C_mean=float(np.nanmean([r.get("inner_choice_C", np.nan) for r in folds])),
                                 outer_C_mean=float(P.C.mean()))
    out["choice_counts"] = pd.Series([r.get("choice", "D (fallback)") for r in folds]).value_counts().to_dict()
    out["paired"] = {f"vs_{nm}": h.paired(ev, refs[nm]) for nm in refs if refs[nm] is not ev}
    out["G"] = {k: h.gate_G(v) for k, v in out["paired"].items()}
    return out


# ============================================================================ main (parts, DV14)
import pickle  # noqa: E402

FOLDS_DEF = h.outer_folds(BS)


def part_N():
    """D, D' first (DV12), then N with pilot + pre-set shrink rule, then the all-15 modal run."""
    sc = h.selfcheck(FEATS, GT, verbose=False)
    assert all(sc.values()), [k for k, v in sc.items() if not v]
    log("harness2 self-checks pass; frozen hashes ok")
    timing = {}
    t = time.time()
    Dres = {b: run_fixed(i, b, tr, D_KEY, D_DOWN) for i, b, tr in FOLDS_DEF}
    DPres = {b: run_fixed(i, b, tr, DP_KEY, D_DOWN) for i, b, tr in FOLDS_DEF}
    evD = assemble("D", list(Dres.values()), {b: 3 for b in BS})
    evDp = assemble("D'", list(DPres.values()), {b: 5 for b in BS})
    timing["D_Dp_s"] = time.time() - t
    dsum = {nm: dict(C=float(e["per_bearing"].C.mean()), MC=e["summary"]["MC_events"], EP=e["summary"]["EP_events"],
                     EP_bearings=e["summary"]["EP_bearings"], MC_bearings=e["summary"]["MC_bearings"])
            for nm, e in (("D", evD), ("D'", evDp))}
    (RES / "post-onset-rul_D.json").write_text(json.dumps(h.jsonable(dict(
        note="D and D' computed first, before any other candidate (DV12 / A.20)", summary=dsum,
        D=evD["summary"], Dp=evDp["summary"])), indent=2), encoding="utf-8")
    log("D :", dsum["D"]); log("D':", dsum["D'"])

    shrink, grid, s1sel = 0, FULL_GRID, {}

    def n_fold(i, b, tr, grid, tag):
        sel = s1sel.get(b) or select_stage1(tr)
        s1sel[b] = sel
        r = dict(run_fold(i, b, tr, sel["choice"]["name"], grid, tag))
        r.update(stage1_n_eligible=sel["n_eligible"], stage1_tie_size=sel["tie_size"],
                 stage1_fell_back=sel["fell_back"])
        return r

    t = time.time()
    i0, b0, tr0 = FOLDS_DEF[0]
    r0 = n_fold(i0, b0, tr0, grid, "full")
    pilot_s = time.time() - t
    proj_h = (pilot_s * 16 + (time.time() - T0)) / 3600
    log(f"pilot fold 0 ({b0}) took {pilot_s:.0f}s; projected N total {proj_h:.2f} h")
    while proj_h > PILOT_LIMIT_H and shrink < 3:
        shrink += 1
        grid = shrink_grid(FULL_GRID, shrink)
        proj_h = (pilot_s * len(grid) / len(FULL_GRID) * 16 + (time.time() - T0)) / 3600
        log(f"shrink level {shrink}: grid {len(grid)}; projected {proj_h:.2f} h")
    tag = "full" if shrink == 0 else f"shrink{shrink}"
    timing.update(pilot_fold_s=pilot_s, pilot_projection_h=proj_h)
    Nres = [r0 if shrink == 0 else n_fold(i0, b0, tr0, grid, tag)]
    for i, b, tr in FOLDS_DEF[1:]:
        Nres.append(n_fold(i, b, tr, grid, tag))
        r = Nres[-1]
        log(f"N fold {i} {b}: stage1 {r['stage1']} | {r.get('choice', 'D fallback')} | elig {r['n_eligible']} "
            f"tie {r['tie_size']} | {r['seconds']:.0f}s")
        if (time.time() - T0) / 3600 > CAP_H:
            raise RuntimeError("3 h hard cap exceeded during N")
    timing["N_s"] = time.time() - t
    t = time.time()
    sel15 = select_stage1(BS)
    F15 = FoldStage2(BS, spec_by_key(sel15["choice"]["name"]), 42 + 15, need_refit=False)
    s15 = h.select(eval_inner(F15, grid), "C", fallback=None)
    modal = dict(stage1=sel15["choice"]["name"], stage1_n_eligible=sel15["n_eligible"], stage1_tie=sel15["tie_size"],
                 downstream=None if s15["fell_back"] else d_name(s15["choice"]["cfg"]),
                 fell_back_to_D=s15["fell_back"], n_eligible=s15["n_eligible"], tie_size=s15["tie_size"],
                 inner_C_15fold=s15.get("choice_mean"))
    timing["modal_s"] = time.time() - t
    timing["total_N_part_h"] = (time.time() - T0) / 3600
    log("modal (all-15) configuration:", modal)
    with open(CACHE / "part_N.pkl", "wb") as fh:
        pickle.dump(dict(Dres=Dres, DPres=DPres, Nres=Nres, modal=modal, timing=timing, shrink=shrink,
                         grid_size=len(grid)), fh)
    log("part N done")


def part_explo(shrink=0):
    grid = shrink_grid(FULL_GRID, shrink)
    tag = "full" if shrink == 0 else f"shrink{shrink}"
    timing = {}
    Dres = {b: run_fixed(i, b, tr, D_KEY, D_DOWN) for i, b, tr in FOLDS_DEF}
    out = {}
    for vname, key in (("N_simplest", D_KEY), ("N_oracle", ORACLE["key"])):
        t = time.time(); res = []
        for i, b, tr in FOLDS_DEF:
            r = dict(run_fold(i, b, tr, key, grid, tag)); r.update(stage1=key)
            res.append(r)
            log(f"{vname} fold {i} {b}: {r.get('choice', 'D fallback')} | elig {r['n_eligible']} tie {r['tie_size']}"
                f" | {r['seconds']:.0f}s")
        out[vname] = res; timing[vname + "_s"] = time.time() - t
    DOres = {b: run_fixed(i, b, tr, ORACLE["key"], D_DOWN) for i, b, tr in FOLDS_DEF}
    desc = {}
    for vkey, s1key in (("simplest", D_KEY), ("oracle", ORACLE["key"])):
        rows = []
        for d in [x for x in FULL_GRID if x["hysteresis"] == "latch" and x["S1"] == 2.0 and x["S2"] is None]:
            st = {}
            for i, b, tr in FOLDS_DEF:
                F = get_F(i, tr, s1key, 42 + i)
                F.ensure(d["stage2_index"])
                st[b] = outer_publish(F, d, b, np.nan)["states"]
            fr = cost_frame(st, BS)
            rows.append(dict(config=d_name(d), C=fr.C.mean(), C_w3=fr.C_w3.mean(), C_w10=fr.C_w10.mean(),
                             LC_sum=fr.LC.sum(), MC=int(fr.MC.sum()), EP=int(fr.EP.sum()),
                             EP_bearings=[f"{b}@{int(fr.at[b, 'crit_lead'])}" for b in fr.index[fr.EP == 1]],
                             crit_never=sorted(fr.index[fr.crit_lead.isna()])))
        desc[vkey] = rows
    with open(CACHE / "part_explo.pkl", "wb") as fh:
        pickle.dump(dict(explo=out, DOres=DOres, Dres_explo=Dres, desc=desc, timing=timing, shrink=shrink), fh)
    log("part explo done")


def part_report():
    with open(CACHE / "part_N.pkl", "rb") as fh:
        A = pickle.load(fh)
    Bx = None
    if (CACHE / "part_explo.pkl").exists():
        with open(CACHE / "part_explo.pkl", "rb") as fh:
            Bx = pickle.load(fh)
    ev_d = {nm: h.evaluate(nm, st, FEATS, GT, k=3) for nm, st in DUMMY_STATES.items()}
    refs = {}
    for nm, fn in (("R0", "baseline_oof.csv"), ("R1", "combined_oof.csv")):
        inp = h.reference_inputs(c.ROOT / "experiments/xjtu/results" / fn)
        refs[nm] = h.evaluate(nm, inp["states"], FEATS, GT, k=None, rul_pred=inp["rul_pred"], q_lo=inp["q_lo"],
                              q_hi=inp["q_hi"], prob=inp["prob"], baselines=BASE,
                              interval_note=f"served band pred +/- e90 ({inp['e90']:.2f})")
    assert abs(refs["R0"]["per_bearing"].C.mean() - 6.740277777777777) < 1e-9
    Dres, DPres, Nres = A["Dres"], A["DPres"], A["Nres"]
    evD = assemble("D", list(Dres.values()), {b: 3 for b in BS})
    evDp = assemble("D'", list(DPres.values()), {b: 5 for b in BS})
    evN = assemble("N", Nres, {r["held_out"]: spec_by_key(r["stage1"])["k"] for r in Nres}, fallback_D=Dres)
    explo, extra_ev = {}, {}
    if Bx is not None:
        for vname, res in Bx["explo"].items():
            key = res[0]["stage1"]
            explo[vname] = (assemble(vname, res, {b: spec_by_key(key)["k"] for b in BS}, fallback_D=Dres), res)
        extra_ev["D_oracle"] = (assemble("D_oracle", list(Bx["DOres"].values()), {b: 1 for b in BS}),
                                list(Bx["DOres"].values()))
    allrefs = {"R0": refs["R0"], "R1": refs["R1"], "D": evD, "D'": evDp}
    gP = {nm: h.gates_P(e, refs["R0"], evD, ev_d) for nm, e in (("N", evN), ("D", evD), ("D'", evDp))}
    for nm, (e, _) in {**explo, **extra_ev}.items():
        gP[nm + " (exploratory)"] = h.gates_P(e, refs["R0"], evD, ev_d)
    G_ND = h.gate_G(h.paired(evN, evD))
    G_DR0 = h.gate_G(h.paired(evD, refs["R0"]))
    G_DpR0 = h.gate_G(h.paired(evDp, refs["R0"]))
    if gP["N"]["passed"] and G_ND["passed"]:
        decision = "N ships"
    elif gP["D"]["passed"]:
        decision = "D ships" + ("" if gP["D'"]["passed"] else " (conditional on defaults chosen with all bearings visible)")
    else:
        decision = "nothing ships"
    gnd_fail = [k for k in ("G_a", "G_b") if not G_ND[k]] + G_ND["G_c_failed"]
    failed = dict(N=gP["N"]["failed"] + ([] if G_ND["passed"] else ["G(N better than D): " + ",".join(gnd_fail)]),
                  D=gP["D"]["failed"], Dp=gP["D'"]["failed"])
    log("DECISION:", decision, failed)
    evs = {"N": evN, "D": evD, "D'": evDp, "R0": refs["R0"], "R1": refs["R1"],
           **{k: v[0] for k, v in explo.items()}, **{k: v[0] for k, v in extra_ev.items()}}
    pairs = (("N", "D"), ("N", "R0"), ("D", "R0"), ("D'", "R0"), ("N", "R1"), ("N", "D'")) + tuple(
        (k, r) for k in list(explo) + list(extra_ev) for r in ("R0", "D", "R1"))
    cmp = h.compare(evs, pairs=pairs, dummies=ev_d, show=True)
    variants = dict(N=variant_doc(evN, Nres, allrefs), D=variant_doc(evD, list(Dres.values()), allrefs),
                    Dp=variant_doc(evDp, list(DPres.values()), allrefs))
    for k, (e, res) in {**explo, **extra_ev}.items():
        variants[k] = variant_doc(e, res, allrefs)
    doc = dict(
        protocol="experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2)", frozen_hashes_ok=h.check_frozen(),
        deviations_file=str(DEV_FILE.relative_to(c.ROOT)), deviations=DEV_FILE.read_text(encoding="utf-8").splitlines(),
        pre_onset=PRE_ONSET, grid_shrink_level=A["shrink"], grid_size=A["grid_size"],
        explo_grid_shrink_level=None if Bx is None else Bx["shrink"],
        timing=dict(N_part=A["timing"], explo_part=None if Bx is None else Bx["timing"]),
        stage2_features=FEAT_NAMES, decision=decision, failed_conditions=failed, gates_P=gP,
        G_N_better_than_D=G_ND, G_D_better_than_R0=G_DR0, G_Dp_better_than_R0=G_DpR0,
        modal_configuration=A["modal"], table=cmp["table"], paired=cmp["paired"], variants=variants,
        references=dict(R0=refs["R0"]["summary"], R1=refs["R1"]["summary"]),
        dummies={k: v["summary"] for k, v in ev_d.items()},
        descriptive_outer_grid_not_selection=None if Bx is None else Bx["desc"],
        status=dict(N="confirmatory (nested, protocol 4.3)", D="fixed reference", Dp="fixed sensitivity reference",
                    N_simplest="exploratory: stage 1 fixed to ratio1.3/k3/self (DV10b)",
                    N_oracle="exploratory diagnostic: GT t_on used as onset input (DV10a); never a ship candidate",
                    D_oracle="exploratory diagnostic: D downstream with GT onset (DV10a)"),
    )
    OUT_JSON.write_text(json.dumps(h.jsonable(doc), indent=2), encoding="utf-8")
    pb = [e["per_bearing"].assign(model=nm).reset_index() for nm, e in evs.items()]
    pd.concat(pb).to_csv(OUT_CSV, index=False)
    log("wrote", OUT_JSON, OUT_CSV)


if __name__ == "__main__":
    RES.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    {"N": part_N, "explo": part_explo, "report": part_report}[PART]()


