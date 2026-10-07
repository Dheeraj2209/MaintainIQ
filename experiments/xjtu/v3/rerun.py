"""Re-run of the FROZEN v2 protocol on XJTU-SY + FEMTO/PRONOSTIA (v3).

Protocol: experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2), unchanged: causal.py cost/replay/statistics,
onset-GT rules (v3 copy, XJTU rows identical), 24 stage-1 x 672 downstream candidate space, factorized
nested LOBO (4.3), paired 1-SE rule, eligibility E1-E3, gates G/P1-P6, decision order 4.4.
Deviations forced by the new data: experiments/xjtu/v3/DEVIATIONS.md (written before any pass below ran).

Nothing is re-implemented: the frozen evaluator (causal.py), harness2.py and the v2 stage-2 module
(build_post-onset-rul.py, as `bp`) are imported through pooled_env.py, which only redirects data sources.

Parts (repo root):
  python experiments/xjtu/v3/rerun.py --part pooled --shard 0 --nshards 3   # (a) nested LOBO over 32, one shard
  python experiments/xjtu/v3/rerun.py --part modal                          # all-32 selection (configuration that would ship)
  python experiments/xjtu/v3/rerun.py --part transfer --direction XF        # (b) select+refit on XJTU, apply to FEMTO
  python experiments/xjtu/v3/rerun.py --part transfer --direction FX        # (b) select+refit on FEMTO, apply to XJTU
  python experiments/xjtu/v3/rerun.py --part report                         # results/rerun.json + per-bearing CSV
"""
from __future__ import annotations

import os
import sys

_argv = list(sys.argv)


def _arg(name, default=None):
    return _argv[_argv.index(name) + 1] if name in _argv else default


PART = _arg("--part", "report")
NJ = int(_arg("--n-jobs", "1"))
os.environ["OMP_NUM_THREADS"] = str(NJ)

import json  # noqa: E402
import pickle  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402
from threadpoolctl import threadpool_limits  # noqa: E402

V3 = Path(__file__).resolve().parent
sys.path.insert(0, str(V3))
import pooled_env as E  # noqa: E402

c, h = E.c, E.h
bp = E.load_bp(NJ)
RES = V3 / "results"
RC = V3 / "cache" / "rerun"
T0 = time.time()
SEL_SEED_OFFSET = 42


def log(*a):
    print(f"[{(time.time() - T0) / 60:7.1f} min]", *a, flush=True)


def dev_guard():
    p = V3 / "DEVIATIONS.md"
    assert p.exists() and "V1" in p.read_text(encoding="utf-8"), "DEVIATIONS.md must exist before any pass"
    assert all(h.check_frozen().values()), "frozen artefacts changed"


# ============================================================================ (a) pooled nested LOBO
def part_pooled(shard: int, nshards: int):
    dev_guard()
    E.set_R0F(bp, "pooled", bp.BS)
    for i, b, tr in bp.FOLDS_DEF:
        if i % nshards != shard:
            continue
        out = RC / f"pooled_fold_{i:02d}.pkl"
        if out.exists():
            continue
        t = time.time()
        sel = bp.select_stage1(tr)
        r = dict(bp.run_fold(i, b, tr, sel["choice"]["name"], bp.FULL_GRID, "full"))
        r.update(stage1_n_eligible=sel["n_eligible"], stage1_tie_size=sel["tie_size"],
                 stage1_fell_back=sel["fell_back"], seed=42 + i)
        bp.F_MEMO.clear(); bp.FOLD_MEMO.clear()
        with open(out, "wb") as fh:
            pickle.dump(r, fh)
        log(f"pooled fold {i} {b}: {r['stage1']} | {r.get('choice', 'D fallback')} | elig {r['n_eligible']} "
            f"tie {r['tie_size']} | {time.time() - t:.0f}s")


