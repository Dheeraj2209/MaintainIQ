"""Final two-stage system under the frozen v2 protocol: assembly, primary decision, seed robustness, timeline.

PROTOCOL: experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2). Deviations: results/final.deviations.txt
(written before any run of this script).

Nothing is re-implemented here. Indicators/replay/cost/statistics come from causal.py; conformal,
grids, pipeline states, metrics, eligibility, selection and gates from harness2.py; stage-2
features, learners and the nested fold driver from build_post-onset-rul.py (imported as `bp`).

The system that the protocol selects is the factorized nested procedure of section 4.3:
  stage 1  : 24 rule configs, chosen per fold by inner C1 (paired 1-SE rule)
  stage 2  : 672 downstream combos (hysteresis x 7 learners x binning x model_crit x S1 x S2),
             chosen per fold by inner C on 14-fold inner LOBO with bearing-grouped conformal
  states   : causal.replay (concurrent counters, latch, ratchet)
The primary (seed 42) run of that procedure is build_post-onset-rul --part N (FD1).

Usage (repo root):
  python experiments/xjtu/v2/final.py --part seed --seed-base 1042   # one extra full nested run
  python experiments/xjtu/v2/final.py --part report                  # final.json, timeline, tables
"""
from __future__ import annotations

import os
import sys

_argv = list(sys.argv)
PART = _argv[_argv.index("--part") + 1] if "--part" in _argv else "report"
SEED_BASE = int(_argv[_argv.index("--seed-base") + 1]) if "--seed-base" in _argv else 42
NJ = 2 if PART == "seed" else 3
os.environ["OMP_NUM_THREADS"] = str(NJ)
sys.argv = [sys.argv[0], "--part", "N"]          # bp parses --part at import; keep its confirmatory n_jobs path

import importlib.util  # noqa: E402
import json  # noqa: E402
import pickle  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from threadpoolctl import threadpool_limits  # noqa: E402

V2 = Path(__file__).resolve().parent
sys.path.insert(0, str(V2))
_spec = importlib.util.spec_from_file_location("bp", V2 / "build_post-onset-rul.py")
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)
c, h = bp.c, bp.h
bp.NJOBS = NJ

RES = V2 / "results"
CACHE = V2 / "cache" / "final"
PRIMARY_PKL = V2 / "cache" / "post-onset-rul" / "part_N.pkl"
SEED_BASES = (1042, 2042)
T0 = time.time()
STATE_NAMES = np.array(["healthy", "degrading", "faulty", "critical"])


def log(*a):
    print(f"[{(time.time() - T0) / 60:6.1f} min]", *a, flush=True)


# ============================================================================ seed rerun (FD2)
def part_seed(base: int):
    """Identical nested procedure to bp.part_N (stage 1 + full 672 grid + modal run), seed base `base`."""
    assert all(h.check_frozen().values()), "frozen artefacts changed"
    orig_fit = bp.fit_model
    off = base - 42

    def fit_shifted(si, train, fpts, ref, seed):
        return orig_fit(si, train, fpts, ref, seed + off)

    bp.fit_model = fit_shifted                       # FoldStage2.ensure resolves fit_model at call time
    bp.FOLD_MEMO.clear(); bp.F_MEMO.clear()
    grid = bp.FULL_GRID
    res, s1sel = [], {}
    t = time.time()
    for i, b, tr in bp.FOLDS_DEF:
        sel = bp.select_stage1(tr); s1sel[b] = sel
        r = dict(bp.run_fold(i, b, tr, sel["choice"]["name"], grid, f"seed{base}"))
        r.update(stage1_n_eligible=sel["n_eligible"], stage1_tie_size=sel["tie_size"],
                 stage1_fell_back=sel["fell_back"], seed=42 + i + off)
        res.append(r)
        log(f"seed {base} fold {i} {b}: {r['stage1']} | {r.get('choice', 'D fallback')} | elig {r['n_eligible']} "
            f"tie {r['tie_size']} | {r['seconds']:.0f}s")
        if (time.time() - T0) / 3600 > 3.0:
            raise RuntimeError("3 h cap exceeded")
        bp.F_MEMO.pop((i, sel["choice"]["name"]), None)   # free memory; fold is finished
    n_s = time.time() - t
    sel15 = bp.select_stage1(bp.BS)
    F15 = bp.FoldStage2(bp.BS, bp.spec_by_key(sel15["choice"]["name"]), 42 + 15 + off, need_refit=False)
    s15 = h.select(bp.eval_inner(F15, grid), "C", fallback=None)
    modal = dict(stage1=sel15["choice"]["name"], downstream=None if s15["fell_back"] else bp.d_name(s15["choice"]["cfg"]),
                 fell_back_to_D=s15["fell_back"], n_eligible=s15["n_eligible"], tie_size=s15["tie_size"],
                 inner_C_15fold=s15.get("choice_mean"))
    log("modal:", modal)
    with open(CACHE / f"seed_{base}.pkl", "wb") as fh:
        pickle.dump(dict(Nres=res, modal=modal, seed_base=base, timing=dict(N_s=n_s, total_h=(time.time() - T0) / 3600)),
                    fh)
    log("seed run done")


