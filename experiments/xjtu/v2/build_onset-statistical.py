"""Stage-1 track (statistical / unsupervised onset detectors) under the FROZEN v2 protocol.

PROTOCOL: experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2). Evaluator: causal.py (frozen) via
harness2.py (no re-implementation of indicators, replay, cost or statistics).

What this script does
- Stage 1: the protocol's full stage-1 candidate space (24 configs: ratio / dual-gate x G x S x k x
  {self, self+min60 fallback}), chosen inside every outer fold by the paired 1-SE rule on inner C1
  with the stage-1 E2 constraint (PROTOCOL 4.3 step 1).
- Stage 2: fixed to the protocol's simplest RUL model, TSO (q50 = max(0, L - tso)), so the stage-1
  effect is isolated. Bounds: bearing-grouped log1p conformal (global or Mondrian, 5-bearing rule).
- Two nested pipelines are reported:
    N_TSO  -- stage 1 nested + the TSO slice of the downstream grid (2 hyst x 2 binning x 3 model_crit
              x 2 S1 x 4 S2 = 96 combos), selected with E1-E3 and the paired 1-SE rule on inner C.
    N_S1   -- stage 1 nested, every downstream item fixed at D's values (pure stage-1 isolation).
- References: D (computed FIRST, before any candidate), D', R0, R1, five dummies.
- Gates G and P1-P6, decision order of 4.4, per-bearing bootstrap CIs and exact sign-flip p for
  every pair vs D and vs R0.

Run from the repo root:  python experiments/xjtu/v2/build_onset-statistical.py
Writes: experiments/xjtu/v2/results/onset-statistical.json (+ _per_bearing.csv, .deviations.json).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

V2 = Path(__file__).resolve().parent
sys.path.insert(0, str(V2))
import causal as c  # noqa: E402  frozen evaluator
import harness2 as h  # noqa: E402  protocol harness (imports causal)

RES = V2 / "results"
OUT_JSON = RES / "onset-statistical.json"
OUT_PB = RES / "onset-statistical_per_bearing.csv"
OUT_DEV = RES / "onset-statistical.deviations.json"
PRE_ONSET = "provisional"          # harness2.pipeline_states default (see DEVIATIONS[1])
PUBLISH_ALPHA = 0.1                # published interval [q10, q90] (P2 nominal 0.90)
PROB_LEVELS = (0.05, 0.1, 0.5, 0.9, 0.95)
TIME_CAP_H, SHRINK_AT_H = 3.0, 2.5

# Written to disk BEFORE any candidate/D result is computed (PROTOCOL header: deviations are
# recorded before the affected result is looked at; results they affect are exploratory).
DEVIATIONS = [
    dict(id=1, kind="ambiguity resolution (not a change)",
         text=("Pre-onset stage-2 semantics: pre_onset='provisional' (harness2 default). While O = 0 the "
               "faulty/critical conditions are evaluated with a provisional onset at the current row "
               "(tso = 0); no RUL is published while healthy. This follows the concurrent-counter rule "
               "of PROTOCOL 2.2 and the latency floors of 1.3. Same choice for D, D', N_TSO, N_S1.")),
    dict(id=2, kind="ambiguity resolution (not a change)",
         text=("Warm-up: pipeline states come from causal.replay, which gates rows < 20 (healthy). R0/R1 "
               "use the frozen ungated causal.states_r mapping, exactly as the frozen 4.5 table.")),
    dict(id=3, kind="DEVIATION: candidate space restricted (task scope)",
         text=("Stage-2 family fixed to TSO (the protocol's simplest reference RUL model) to isolate the "
               "stage-1 effect. ET and HGB-q (5 of the 7 stage-2 configs, 576 of 672 downstream combos) "
               "are NOT searched. N_TSO is therefore the protocol's nested procedure on a 96-combo subset "
               "of the 672 grid, not the protocol's N. Its gate outcomes are reported, but a ship "
               "decision for the protocol's N requires the full grid (run_nested.py). Results for "
               "N_TSO/N_S1 are labelled exploratory-for-N; D, D', R0 and R1 are unaffected.")),
    dict(id=4, kind="DEVIATION: secondary pipeline not in the protocol",
         text=("N_S1 (stage 1 nested, downstream fixed at D's values) is an additional isolation analysis; "
               "it is not a protocol candidate selection and is reported as exploratory.")),
    dict(id=5, kind="scope note (nothing added)",
         text=("The task text mentions CUSUM/EWMA detectors on fused health indicators. They are NOT in "
               "the frozen candidate space (section 5: ratio and dual-gate only) and were NOT searched or "
               "scored. Only the 24 protocol stage-1 configs are searched.")),
    dict(id=6, kind="ambiguity resolution (not a change)",
         text=("Stage-1 scoring for C1 and the causal FPT used for TSO training/calibration use the hard "
               "latch (harness2.causal_fpt); the hysteresis option is a downstream item and is applied in "
               "the full replay only. Fallback when no downstream candidate is eligible = D's full "
               "configuration (stage 1 and downstream), as 4.2 says 'that fold uses D's configuration'. "
               "p0 = bearing-equal fleet rate of RUL <= 120 over rows >= 20 that are healthy in the chosen "
               "pipeline's inner-OOF replay on the training bearings. Published interval alpha = 0.1 for "
               "every model (P2 nominal 0.90), whatever model_crit alpha was selected.")),
    dict(id=7, kind="ambiguity resolution (not a change)",
         text=("O1 delay for nested pipelines uses each bearing's own fold-selected k for the eligibility "
               "row (harness2.evaluate takes one k; the O1 column is recomputed per bearing).")),
]


# ============================================================================ helpers
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def stage1_states(feats, cfg):
    """Hard-latch stage-1 states, indicators and FPT per bearing for one stage-1 config."""
    out = {}
    for b, d in feats.items():
        ind = c.indicators(d, cfg["ref"])
        trig = h.stage1_trigger(cfg, ind)
        st = c.replay(trig, cfg["k"])
        nz = np.where(st >= 1)[0]
        out[b] = dict(ind=ind, trig=trig, st=st, fpt=int(nz[0]) if len(nz) else -1)
    return out


def per_bearing_frame(states, feats, gt, bearings):
    """Primary-GT cost terms at w_EP 5, plus C_w3/C_w10 (exactly C + (w - 5) * EP) and demotions."""
    P = c.score({b: states[b] for b in bearings}, feats, gt, w_ep=c.W_EP_PRIMARY, bearings=list(bearings))
    for w in c.W_EP_SENS:
        P[f"C_w{int(w)}"] = P.C + (w - c.W_EP_PRIMARY) * P.EP
    P["demotions"] = [h.demotion_stats(states[b])["demotions"] for b in P.index]
    return P


class TSOModel:
    """TSO stage 2 with bearing-grouped log1p conformal (harness2.Conformal semantics), cached.

    L = median post-onset life (true RUL at the hard-latch causal FPT) of the fitting bearings
    (one value per bearing -> bearing-equal). Calibration residuals e = log1p(y) - log1p(q50_oof)
    on the calibration bearings' post-FPT rows, where q50_oof comes from that bearing's own
    leave-one-out L (inner OOF)."""
    REPS = np.array([15.0, 45.0, 90.0, 200.0])      # one representative q50 per Mondrian bin

    def __init__(self, L, e, g, pred_cal, binning):
        self.L, self.binning = float(L), binning
        self.tab = {}
        for a in sorted({PUBLISH_ALPHA, 0.2, 0.05}):
            cf = h.Conformal(binning).fit(e, g, pred_cal)
            lo, ok_lo = cf.quantile(a, self.REPS)
            hi, ok_hi = cf.quantile(1 - a, self.REPS)
            self.tab[a] = (lo, hi, ok_lo & ok_hi)

    def bounds(self, q50, a):
        lo, hi, ok = self.tab[a]
        tb = h.q50_bin(q50) if self.binning == "mondrian" else np.zeros(len(q50), int)
        lp = np.log1p(q50)
        return h._order(np.expm1(lp + lo[tb]), q50, np.expm1(lp + hi[tb])) + (ok[tb],)

    def predict_fn(self, alphas=(0.1, 0.2)):
        def f(fpt):
            idx = np.arange(len(fpt), dtype=float)
            q50 = np.where(fpt >= 0, np.maximum(0.0, self.L - (idx - fpt)), np.nan)
            out = {"q50": q50}
            for a in alphas:
                lo, _, _, ok = self.bounds(np.nan_to_num(q50, nan=self.L), a)
                out[f"lo_{a}"] = np.where(np.isnan(q50), np.nan, lo); out[f"lo_ok_{a}"] = ok
            return out
        return f


def lives_of(feats, s1, bearings):
    return {b: float(feats[b].rul_minutes.iloc[s1[b]["fpt"]]) for b in bearings if s1[b]["fpt"] >= 0}


def loo_residuals(feats, s1, train):
    """Inner-OOF TSO residuals for every bearing j in train: L_{-j} from train \\ {j}."""
    lives = lives_of(feats, s1, train)
    res = {}
    for j in train:
        if s1[j]["fpt"] < 0:
            continue
        Lj = float(np.median([v for b, v in lives.items() if b != j]))
        y = feats[j].rul_minutes.to_numpy(float)[s1[j]["fpt"]:]
        tso = np.arange(len(y), dtype=float)
        pred = np.maximum(0.0, Lj - tso)
        res[j] = (np.log1p(y) - np.log1p(pred), pred, Lj)
    return res, lives


def make_model(res, lives, fit_bearings, calib_bearings, binning):
    L = float(np.median([lives[b] for b in fit_bearings if b in lives]))
    cb = [b for b in calib_bearings if b in res]
    e = np.concatenate([res[b][0] for b in cb]); g = np.concatenate([np.full(len(res[b][0]), b) for b in cb])
    pc = np.concatenate([res[b][1] for b in cb])
    return TSOModel(L, e, g, pc, binning)


def run_pipeline(s1b, ind_cfg_k, model, ds):
    out = h.pipeline_states(s1b["trig"], ind_cfg_k, s1b["ind"], model.predict_fn(),
                            model_crit=ds["model_crit"], S1=ds["S1"], S2=ds["S2"],
                            hysteresis=ds["hysteresis"], pre_onset=PRE_ONSET)
    return out


def publish(out, model, st):
    """Published outputs (2.3): q50/q10/q90 while O = 1, NaN while healthy; probability."""
    fpt = out["fpt"]
    q50 = np.asarray(out["pred"]["q50"], float)
    lo, _, hi, _ = model.bounds(np.nan_to_num(q50, nan=model.L), PUBLISH_ALPHA)
    lo5, _, hi5, _ = model.bounds(np.nan_to_num(q50, nan=model.L), 0.05)
    on = st >= 1
    prob = h.prob_within(h.HORIZON, PROB_LEVELS, np.vstack([lo5, lo, np.nan_to_num(q50, nan=model.L), hi, hi5]))
    return dict(q50=np.where(on, q50, np.nan), lo=np.where(on, lo, np.nan), hi=np.where(on, hi, np.nan),
                prob_on=prob, fpt=fpt)


# ============================================================================ nested selection
def select_stage1(train, s1_frames, dummy_pb, D_cand):
    cands = []
    for cfg in h.stage1_grid():
        P = s1_frames[cfg["name"]].loc[train]
        el = h.eligibility(P, None, dummy_pb, bearings=train, stage=1)
        cands.append(dict(cfg=cfg, per_bearing=P, eligible=el["eligible"], complexity=cfg["complexity"],
                          grid_index=cfg["grid_index"], name=cfg["name"]))
    sel = h.select(cands, metric="C1", fallback=D_cand)
    return sel, cands


def downstream_tso_grid():
    return [x for x in h.downstream_grid() if x["stage2"] == "TSO"]


def inner_replays(feats, gt, train, s1, cfg, grid, r0_pb, dummy_pb):
    """Inner-LOBO TSO predictions on the training bearings and replay of every downstream combo."""
    res, lives = loo_residuals(feats, s1, train)
    models = {}
    for i in train:
        others = [b for b in train if b != i]
        for binning in h.BINNING:
            models[(i, binning)] = make_model(res, lives, others, others, binning)
    cands = []
    for ds in grid:
        states, outs = {}, {}
        for i in train:
            o = run_pipeline(s1[i], cfg["k"], models[(i, ds["binning"])], ds)
            states[i] = o["states"]; outs[i] = o
        P = per_bearing_frame(states, feats, gt, train)
        el = h.eligibility(P, r0_pb, dummy_pb, bearings=train)
        cands.append(dict(ds=ds, per_bearing=P, eligible=el["eligible"], elig=el, complexity=ds["complexity"],
                          grid_index=ds["grid_index"], S2=ds["S2"], states=states))
    return cands, res, lives


def p0_rate(feats, states, train):
    rates = []
    for b in train:
        st = states[b]; rul = feats[b].rul_minutes.to_numpy(float)
        o0 = (st == 0) & (np.arange(len(st)) >= c.COMMISSION)
        if o0.any():
            rates.append(float((rul[o0] <= h.HORIZON).mean()))
    return float(np.mean(rates)) if rates else np.nan


def ds_name(ds):
    mc = ds["model_crit"][0] if ds["model_crit"][0] == "q50" else f"q10@a{ds['model_crit'][1]}"
    return f"{ds['hysteresis']}/TSO/{ds['binning']}/{mc}/S1={ds['S1']}/S2={ds['S2'] or 'off'}"


def apply_fixed(feats, s1_cache, train, held, cfg, ds):
    """Refit chosen stage 2 on the 14 training bearings and apply to the held-out bearing (4.3.4)."""
    s1 = s1_cache[cfg["name"]]
    res, lives = loo_residuals(feats, s1, train)
    model = make_model(res, lives, train, train, ds["binning"])
    out = run_pipeline(s1[held], cfg["k"], model, ds)
    pub = publish(out, model, out["states"])
    return out, pub, model


# ============================================================================ main
def main():
    t0 = time.time()
    RES.mkdir(parents=True, exist_ok=True)
    OUT_DEV.write_text(json.dumps(dict(written=datetime.now().isoformat(timespec="seconds"),
                                       note="written before any D / candidate result is computed",
                                       deviations=DEVIATIONS), indent=2), encoding="utf-8")
    feats = c.load_features(); gt = c.load_gt()
    checks = {"frozen_hashes": h.check_frozen(), "gt_recompute_equals_frozen": h.verify_gt(gt)}
    assert all(checks["frozen_hashes"].values()) and checks["gt_recompute_equals_frozen"], checks
    bs = sorted(feats)
    flags = h.bearing_flags(gt)

    # references needed for eligibility (frozen definitions)
    ev_dum = {nm: h.evaluate(nm, st, feats, gt, k=3) for nm, st in c.dummy_states(feats, k=3).items()}
    dummy_pb = {nm: e["per_bearing"] for nm, e in ev_dum.items()}
    base = h.p1_baselines(feats, gt)
    ev_ref, ref_inp = {}, {}
    for nm, fn in (("R0", "baseline_oof.csv"), ("R1", "combined_oof.csv")):
        inp = h.reference_inputs(c.ROOT / "experiments/xjtu/results" / fn); ref_inp[nm] = inp
        ev_ref[nm] = h.evaluate(nm, inp["states"], feats, gt, rul_pred=inp["rul_pred"], q_lo=inp["q_lo"],
                                q_hi=inp["q_hi"], prob=inp["prob"], baselines=base,
                                interval_note=f"served band pred +/- e90 ({inp['e90']:.2f})")
    r0_pb = ev_ref["R0"]["per_bearing"].copy()
    r0_pb["demotions"] = r0_pb["demotions"]
    frozen45 = pd.read_csv(V2 / "results/dummies/summary.csv").set_index("model")
    checks["R0_C_matches_4.5"] = bool(np.isclose(r0_pb.C.mean(), frozen45.at["R0", "C"], atol=1e-9))

    # stage-1 cache: all 24 configs on all 15 bearings (rules have no fitted parameters)
    s1_grid = h.stage1_grid()
    s1_cache = {cfg["name"]: stage1_states(feats, cfg) for cfg in s1_grid}
    s1_frames = {nm: per_bearing_frame({b: v["st"] for b, v in s.items()}, feats, gt, bs)
                 for nm, s in s1_cache.items()}
    D_cfg = next(x for x in s1_grid if x["name"] == "ratio1.3/k3/self")
    DP_cfg = next(x for x in s1_grid if x["name"] == "ratio1.2/k5/self")
    D_ds = next(x for x in h.downstream_grid() if all(x[k] == v for k, v in h.D_DOWNSTREAM.items()))
    D_s1_cand = dict(cfg=D_cfg, name=D_cfg["name"])

    # self-checks specific to this script
    P_chk = c.score({b: s1_cache[D_cfg["name"]][b]["st"] for b in bs}, feats, gt, w_ep=3.0)
    checks["C_w3_derivation_exact"] = bool(np.allclose(P_chk.C.to_numpy(), s1_frames[D_cfg["name"]].C_w3.to_numpy()))
    checks["onset_only_equals_D_stage1"] = bool(np.allclose(s1_frames[D_cfg["name"]].C1, dummy_pb["onset_only"].C1))
    tr = bs[1:]
    res_chk, lives_chk = loo_residuals(feats, s1_cache[D_cfg["name"]], tr)
    for binning in h.BINNING:
        m = make_model(res_chk, lives_chk, tr, tr, binning)
        cb = [b for b in tr if b in res_chk]
        e = np.concatenate([res_chk[b][0] for b in cb]); g = np.concatenate([np.full(len(res_chk[b][0]), b) for b in cb])
        pc = np.concatenate([res_chk[b][1] for b in cb])
        test = np.linspace(0, 300, 301)
        ref = h.conformal_point(pc, np.expm1(e + np.log1p(pc)), g, test, 0.1, binning)
        got = m.bounds(test, 0.1)
        checks[f"cached_conformal_equals_harness_{binning}"] = bool(
            all(np.allclose(a, b_, equal_nan=True) for a, b_ in zip(ref[:3], got[:3])) and np.array_equal(ref[3], got[3]))
    log(f"self-checks: {checks}")
    assert all(v for k, v in checks.items() if k != "frozen_hashes"), checks

    # ------------------------------------------------------------------ fixed references FIRST: D, D'
    def fixed_outer(cfg, ds, name):
        states, rp, lo, hi, prob, reasons, p0, kk = {}, {}, {}, {}, {}, {}, {}, {}
        for _, held, train in h.outer_folds(bs):
            out, pub, model = apply_fixed(feats, s1_cache, train, held, cfg, ds)
            st = out["states"]; states[held] = st
            # p0 on the training bearings: inner-OOF replay of this same configuration
            res, lives = loo_residuals(feats, s1_cache[cfg["name"]], train)
            inner_st = {}
            for i in train:
                oth = [b for b in train if b != i]
                inner_st[i] = run_pipeline(s1_cache[cfg["name"]][i], cfg["k"],
                                           make_model(res, lives, oth, oth, ds["binning"]), ds)["states"]
            p0[held] = p0_rate(feats, inner_st, train)
            rp[held], lo[held], hi[held] = pub["q50"], pub["lo"], pub["hi"]
            prob[held] = np.where(st >= 1, pub["prob_on"], p0[held])
            reasons[held] = dict(crit_reason=out["crit_reason"], faulty_reason=out["faulty_reason"])
            kk[held] = cfg["k"]
        ev = h.evaluate(name, states, feats, gt, k=cfg["k"], rul_pred=rp, q_lo=lo, q_hi=hi, prob=prob,
                        reasons=reasons, p0=p0, baselines=base,
                        interval_note="TSO bearing-grouped log1p conformal, alpha 0.1")
        ev["k_per_bearing"] = kk
        return ev

    log("computing D first (PROTOCOL A.20)")
    ev_D = fixed_outer(D_cfg, D_ds, "D")
    log(f"D: C={ev_D['per_bearing'].C.mean():.3f}")
    ev_DP = fixed_outer(DP_cfg, D_ds, "D'")
    log(f"D': C={ev_DP['per_bearing'].C.mean():.3f}")

    # ------------------------------------------------------------------ nested LOBO
    grid = downstream_tso_grid()
    assert len(grid) == 96 and any(x["grid_index"] == D_ds["grid_index"] for x in grid)
    folds, pilot_s = [], None
    nested = {"N_TSO": dict(states={}, rp={}, lo={}, hi={}, prob={}, reasons={}, p0={}, k={}),
              "N_S1": dict(states={}, rp={}, lo={}, hi={}, prob={}, reasons={}, p0={}, k={})}
    shrink = []

    def nested_fold(train, held=None, fold=None):
        sel1, c1 = select_stage1(train, s1_frames, dummy_pb, D_s1_cand)
        cfg = sel1["choice"]["cfg"]
        s1 = s1_cache[cfg["name"]]
        cands, res, lives = inner_replays(feats, gt, train, s1, cfg, grid, r0_pb.loc[train],
                                          {k: v.loc[train] for k, v in dummy_pb.items()})
        D_full = dict(ds=D_ds, per_bearing=None, S2=None, fallback_full_D=True)
        sel2 = h.select(cands, metric="C", fallback=D_full)
        ds_choice = sel2["choice"]["ds"]
        fell = sel2["fell_back"]
        cfg_used = D_cfg if fell else cfg
        s1_pick = sorted(c1, key=lambda x: x["per_bearing"].C1.mean())[:3]
        info = dict(fold=fold, held_out=held, stage1_choice=cfg["name"], stage1_fell_back=sel1["fell_back"],
                    stage1_n_eligible=sel1["n_eligible"], stage1_tie_size=sel1["tie_size"],
                    stage1_inner_C1_choice=float(sel1["choice"]["per_bearing"].C1.mean()) if not sel1["fell_back"] else None,
                    stage1_inner_best3=[(x["name"], round(float(x["per_bearing"].C1.mean()), 4)) for x in s1_pick],
                    stage1_inner_C1_D=float(s1_frames[D_cfg["name"]].loc[train].C1.mean()),
                    downstream_choice=ds_name(ds_choice), downstream_fell_back_to_full_D=fell,
                    downstream_n_eligible=sel2["n_eligible"], downstream_tie_size=sel2["tie_size"],
                    inner_C_choice=(float(sel2["choice"]["per_bearing"].C.mean()) if not fell else None),
                    inner_C_best=sel2.get("best_mean"),
                    inner_C_D_downstream=float(next(x for x in cands if x["grid_index"] == D_ds["grid_index"])
                                               ["per_bearing"].C.mean()),
                    inner_D_eligible=bool(next(x for x in cands if x["grid_index"] == D_ds["grid_index"])["eligible"]),
                    stage1_used=cfg_used["name"], downstream_used=ds_name(ds_choice))
        # p0 for the chosen pipeline: its inner-OOF replay states (if fell back, D's)
        if fell:
            sD = s1_cache[D_cfg["name"]]; candsD, _, _ = inner_replays(feats, gt, train, sD, D_cfg, [D_ds],
                                                                         r0_pb.loc[train],
                                                                         {k: v.loc[train] for k, v in dummy_pb.items()})
            p0_tso = p0_rate(feats, candsD[0]["states"], train)
        else:
            p0_tso = p0_rate(feats, sel2["choice"]["states"], train)
        p0_s1 = p0_rate(feats, next(x for x in cands if x["grid_index"] == D_ds["grid_index"])["states"], train)
        return dict(info=info, cfg_tso=cfg_used, ds_tso=ds_choice, cfg_s1=cfg, p0_tso=p0_tso, p0_s1=p0_s1)

    for fold, held, train in h.outer_folds(bs):
        tf = time.time()
        r = nested_fold(train, held, fold)
        for key, cfg, ds, p0v in (("N_TSO", r["cfg_tso"], r["ds_tso"], r["p0_tso"]),
                                  ("N_S1", r["cfg_s1"], D_ds, r["p0_s1"])):
            out, pub, _ = apply_fixed(feats, s1_cache, train, held, cfg, ds)
            st = out["states"]; A = nested[key]
            A["states"][held] = st; A["rp"][held] = pub["q50"]; A["lo"][held] = pub["lo"]; A["hi"][held] = pub["hi"]
            A["prob"][held] = np.where(st >= 1, pub["prob_on"], p0v); A["p0"][held] = p0v; A["k"][held] = cfg["k"]
            A["reasons"][held] = dict(crit_reason=out["crit_reason"], faulty_reason=out["faulty_reason"])
        P_held = c.score({held: nested["N_TSO"]["states"][held]}, feats, gt, bearings=[held])
        r["info"]["outer_C_heldout_N_TSO"] = float(P_held.C.iloc[0])
        folds.append(r["info"])
        el = time.time() - tf
        log(f"fold {fold} ({held}): s1={r['info']['stage1_choice']} ds={r['info']['downstream_choice']} "
            f"fellback={r['info']['downstream_fell_back_to_full_D']} {el:.1f}s")
        if fold == 0:
            pilot_s = el
            proj_h = (el * 16 + (time.time() - t0)) / 3600
            log(f"pilot projection {proj_h:.3f} h (cap {TIME_CAP_H} h, shrink at {SHRINK_AT_H} h)")
            if proj_h > SHRINK_AT_H:
                raise SystemExit("pilot projects > 2.5 h: apply the pre-set shrink order before continuing")
        if (time.time() - t0) / 3600 > TIME_CAP_H:
            raise SystemExit("3 h hard cap exceeded")

    ev_N = {}
    for key, A in nested.items():
        ev = h.evaluate(key, A["states"], feats, gt, k=None, rul_pred=A["rp"], q_lo=A["lo"], q_hi=A["hi"],
                        prob=A["prob"], reasons=A["reasons"], p0=A["p0"], baselines=base,
                        interval_note="TSO bearing-grouped log1p conformal, alpha 0.1")
        per = ev["per_bearing"]
        for b in per.index:                       # DEVIATIONS[7]: per-bearing k for O1
            te, tl = c.gt_variant_rows(gt, b, len(feats[b]), "primary")
            ref_late = max(tl, c.COMMISSION + A["k"][b] - 1)
            tdet = per.at[b, "t_det_row"]
            per.at[b, "O1_delay_min"] = (np.nan if pd.isna(tdet) else 0 if te <= tdet <= ref_late
                                         else (tdet - ref_late if tdet > ref_late else tdet - te))
        ev["summary"] = h.summarize(per, gt); ev["k_per_bearing"] = A["k"]
        ev_N[key] = ev

    # ------------------------------------------------------------------ modal run on all 15 bearings
    log("modal run (selection on all 15 bearings)")
    modal = nested_fold(bs, None, "all15")["info"]

    # ------------------------------------------------------------------ comparison, gates, decision
    evs = {"N_TSO": ev_N["N_TSO"], "N_S1": ev_N["N_S1"], "D": ev_D, "D'": ev_DP, "R0": ev_ref["R0"], "R1": ev_ref["R1"]}
    pairs = (("N_TSO", "D"), ("N_TSO", "R0"), ("N_S1", "D"), ("N_S1", "R0"), ("D", "R0"), ("D'", "R0"),
             ("N_TSO", "D'"), ("N_TSO", "R1"), ("N_S1", "N_TSO"))
    cmp = h.compare(evs, pairs=pairs, dummies=ev_dum, show=True)
    G = {k: h.gate_G(v) for k, v in cmp["paired"].items()}
    PG = {nm: h.gates_P(evs[nm], ev_ref["R0"], ev_D, ev_dum) for nm in ("N_TSO", "N_S1", "D", "D'")}
    elig_outer = {nm: h.eligibility(evs[nm]["per_bearing"], ev_ref["R0"]["per_bearing"], dummy_pb)
                  for nm in ("N_TSO", "N_S1", "D", "D'")}

    def decide(nname):
        if PG[nname]["passed"] and G[f"{nname}-D"]["passed"]:
            return f"{nname} passes the 4.4 rule (track-level; see deviation 3)"
        if PG["D"]["passed"]:
            return "D ships" + ("" if PG["D'"]["passed"] else " (conditional on defaults chosen with all bearings visible)")
        failed = {f"P({nname})": PG[nname]["failed"], f"G({nname}>D)": G[f"{nname}-D"]["G_c_failed"]
                  + ([] if G[f"{nname}-D"]["G_a"] else ["G_a"]) + ([] if G[f"{nname}-D"]["G_b"] else ["G_b"]),
                  "P(D)": PG["D"]["failed"], "P(D')": PG["D'"]["failed"]}
        return dict(decision="nothing ships", failed_conditions=failed)

    decision = {"N_TSO": decide("N_TSO"), "N_S1": decide("N_S1")}

    # per-bearing table
    rows = []
    for b in bs:
        r = dict(bearing=b, gt_early_rul=gt.at[b, "gt_early_rul"], gt_late_rul=gt.at[b, "gt_late_rul"],
                 **{f"flag_{k}": flags.at[b, k] for k in ("baseline_contaminated", "short_healthy", "abrupt", "short_life")})
        for nm in ("N_TSO", "N_S1", "D", "D'", "R0", "R1"):
            P = evs[nm]["per_bearing"]
            for col in ("C", "LC", "MC", "EP", "ECh", "FD", "FDh", "LD", "EFh", "det_lead", "O1_delay_min",
                        "crit_lead", "crit_bin", "crit_reason", "faulty_lead", "P1_mae_own", "q50_at_onset", "q10_at_onset"):
                if col in P:
                    r[f"{nm}:{col}"] = P.at[b, col]
        f = next((x for x in folds if x["held_out"] == b), {})
        r["N_TSO:stage1"] = f.get("stage1_used"); r["N_TSO:downstream"] = f.get("downstream_used")
        r["N_S1:stage1"] = f.get("stage1_choice")
        rows.append(r)
    PB = pd.DataFrame(rows).set_index("bearing")
    PB.to_csv(OUT_PB)

    def vs_refs(nm):
        out = {}
        for ref in ("D", "R0", "D'", "R1"):
            if ref == nm:
                continue
            k = f"{nm}-{ref}"
            pr = cmp["paired"].get(k) or h.paired(evs[nm], evs[ref])
            out[ref] = dict(mean_diff=pr["mean"], ci95=pr["ci95"], signflip_p=pr["signflip_p"], w3=pr["w3"],
                            w10=pr["w10"], unflagged8=pr["unflagged8"], flagged7=pr["flagged7"],
                            gt_variants={v: pr[f"gt_{v}"] for v in h.GATE_VARIANTS + h.REPORT_VARIANTS},
                            influence=pr["influence"], per_bearing=pr["per_bearing"], by_condition=pr["by_condition"],
                            abrupt_true=pr["abrupt_true"], short_life_true=pr["short_life_true"],
                            G=h.gate_G(pr))
        return out

    stab = pd.DataFrame(folds)
    doc = dict(
        name="onset-statistical", protocol="experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2, frozen)",
        script="experiments/xjtu/v2/build_onset-statistical.py", run_at=datetime.now().isoformat(timespec="seconds"),
        runtime_s=round(time.time() - t0, 1), pilot_fold_s=pilot_s, grid_shrink_applied=shrink,
        self_checks=checks, deviations=DEVIATIONS, pre_onset=PRE_ONSET,
        candidate_space=dict(stage1=[x["name"] for x in s1_grid], downstream_TSO_slice_size=len(grid),
                             not_searched=["ET (4 configs)", "HGB-q (2 configs)", "CUSUM/EWMA (not in protocol)"]),
        primary_table=cmp["table"].round(4).reset_index().to_dict("records"),
        summaries={nm: e["summary"] for nm, e in evs.items()},
        dummy_summaries={nm: e["summary"] for nm, e in ev_dum.items()},
        legacy_120_comparison_only={nm: e.get("legacy_120") for nm, e in evs.items()},
        rul_comparison_only={nm: e.get("rul") for nm, e in evs.items()},
        coverage_descriptive={nm: e.get("coverage") for nm, e in evs.items()},
        paired=cmp["paired"], G=G, P=PG, eligibility_outer=elig_outer,
        vs_references={nm: vs_refs(nm) for nm in ("N_TSO", "N_S1", "D", "D'")},
        decision=decision,
        selection=dict(per_fold=folds, modal_all15=modal,
                       stage1_choice_counts=stab.stage1_choice.value_counts().to_dict(),
                       downstream_choice_counts=stab.downstream_used.value_counts().to_dict(),
                       folds_fell_back_to_D=stab.held_out[stab.downstream_fell_back_to_full_D].tolist(),
                       inner_vs_outer_gap_N_TSO=dict(
                           mean_inner_C_choice=float(pd.to_numeric(stab.inner_C_choice).mean()),
                           mean_outer_C_heldout=float(stab.outer_C_heldout_N_TSO.mean()))),
        per_bearing_csv=str(OUT_PB.relative_to(c.ROOT)).replace("\\", "/"),
    )
    OUT_JSON.write_text(json.dumps(h.jsonable(doc), indent=2, default=str), encoding="utf-8")
    log(f"wrote {OUT_JSON} in {time.time() - t0:.1f}s")
    for nm in ("N_TSO", "N_S1", "D", "D'"):
        log(f"P({nm}) failed={PG[nm]['failed']}")
    log(f"decision: {json.dumps(h.jsonable(decision), default=str)}")


if __name__ == "__main__":
    main()