def part_modal():
    dev_guard()
    E.set_R0F(bp, "pooled", bp.BS)
    sel = bp.select_stage1(bp.BS)
    F = bp.FoldStage2(bp.BS, bp.spec_by_key(sel["choice"]["name"]), 42 + len(bp.BS), need_refit=False)
    s = h.select(bp.eval_inner(F, bp.FULL_GRID), "C", fallback=None)
    modal = dict(stage1=sel["choice"]["name"], stage1_n_eligible=sel["n_eligible"], stage1_tie=sel["tie_size"],
                 downstream=None if s["fell_back"] else bp.d_name(s["choice"]["cfg"]), fell_back_to_D=s["fell_back"],
                 n_eligible=s["n_eligible"], tie_size=s["tie_size"], inner_C_32fold=s.get("choice_mean"))
    with open(RC / "modal.pkl", "wb") as fh:
        pickle.dump(modal, fh)
    log("modal:", modal)


# ============================================================================ (b) transfer
def _fixed_on(S, s1key, d, T, seed):
    F = bp.FoldStage2(S, bp.spec_by_key(s1key), seed, need_refit=True)
    F.ensure(d["stage2_index"])
    st_tr = {i: bp.d_states_for(F, d, i, i)["states"] for i in S}
    p0 = bp.p0_from(st_tr, S)
    return {b: dict(held_out=b, stage1=s1key, choice=bp.d_name(d), p0=p0, pub=bp.outer_publish(F, d, b, p0)) for b in T}


def part_transfer(direction: str):
    dev_guard()
    S, T, r0key = (E.XJTU, E.FEMTO, "xjtu_lobo") if direction == "XF" else (E.FEMTO, E.XJTU, "femto_lobo")
    E.set_R0F(bp, r0key, S)
    seed = SEL_SEED_OFFSET + len(S)
    t = time.time()
    sel = bp.select_stage1(S)
    s1key = sel["choice"]["name"]
    F = bp.FoldStage2(S, bp.spec_by_key(s1key), seed, need_refit=True)
    cands = bp.eval_inner(F, bp.FULL_GRID)
    s = h.select(cands, "C", fallback=None)
    res = dict(direction=direction, source=S, target=T, stage1=s1key, stage1_n_eligible=sel["n_eligible"],
               stage1_tie=sel["tie_size"], n_eligible=s["n_eligible"], tie_size=s["tie_size"], fell_back=s["fell_back"],
               seed=seed)
    if s["fell_back"]:
        Nres = _fixed_on(S, bp.D_KEY, bp.D_DOWN, T, seed)
        res["choice"] = "D (fallback, 4.2)"
    else:
        ch = s["choice"]; d = ch["cfg"]
        p0 = bp.p0_from(ch["states"], S)
        Nres = {b: dict(held_out=b, stage1=s1key, choice=bp.d_name(d), p0=p0, pub=bp.outer_publish(F, d, b, p0))
                for b in T}
        res.update(choice=bp.d_name(d), inner_best_C=s["best_mean"], inner_choice_C=s["choice_mean"])
    res["N"] = Nres
    log(f"{direction}: stage1 {s1key} | {res['choice']} | elig {s['n_eligible']} tie {s['tie_size']} | "
        f"{time.time() - t:.0f}s")
    res["D"] = _fixed_on(S, bp.D_KEY, bp.D_DOWN, T, seed)
    res["Dp"] = _fixed_on(S, bp.DP_KEY, bp.D_DOWN, T, seed)
    with open(RC / f"transfer_{direction}.pkl", "wb") as fh:
        pickle.dump(res, fh)
    log(f"{direction} done")


# ============================================================================ report helpers
FA_DOC = ("failure-anchored, GT-independent subtotal: 10*LC + w_EP*EP + 0.5*min(ECh,4) + 0.25*min(EFh,4) "
          "(the C terms that do not use the onset GT)")


def add_fa(per: pd.DataFrame) -> pd.DataFrame:
    for w in c.W_EP_SENS:
        per[f"FA_w{int(w)}"] = (c.W_LC * per.LC + w * per.EP + c.W_EC * per.ECh + c.W_EF * per.EFh)
    per["dataset"] = np.where(per.index.str.startswith("FEMTO_"), "FEMTO", "XJTU")
    return per


def ref_eval(name, key, bearings, baselines):
    inp = h.reference_inputs(E.R0_FILES[key])
    sub = lambda dct: {b: dct[b] for b in bearings}  # noqa: E731
    ev = h.evaluate(name, sub(inp["states"]), bp.FEATS, bp.GT, k=None, rul_pred=sub(inp["rul_pred"]),
                    q_lo=sub(inp["q_lo"]), q_hi=sub(inp["q_hi"]), prob=sub(inp["prob"]), baselines=baselines,
                    interval_note=f"served band pred +/- e90 ({inp['e90']:.2f})")
    ev["states"] = sub(inp["states"]); ev["rul_pred"] = sub(inp["rul_pred"])
    add_fa(ev["per_bearing"])
    return ev