# ============================================================================ report
def _ev_refs():
    ev_d = {nm: h.evaluate(nm, st, bp.FEATS, bp.GT, k=3) for nm, st in bp.DUMMY_STATES.items()}
    refs = {}
    for nm, fn in (("R0", "baseline_oof.csv"), ("R1", "combined_oof.csv")):
        inp = h.reference_inputs(c.ROOT / "experiments/xjtu/results" / fn)
        refs[nm] = h.evaluate(nm, inp["states"], bp.FEATS, bp.GT, k=None, rul_pred=inp["rul_pred"], q_lo=inp["q_lo"],
                              q_hi=inp["q_hi"], prob=inp["prob"], baselines=bp.BASE,
                              interval_note=f"served band pred +/- e90 ({inp['e90']:.2f})")
        refs[nm]["inputs"] = inp
    assert abs(refs["R0"]["per_bearing"].C.mean() - 6.740277777777777) < 1e-9, "R0 does not reproduce 4.5"
    return ev_d, refs


def _decision(gN, gD, gDp, G_ND):
    if gN["passed"] and G_ND["passed"]:
        return "N ships"
    if gD["passed"]:
        return "D ships" + ("" if gDp["passed"] else " (conditional on defaults chosen with all bearings visible)")
    return "nothing ships"


def _fmt_pair(p):
    return dict(mean=round(p["mean"], 3), ci95=[round(x, 3) for x in p["ci95"]], signflip_p=round(p["signflip_p"], 4),
                w3=round(p["w3"], 3), w10=round(p["w10"], 3), unflagged8=round(p["unflagged8"], 3),
                flagged7=round(p["flagged7"], 3), gt_env=round(p["gt_env"], 3), gt_hf=round(p["gt_hf"], 3),
                gt_twophase=round(p["gt_twophase"], 3), gt_v2x=round(p["gt_v2x"], 3),
                loo_min=round(min(v for k, v in p["influence"].items() if k != "full"), 3),
                loo_max=round(max(v for k, v in p["influence"].items() if k != "full"), 3),
                per_bearing=p["per_bearing"])