def assemble(name, res_list, baselines, fallback_D=None):
    old = bp.BASE
    bp.BASE = baselines
    try:
        ev = bp.assemble(name, res_list, {r["held_out"]: bp.spec_by_key(r["stage1"])["k"] for r in res_list},
                         fallback_D=fallback_D)
    finally:
        bp.BASE = old
    ev["rul_pred"] = {r["held_out"]: (r.get("pub") or fallback_D[r["held_out"]]["pub"])["q50"] for r in res_list}
    add_fa(ev["per_bearing"])
    return ev


def dummies_on(bearings):
    out = {}
    for nm, st in bp.DUMMY_STATES.items():
        ev = h.evaluate(nm, {b: st[b] for b in bearings}, bp.FEATS, bp.GT, k=3)
        ev["states"] = {b: st[b] for b in bearings}
        add_fa(ev["per_bearing"]); out[nm] = ev
    return out


def subset_ev(ev, bearings, name=None):
    """Restrict an evaluate() output to a bearing subset (per-bearing metrics are per bearing; the summary is
    recomputed on the subset)."""
    per = ev["per_bearing"].loc[list(bearings)].copy()
    return dict(model=name or ev["model"], per_bearing=per, summary=h.summarize(per, bp.GT), k=ev.get("k"),
                states={b: ev["states"][b] for b in bearings}, rul_pred={b: ev["rul_pred"][b] for b in bearings}
                if "rul_pred" in ev else None)


def pair_stats(PX, PY, col):
    d = (PX[col] - PY[col]).astype(float)
    return dict(mean=round(float(d.mean()), 4), ci95=[round(x, 4) for x in c.boot_ci(d.to_numpy())],
                signflip_p=round(c.signflip_p(d.to_numpy()), 5), n=int(len(d)),
                n_better=int((d < 0).sum()), n_worse=int((d > 0).sum()))


def fmt_pair(p):
    inf = {k: v for k, v in p["influence"].items() if k != "full"}
    return dict(mean=round(p["mean"], 4), ci95=[round(x, 4) for x in p["ci95"]], signflip_p=round(p["signflip_p"], 5),
                w3=round(p["w3"], 4), w10=round(p["w10"], 4), unflagged=round(p["unflagged8"], 4),
                flagged=round(p["flagged7"], 4), gt_env=round(p["gt_env"], 4), gt_hf=round(p["gt_hf"], 4),
                gt_twophase=round(p["gt_twophase"], 4), gt_v2x=round(p["gt_v2x"], 4),
                loo_min=round(min(inf.values()), 4), loo_max=round(max(inf.values()), 4),
                loo_positive=[k for k, v in inf.items() if v >= 0], by_condition=p["by_condition"])


def model_block(ev):
    P = ev["per_bearing"]; s = ev["summary"]
    blk = dict(C_w5=round(float(P.C_w5.mean()), 4), C_w5_ci95=[round(x, 3) for x in c.boot_ci(P.C_w5.to_numpy())],
               C_w3=round(float(P.C_w3.mean()), 4), C_w10=round(float(P.C_w10.mean()), 4),
               FA_w5=round(float(P.FA_w5.mean()), 4), FA_w5_ci95=[round(x, 3) for x in c.boot_ci(P.FA_w5.to_numpy())],
               FA_w3=round(float(P.FA_w3.mean()), 4), FA_w10=round(float(P.FA_w10.mean()), 4),
               C1=round(float(P.C1.mean()), 4), LC_sum=round(float(P.LC.sum()), 3), MC=int(P.MC.sum()),
               EP=int(P.EP.sum()), ECh=round(float(P.ECh.mean()), 4), FD=int(P.FD.sum()), FDh=round(float(P.FDh.mean()), 4),
               LD=round(float(P.LD.mean()), 4), EFh=round(float(P.EFh.mean()), 4), MO=int(P.MO.sum()),
               MC_bearings=s["MC_bearings"], EP_bearings=s["EP_bearings"], crit_entry=s["F_crit_entry"],
               faulty_entry=s["F_faulty_entry"], O3_missed=s["O3_missed_bearings"], n=int(len(P)))
    return blk


def by_dataset(ev):
    P = ev["per_bearing"]
    out = {}
    for ds in ("XJTU", "FEMTO"):
        bs = list(P.index[P.dataset == ds])
        if bs:
            out[ds] = model_block(dict(per_bearing=P.loc[bs], summary=h.summarize(P.loc[bs].copy(), bp.GT)))
    return out


# post-onset discrimination: RUL < 30 (positive) vs RUL 60-120 (negative), score = -predicted RUL
def discrimination(rul_pred: dict, bearings, own_states: dict | None = None):
    rows = []
    for b in bearings:
        n = len(bp.RUL[b]); rul = bp.RUL[b]; q = np.asarray(rul_pred[b], float)
        _, tl = c.gt_variant_rows(bp.GT, b, n, "primary")
        post = np.arange(n) >= tl
        cls = np.where(rul < 30, 1, np.where((rul >= 60) & (rul <= 120), 0, -1))
        elig = post & (cls >= 0)
        rows.append(pd.DataFrame(dict(b=b, y=cls[elig], q=q[elig])))
    D = pd.concat(rows)
    n_elig = D.groupby("b").y.agg(lambda s: (int((s == 1).sum()), int((s == 0).sum()))).to_dict()
    Dp = D.dropna(subset=["q"])
    out = dict(bearings_with_post_onset_rul60_120_rows=sorted(b for b, (p, ng) in n_elig.items() if ng > 0),
               rows_lt30=int((D.y == 1).sum()), rows_60_120=int((D.y == 0).sum()),
               published_fraction=round(float(len(Dp) / max(len(D), 1)), 3))
    if Dp.y.nunique() == 2:
        w = 1.0 / Dp.groupby(["b", "y"]).y.transform("size")
        out["auc_bearing_class_weighted"] = round(float(roc_auc_score(Dp.y, -Dp.q, sample_weight=w)), 4)
        out["auc_rows"] = round(float(roc_auc_score(Dp.y, -Dp.q)), 4)
        sep = []
        for b, g in Dp.groupby("b"):
            if g.y.nunique() == 2:
                sep.append(float(g.q[g.y == 0].median() - g.q[g.y == 1].median()))
        out["within_bearing_median_q_gap_min"] = (round(float(np.median(sep)), 2) if sep else None)
        out["n_bearings_both_classes"] = len(sep)
        out["median_pred_rul_on_lt30"] = round(float(Dp.q[Dp.y == 1].median()), 2)
        out["median_pred_rul_on_60_120"] = round(float(Dp.q[Dp.y == 0].median()), 2)
    return out


def gates_for(evs, R0, D, Dp, dmy):
    gP = {nm: h.gates_P(evs[nm], R0, D, dmy) for nm in ("N", "D", "D'")}
    pairs = {f"{x} - {y}": h.paired(evs[x], evs[y]) for x, y in
             (("N", "D"), ("N", "R0"), ("D", "R0"), ("D'", "R0"), ("N", "D'"))}
    G = {k: h.gate_G(v) for k, v in pairs.items()}
    if gP["N"]["passed"] and G["N - D"]["passed"]:
        dec = "N ships"
    elif gP["D"]["passed"]:
        dec = "D ships" + ("" if gP["D'"]["passed"] else " (conditional on defaults chosen with all bearings visible)")
    else:
        dec = "nothing ships"
    gnd = G["N - D"]
    failed = dict(N=gP["N"]["failed"] + ([] if gnd["passed"] else
                                         ["G(N better than D): " + ",".join([k for k in ("G_a", "G_b") if not gnd[k]]
                                                                             + gnd["G_c_failed"])]),
                  D=gP["D"]["failed"], Dp=gP["D'"]["failed"])
    gp_doc = {k: dict(passed=v["passed"], failed=v["failed"], P2=v["P2"], P3=v["P3"],
                      P4_detail=dict(E1=v["P4_detail"]["E1"], E2=v["P4_detail"]["E2"],
                                     E2_failed=v["P4_detail"].get("E2_failed")),
                      P1_G_failed=[kk for kk in ("G_a", "G_b") if not v["P1_detail"][kk]] + v["P1_detail"]["G_c_failed"])
              for k, v in gP.items()}
    G_doc = {k: dict(passed=v["passed"], G_a=v["G_a"], G_b=v["G_b"], G_c_failed=v["G_c_failed"]) for k, v in G.items()}
    return dec, failed, gp_doc, G_doc, {k: fmt_pair(v) for k, v in pairs.items()}