def _metric_block(ev):
    """Every protocol metric for one model, each bearing-mean with its per-bearing bootstrap 95% CI."""
    P = ev["per_bearing"]

    def mci(col, how="mean"):
        x = P[col].astype(float).dropna().to_numpy()
        if len(x) == 0:
            return None
        val = float(x.mean()) if how == "mean" else float(x.sum())
        lo, hi = c.boot_ci(x)
        if how == "sum":
            lo, hi = lo * len(x), hi * len(x)
        return dict(value=round(val, 4), ci95=[round(lo, 4), round(hi, 4)], n_bearings=int(len(x)))

    s = ev["summary"]
    out = dict(
        C_w5=mci("C_w5"), C_w3=mci("C_w3"), C_w10=mci("C_w10"), C1=mci("C1"),
        LC_sum=mci("LC", "sum"), MC_events=mci("MC", "sum"), EP_events=mci("EP", "sum"),
        ECh_capped_mean=mci("ECh"), FD_episodes=mci("FD", "sum"), FDh_mean=mci("FDh"), LD_mean_h=mci("LD"),
        EFh_mean=mci("EFh"), MO_events=mci("MO", "sum"),
        C_gt_env=mci("C_env"), C_gt_hf=mci("C_hf"), C_gt_twophase=mci("C_twophase"), C_gt_v2x=mci("C_v2x"),
        C_gt_hinge=mci("C_hinge"), C_gt_on=mci("C_on"),
        O1_delay_min=mci("O1_delay_min"), O4_det_lead_min=mci("det_lead"),
        demotion_fraction=mci("demotion_frac") if "demotion_frac" in P else None,
        MC_bearings=s.get("MC_bearings"), EP_bearings=s.get("EP_bearings"),
        O3_missed_onsets=s.get("O3_missed_onsets"), summary=s)
    for col in ("P1_mae_common", "P1_mae_common_le60", "P1_const_mae_common", "P1_const_mae_common_le60",
                "P1_tso_mae_common_le60", "P3_late_rate", "P2a_cover_at_fpt", "P2b_cover_first_le60",
                "P2_row_coverage", "P2_mean_width", "p0", "p0_obs_frac", "p0_brier"):
        if col in P:
            out[col] = mci(col)
    out["legacy_120_comparison_only"] = ev.get("legacy_120")
    out["coverage"] = ev.get("coverage")
    out["rul_comparison_only"] = ev.get("rul")
    return out


def _timeline(evN, evD, refs, Nres):
    rows = []
    pubs = {r["held_out"]: r for r in Nres}
    for b in bp.BS:
        F = bp.FEATS[b]; n = len(F); g = bp.GT.loc[b]
        te, tl = c.gt_variant_rows(bp.GT, b, n, "primary")
        stN = evN["states"][b]; r = pubs[b]
        pub = r.get("pub") or bp.Dres_cache[b]["pub"]
        crit_r = pub["reasons"]["crit_reason"] if pub["reasons"] else None
        df = pd.DataFrame(dict(
            bearing=b, cycle=F.cycle.to_numpy(), condition=F.condition.to_numpy(), rul_true=bp.RUL[b],
            true_onset=(np.arange(n) >= int(g.gt_on_row)).astype(int),
            gt_interval_started=(np.arange(n) >= te).astype(int), gt_interval_passed=(np.arange(n) >= tl).astype(int),
            state=STATE_NAMES[stN], state_code=stN,
            rul_estimate_kind=np.where(stN >= 1, "post_onset", np.where(np.arange(n) < c.COMMISSION, "warming_up",
                                                                         "unknown_no_onset")),
            predicted_rul=np.round(pub["q50"], 3), interval_lo=np.round(pub["lo"], 3), interval_hi=np.round(pub["hi"], 3),
            failure_within_120_prob=np.round(pub["prob"], 4),
            fold_choice=r.get("choice", "D (fallback, 4.2)"), stage1=r["stage1"],
            D_state=STATE_NAMES[evD["states"][b]],
            R0_state=STATE_NAMES[refs["R0"]["states_arr"][b]],
            R0_rul_pred=np.round(refs["R0"]["inputs"]["rul_pred"][b], 3)))
        df["crit_entry_reason"] = ""
        on = np.where(stN >= 3)[0]
        if len(on) and crit_r:
            df.loc[on[0], "crit_entry_reason"] = str(crit_r)
        rows.append(df)
    return pd.concat(rows, ignore_index=True)