def table(evs):
    return {nm: model_block(e) for nm, e in evs.items()}


def per_bearing_rows(evs, run):
    out = []
    keep = ["dataset", "condition", "life_min", "C_w5", "C_w3", "C_w10", "FA_w5", "FA_w3", "FA_w10", "C1", "LC", "MC",
            "EP", "ECh", "FD", "FDh", "LD", "EFh", "MO", "MO_scorable", "crit_lead", "faulty_lead", "det_lead",
            "crit_bin", "faulty_bin", "crit_reason", "O1_delay_min", "fpt_row", "q50_at_onset", "q10_at_onset",
            "P1_mae_common_le60", "P3_late_rate", "baseline_contaminated", "short_healthy", "abrupt", "short_life",
            "demotions"]
    for nm, e in evs.items():
        P = e["per_bearing"]
        out.append(P[[k for k in keep if k in P]].assign(run=run, model=nm).reset_index())
    return out


# ============================================================================ report
def part_report():
    dev_guard()
    t = time.time()
    folds = []
    for i, b, tr in bp.FOLDS_DEF:
        p = RC / f"pooled_fold_{i:02d}.pkl"
        if not p.exists():
            raise FileNotFoundError(p)
        with open(p, "rb") as fh:
            folds.append(pickle.load(fh))
    modal = pickle.load(open(RC / "modal.pkl", "rb")) if (RC / "modal.pkl").exists() else None
    # ---------------- (a) pooled
    E.set_R0F(bp, "pooled", bp.BS)
    Dres = {b: bp.run_fixed(i, b, tr, bp.D_KEY, bp.D_DOWN) for i, b, tr in bp.FOLDS_DEF}
    DPres = {b: bp.run_fixed(i, b, tr, bp.DP_KEY, bp.D_DOWN) for i, b, tr in bp.FOLDS_DEF}
    base = bp.BASE
    evD = assemble("D", list(Dres.values()), base)
    evDp = assemble("D'", list(DPres.values()), base, )
    evN = assemble("N", folds, base, fallback_D=Dres)
    evR0 = ref_eval("R0", "pooled", bp.BS, base)
    dmy = dummies_on(bp.BS)
    evs = {"N": evN, "D": evD, "D'": evDp, "R0": evR0}
    dec, failed, gP, G, pairs = gates_for(evs, evR0, evD, evDp, dmy)
    log("POOLED DECISION:", dec, failed)
    fa_pairs = {f"{x} - {y}": {f"FA_w{int(w)}": pair_stats(evs[x]["per_bearing"], evs[y]["per_bearing"], f"FA_w{int(w)}")
                               for w in c.W_EP_SENS}
                for x, y in (("N", "D"), ("N", "R0"), ("D", "R0"), ("D'", "R0"))}
    # per dataset (strata of the pooled LOBO)
    strata = {}
    for ds, bs in (("XJTU", E.XJTU), ("FEMTO", E.FEMTO)):
        sub = {nm: subset_ev(e, bs) for nm, e in evs.items()}
        strata[ds] = dict(
            table={nm: model_block(e) for nm, e in {**sub, **{k: subset_ev(v, bs) for k, v in dmy.items()}}.items()},
            paired_C_w5={f"{x} - {y}": pair_stats(sub[x]["per_bearing"], sub[y]["per_bearing"], "C_w5")
                         for x, y in (("N", "D"), ("N", "R0"), ("D", "R0"))},
            paired_FA_w5={f"{x} - {y}": pair_stats(sub[x]["per_bearing"], sub[y]["per_bearing"], "FA_w5")
                          for x, y in (("N", "D"), ("N", "R0"), ("D", "R0"))},
            discrimination={nm: discrimination(sub[nm]["rul_pred"], bs) for nm in ("N", "D", "R0")})
    # v2 (XJTU-only data) vs v3 pooled, on the 15 XJTU bearings
    v2pb = pd.read_csv(E.ROOT / "experiments/xjtu/v2/results/final_per_bearing.csv", dtype={"bearing": str})
    v2pb = v2pb.set_index("bearing")
    v2cmp = {}
    for nm in ("N", "D", "R0"):
        a = v2pb[v2pb.model == nm].loc[E.XJTU]
        bpb = evs[nm]["per_bearing"].loc[E.XJTU]
        a = add_fa(a.copy())
        v2cmp[nm] = dict(
            v2_C_w5=round(float(a.C_w5.mean()), 4), v3_C_w5=round(float(bpb.C_w5.mean()), 4),
            v2_FA_w5=round(float(a.FA_w5.mean()), 4), v3_FA_w5=round(float(bpb.FA_w5.mean()), 4),
            v2_MC=int(a.MC.sum()), v3_MC=int(bpb.MC.sum()), v2_EP=int(a.EP.sum()), v3_EP=int(bpb.EP.sum()),
            v2_MC_bearings=sorted(a.index[a.MC == 1]), v3_MC_bearings=sorted(bpb.index[bpb.MC == 1]),
            v2_EP_bearings=sorted(a.index[a.EP == 1]), v3_EP_bearings=sorted(bpb.index[bpb.EP == 1]),
            paired_v3_minus_v2_C_w5=pair_stats(bpb, a, "C_w5"), paired_v3_minus_v2_FA_w5=pair_stats(bpb, a, "FA_w5"),
            crit_lead=pd.DataFrame(dict(v2=a.crit_lead, v3=bpb.crit_lead)).round(1).to_dict("index"))
    tl = pd.read_csv(E.ROOT / "experiments/xjtu/v2/results/final_timeline.csv", dtype={"bearing": str})
    v2q = {b: g.sort_values("cycle").predicted_rul.to_numpy(float) for b, g in tl.groupby("bearing")}
    v2r0 = {b: g.sort_values("cycle").R0_rul_pred.to_numpy(float) for b, g in tl.groupby("bearing")}
    v2cmp["discrimination_v2_XJTU_only"] = dict(N=discrimination(v2q, E.XJTU), R0=discrimination(v2r0, E.XJTU))
    pooled = dict(
        decision=dec, failed_conditions=failed, gates_P=gP, G=G, paired_C=pairs, paired_failure_anchored=fa_pairs,
        table={**table(evs), **{k: model_block(v) for k, v in dmy.items()}}, by_dataset={nm: by_dataset(e) for nm, e in
                                                                                       {**evs, **dmy}.items()},
        strata=strata, v2_vs_v3_on_XJTU=v2cmp, modal_configuration_all32=modal,
        per_fold=bp.fold_table(folds),
        choice_counts=pd.Series([r.get("choice", "D (fallback)") for r in folds]).value_counts().to_dict(),
        stage1_counts=pd.Series([r["stage1"] for r in folds]).value_counts().to_dict(),
        folds_fell_back=[r["held_out"] for r in folds if r.get("fell_back")],
        inner_vs_outer=dict(inner_choice_C_mean=float(np.nanmean([r.get("inner_choice_C", np.nan) for r in folds])),
                            outer_C_mean=float(evN["per_bearing"].C.mean())),
        coverage={nm: e.get("coverage", {}) for nm, e in evs.items()},
        legacy_120_comparison_only={nm: e.get("legacy_120") for nm, e in evs.items()})
    rows = per_bearing_rows({**evs, **dmy}, "pooled_LOBO_32")
    # ---------------- (b) transfer (report only)
    transfer = {}
    for dirn, r0key, tgt in (("XF", "xjtu_to_femto", E.FEMTO), ("FX", "femto_to_xjtu", E.XJTU)):
        p = RC / f"transfer_{dirn}.pkl"
        if not p.exists():
            transfer[dirn] = "missing"; continue
        T = pickle.load(open(p, "rb"))
        src = T["source"]
        tb = {}
        for b in tgt:
            sub = {x: bp.FEATS[x] for x in src + [b]}
            tb[b] = h.p1_baselines(sub, bp.GT)[b]
        Dfb = T["D"]
        tev = {"N": assemble("N", list(T["N"].values()), tb, fallback_D=Dfb), "D": assemble("D", list(Dfb.values()), tb),
               "D'": assemble("D'", list(T["Dp"].values()), tb), "R0": ref_eval("R0", r0key, tgt, tb)}
        tdm = {k: subset_ev(v, tgt) for k, v in dmy.items()}
        tdec, tfailed, tgP, tG, tpairs = gates_for(tev, tev["R0"], tev["D"], tev["D'"], tdm)
        indomain = {nm: subset_ev(evs[nm], tgt, nm + "_pooledLOBO") for nm in ("N", "D", "R0")}
        cmp_in = {f"{nm}_transfer - {nm}_pooledLOBO": dict(
            C_w5=pair_stats(tev[nm]["per_bearing"], indomain[nm]["per_bearing"], "C_w5"),
            FA_w5=pair_stats(tev[nm]["per_bearing"], indomain[nm]["per_bearing"], "FA_w5")) for nm in ("N", "D", "R0")}
        transfer[dirn] = dict(
            status="REPORT ONLY (not a ship gate)", source=("XJTU" if dirn == "XF" else "FEMTO"),
            target=("FEMTO" if dirn == "XF" else "XJTU"),
            selection=dict(stage1=T["stage1"], choice=T["choice"], stage1_n_eligible=T["stage1_n_eligible"],
                           stage1_tie=T["stage1_tie"], n_eligible=T["n_eligible"], tie_size=T["tie_size"],
                           fell_back=T["fell_back"], inner_choice_C=T.get("inner_choice_C"), seed=T["seed"]),
            decision_if_it_were_gated=tdec, failed_conditions=tfailed, gates_P=tgP, G=tG, paired_C=tpairs,
            paired_failure_anchored={f"{x} - {y}": pair_stats(tev[x]["per_bearing"], tev[y]["per_bearing"], "FA_w5")
                                     for x, y in (("N", "D"), ("N", "R0"), ("D", "R0"))},
            table={**table(tev), **{k: model_block(v) for k, v in tdm.items()}},
            vs_pooled_LOBO_same_bearings=cmp_in,
            pooled_LOBO_same_bearings={nm: model_block(e) for nm, e in indomain.items()},
            discrimination={nm: discrimination(tev[nm]["rul_pred"], tgt) for nm in ("N", "D", "R0")})
        log(f"TRANSFER {dirn}: {tdec}; N C {tev['N']['per_bearing'].C_w5.mean():.3f} D {tev['D']['per_bearing'].C_w5.mean():.3f}"
            f" R0 {tev['R0']['per_bearing'].C_w5.mean():.3f}")
        rows += per_bearing_rows(tev, f"transfer_{dirn}")
    doc = dict(
        protocol="experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2), re-run unchanged on XJTU-SY + FEMTO",
        deviations_file="experiments/xjtu/v3/DEVIATIONS.md",
        deviations=(V3 / "DEVIATIONS.md").read_text(encoding="utf-8").splitlines(),
        frozen_hashes_ok=h.check_frozen(), failure_anchored_definition=FA_DOC,
        bearings=dict(XJTU=E.XJTU, FEMTO=E.FEMTO), signflip=dict(
            exact_n_le_20=True, monte_carlo_draws_n_gt_20=E.MC_SIGNFLIP_DRAWS, seed=E.MC_SIGNFLIP_SEED),
        pooled_LOBO_32=pooled, transfer=transfer, report_seconds=time.time() - t)
    (RES / "rerun.json").write_text(json.dumps(h.jsonable(doc), indent=1), encoding="utf-8")
    pd.concat(rows, ignore_index=True).to_csv(RES / "rerun_per_bearing.csv", index=False)
    log("wrote results/rerun.json and results/rerun_per_bearing.csv")


if __name__ == "__main__":
    RC.mkdir(parents=True, exist_ok=True)
    with threadpool_limits(NJ):
        if PART == "pooled":
            part_pooled(int(_arg("--shard", "0")), int(_arg("--nshards", "1")))
        elif PART == "modal":
            part_modal()
        elif PART == "transfer":
            part_transfer(_arg("--direction"))
        else:
            part_report()