def part_report():
    assert all(h.check_frozen().values()), "frozen artefacts changed"
    with open(PRIMARY_PKL, "rb") as fh:
        A = pickle.load(fh)
    assert A["shrink"] == 0 and A["grid_size"] == 672
    ev_d, refs = _ev_refs()
    for nm in ("R0", "R1"):
        refs[nm]["states_arr"] = refs[nm]["inputs"]["states"]
    Dres, DPres = A["Dres"], A["DPres"]
    bp.Dres_cache = Dres
    evD = bp.assemble("D", list(Dres.values()), {b: 3 for b in bp.BS})
    evDp = bp.assemble("D'", list(DPres.values()), {b: 5 for b in bp.BS})

    def asmN(name, res):
        return bp.assemble(name, res, {r["held_out"]: bp.spec_by_key(r["stage1"])["k"] for r in res}, fallback_D=Dres)

    runs = {42: dict(Nres=A["Nres"], modal=A["modal"])}
    for sb in SEED_BASES:
        p = CACHE / f"seed_{sb}.pkl"
        if p.exists():
            with open(p, "rb") as fh:
                runs[sb] = pickle.load(fh)
        else:
            log(f"WARNING: seed run {sb} missing ({p}); seed robustness incomplete")
    evN = {sb: asmN("N" if sb == 42 else f"N_seed{sb}", r["Nres"]) for sb, r in runs.items()}

    allrefs = {"R0": refs["R0"], "R1": refs["R1"], "D": evD, "D'": evDp}
    gP = {"N": h.gates_P(evN[42], refs["R0"], evD, ev_d), "D": h.gates_P(evD, refs["R0"], evD, ev_d),
          "D'": h.gates_P(evDp, refs["R0"], evD, ev_d)}
    pairs = {}
    for X, Y, ex, ey in (("N", "D", evN[42], evD), ("N", "R0", evN[42], refs["R0"]), ("D", "R0", evD, refs["R0"]),
                         ("D'", "R0", evDp, refs["R0"]), ("N", "R1", evN[42], refs["R1"]), ("N", "D'", evN[42], evDp)):
        pairs[f"{X} - {Y}"] = h.paired(ex, ey)
    G = {k: h.gate_G(v) for k, v in pairs.items()}
    decision = _decision(gP["N"], gP["D"], gP["D'"], G["N - D"])
    gnd_fail = [k for k in ("G_a", "G_b") if not G["N - D"][k]] + G["N - D"]["G_c_failed"]
    failed = dict(N=gP["N"]["failed"] + ([] if G["N - D"]["passed"] else ["G(N better than D): " + ",".join(gnd_fail)]),
                  D=gP["D"]["failed"], Dp=gP["D'"]["failed"])
    log("DECISION (primary seed):", decision, failed)

    # ---- seed robustness
    seeds = {}
    choice_by_seed = pd.DataFrame({sb: {r["held_out"]: f"{r['stage1']} || {r.get('choice', 'D (fallback)')}"
                                        for r in run["Nres"]} for sb, run in runs.items()})
    for sb, e in evN.items():
        g = h.gates_P(e, refs["R0"], evD, ev_d)
        gnd = h.gate_G(h.paired(e, evD))
        P = e["per_bearing"]
        seeds[str(sb)] = dict(
            C_w5=round(float(P.C_w5.mean()), 4), C_w5_ci95=[round(x, 3) for x in c.boot_ci(P.C_w5.to_numpy())],
            C_w3=round(float(P.C_w3.mean()), 4), C_w10=round(float(P.C_w10.mean()), 4),
            MC=int(P.MC.sum()), EP=int(P.EP.sum()), FD=int(P.FD.sum()), MO=int(P.MO.sum()),
            EP_bearings=e["summary"]["EP_bearings"], MC_bearings=e["summary"]["MC_bearings"],
            vs_R0=_fmt_pair(h.paired(e, refs["R0"])), vs_D=_fmt_pair(h.paired(e, evD)),
            gates_failed=g["failed"], G_N_better_than_D=gnd["passed"],
            decision=_decision(g, gP["D"], gP["D'"], gnd),
            modal=runs[sb]["modal"], folds_fell_back=[r["held_out"] for r in runs[sb]["Nres"] if r.get("fell_back")])
    same_choice = (choice_by_seed.nunique(axis=1) == 1)
    seed_summary = dict(
        seed_bases=sorted(runs), per_seed=seeds,
        folds_with_identical_choice_across_seeds=int(same_choice.sum()), n_folds=len(same_choice),
        fold_choices=choice_by_seed.to_dict(orient="index"),
        C_w5_range=[min(s["C_w5"] for s in seeds.values()), max(s["C_w5"] for s in seeds.values())],
        decision_stable=len({s["decision"] for s in seeds.values()}) == 1,
        modal_stable=len({json.dumps(s["modal"].get("downstream")) + s["modal"]["stage1"] for s in seeds.values()}) == 1,
        per_bearing_C_sd_across_seeds=pd.DataFrame({sb: e["per_bearing"].C_w5 for sb, e in evN.items()}).std(axis=1)
        .round(3).to_dict())
    for sb in [s for s in runs if s != 42]:
        seed_summary[f"N_seed{sb} - N_seed42"] = _fmt_pair(h.paired(evN[sb], evN[42]))

    # ---- FD7 exploratory: production model with the protocol ratchet (information only)
    def ratchet(st, latch_degrading=False):
        st = np.asarray(st, int)
        if latch_degrading:
            return np.maximum.accumulate(st)
        esc = np.maximum.accumulate(np.where(st >= 2, st, 0))
        return np.maximum(st, esc)

    inpR0 = refs["R0"]["inputs"]
    explo = {}
    for nm, ld in (("R0_ratchet", False), ("R0_latch_all", True)):
        stx = {b: ratchet(inpR0["states"][b], ld) for b in bp.BS}
        explo[nm] = h.evaluate(nm, stx, bp.FEATS, bp.GT, k=None, rul_pred=inpR0["rul_pred"], q_lo=inpR0["q_lo"],
                               q_hi=inpR0["q_hi"], prob=inpR0["prob"], baselines=bp.BASE)
    explo_doc = {nm: dict(status="EXPLORATORY post-hoc (FD7); not a ship candidate", table=h.table_row(e),
                          EP_bearings=e["summary"]["EP_bearings"], MC_bearings=e["summary"]["MC_bearings"],
                          gates_P=h.gates_P(e, refs["R0"], evD, ev_d), vs_R0=_fmt_pair(h.paired(e, refs["R0"])),
                          demotions=int(e["per_bearing"].demotions.sum()))
                 for nm, e in explo.items()}

    # ---- tables
    evs = {"N": evN[42], "D": evD, "D'": evDp, "R0": refs["R0"], "R1": refs["R1"]}
    cmp = h.compare(evs, pairs=(("N", "D"), ("N", "R0"), ("D", "R0"), ("D'", "R0"), ("N", "R1")), dummies=ev_d,
                    show=True)
    metrics = {nm: _metric_block(e) for nm, e in {**evs, **ev_d}.items()}
    strat = {}
    for nm, (ex, ey) in {"N - D": (evN[42], evD), "D - R0": (evD, refs["R0"]), "N - R0": (evN[42], refs["R0"])}.items():
        pr = pairs[nm]
        strat[nm] = dict(flagged7=pr["flagged7"], unflagged8=pr["unflagged8"], abrupt=pr["abrupt_true"],
                         not_abrupt=pr["abrupt_false"], short_healthy=pr["short_healthy_true"],
                         short_life=pr["short_life_true"], by_condition=pr["by_condition"],
                         influence=pr["influence"])
    PN = evN[42]["per_bearing"]
    per_bearing_view = pd.DataFrame({
        "gt_on_rul": bp.GT.gt_on_rul, "gt_early_rul": bp.GT.gt_early_rul, "gt_late_rul": bp.GT.gt_late_rul,
        "N_choice": choice_by_seed[42], "N_C": PN.C_w5, "D_C": evD["per_bearing"].C_w5,
        "R0_C": refs["R0"]["per_bearing"].C_w5, "N_det_lead": PN.det_lead, "D_det_lead": evD["per_bearing"].det_lead,
        "N_faulty_lead": PN.faulty_lead, "N_crit_lead": PN.crit_lead, "N_crit_reason": PN.crit_reason,
        "D_crit_lead": evD["per_bearing"].crit_lead, "R0_crit_lead": refs["R0"]["per_bearing"].crit_lead,
        "N_FD": PN.FD, "N_q50_at_onset": PN.get("q50_at_onset"), "N_q10_at_onset": PN.get("q10_at_onset"),
        "N_mae_common_le60": PN.get("P1_mae_common_le60"), "flag_contaminated": PN.baseline_contaminated,
        "abrupt": PN.abrupt, "short_life": PN.short_life})

    unavoidable = dict(
        structurally_undetectable_onset_MO_excluded=["1_4", "2_4", "1_5"],
        MC_unavoidable_latency_floor=["1_4", "2_4"] + ["1_5 (k=5 only)"],
        partial_LC_unavoidable=["3_3"],
        LC_floor_sum_k3=round(float(c.latency_floor(bp.GT, 3).get("LC_floor", pd.Series(dtype=float)).sum()), 3)
        if "LC_floor" in c.latency_floor(bp.GT, 3) else None)
    doc = dict(
        protocol="experiments/xjtu/v2/PROTOCOL.frozen.md (rev 2)", frozen_hashes_ok=h.check_frozen(),
        deviations=(RES / "final.deviations.txt").read_text(encoding="utf-8").splitlines(),
        inherited_deviations=(RES / "post-onset-rul.deviations.txt").read_text(encoding="utf-8").splitlines(),
        system=dict(
            procedure="factorized nested LOBO (4.3): stage 1 (24) by inner C1 -> downstream (672) by inner C; "
                      "paired 1-SE rule; refit on 14; D fallback (4.2)",
            primary_run="build_post-onset-rul --part N, seed base 42, full grid, no shrink",
            modal_configuration_all15=A["modal"], per_fold=bp.fold_table(A["Nres"])),
        decision=decision, failed_conditions=failed, gates_P=gP, G=G,
        paired={k: _fmt_pair(v) for k, v in pairs.items()}, metrics=metrics, stratified=strat,
        table=cmp["table"], seed_robustness=seed_summary, exploratory_R0_semantics=explo_doc, irreducible=unavoidable,
        per_bearing=per_bearing_view.reset_index().rename(columns={"index": "bearing"}).to_dict(orient="records"))
    (RES / "final.json").write_text(json.dumps(h.jsonable(doc), indent=2), encoding="utf-8")
    pd.concat([e["per_bearing"].assign(model=nm).reset_index() for nm, e in
               {**evs, **{f"N_seed{sb}": evN[sb] for sb in runs if sb != 42}}.items()]).to_csv(
        RES / "final_per_bearing.csv", index=False)
    tl = _timeline(evN[42], evD, refs, A["Nres"])
    tl.to_csv(RES / "final_timeline.csv", index=False)
    log("wrote final.json, final_per_bearing.csv, final_timeline.csv", len(tl), "timeline rows")
    print(json.dumps(h.jsonable(dict(decision=decision, failed=failed, seeds={k: {kk: v[kk] for kk in (
        "C_w5", "C_w3", "C_w10", "MC", "EP", "gates_failed", "decision", "G_N_better_than_D")} for k, v in seeds.items()},
        same_choice=seed_summary["folds_with_identical_choice_across_seeds"],
        explo={k: dict(C=v["table"]["C_w5"], MC=v["table"]["MC"], EP=v["table"]["EP"], ECh=v["table"]["ECh"],
                       EFh=v["table"]["EFh"], dem=v["demotions"], vsR0=v["vs_R0"]["mean"], failed=v["gates_P"]["failed"])
               for k, v in explo_doc.items()})), indent=1))


if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    with threadpool_limits(NJ):
        if PART == "seed":
            part_seed(SEED_BASE)
        else:
            part_report()
