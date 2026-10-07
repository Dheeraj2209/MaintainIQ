"""Stage-1 track "onset-learned" (N1) under the frozen v2 protocol (PROTOCOL.frozen.md rev 2).

Scope (see results/onset-learned.deviations.txt, written before any result was computed):
- The frozen candidate space has NO supervised onset family (ET-onset removed, A.8; nothing may be
  trained on the GT, 1.4.3). The only label-driven stage-1 learning allowed is the nested inner
  selection of the 24 rule configs by inner C1 against the training bearings' offline onset GT
  (4.3 step 1). N1 = that nested stage-1 selection.
- Stage 2 / state thresholds are fixed to D's downstream (hard latch, TSO, global conformal,
  q50 <= 30, S1 2.0, S2 off) to isolate the stage-1 effect (task-directed deviation DV2).

Everything evaluative is imported from the frozen causal.py (via harness2); nothing is re-implemented.
Run from the repo root:  python experiments/xjtu/v2/build_onset-learned.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

V2 = Path(__file__).resolve().parent
sys.path.insert(0, str(V2))
import causal as c  # noqa: E402  frozen evaluator
import harness2 as h  # noqa: E402  protocol harness (imports causal)

OUT_JSON = h.RESULTS / "onset-learned.json"
OUT_CSV = h.RESULTS / "onset-learned_per_bearing.csv"
LEVELS = (0.05, 0.1, 0.5, 0.9, 0.95)


# ============================================================================ stage 1 helpers
def stage1_states(feats, cfg, bearings, ind_cache):
    out = {}
    for b in bearings:
        ind = ind_cache[(b, cfg["ref"])]
        out[b] = c.replay(h.stage1_trigger(cfg, ind), cfg["k"])
    return out


def first_onset(st):
    nz = np.where(np.asarray(st) >= 1)[0]
    return int(nz[0]) if len(nz) else -1


def select_stage1(feats, gt, train, ind_cache, dummy_pb):
    """4.3 step 1: score all 24 stage-1 configs on the training bearings by C1, apply E2 (stage-1
    form), pick by the paired 1-SE rule. Falls back to D's stage 1 if none is eligible."""
    cands = []
    for cfg in h.stage1_grid():
        st = stage1_states(feats, cfg, train, ind_cache)
        pb = c.score(st, feats, gt, bearings=sorted(train))
        el = h.eligibility(pb, None, {k: v.loc[sorted(train)] for k, v in dummy_pb.items()}, stage=1)
        cands.append(dict(cfg=cfg, per_bearing=pb, eligible=el["eligible"], E2=el["E2_items"],
                          complexity=cfg["complexity"], grid_index=cfg["grid_index"], S2=None))
    d_cfg = next(x for x in cands if x["cfg"]["family"] == "ratio" and x["cfg"]["G"] == 1.3
                 and x["cfg"]["k"] == 3 and x["cfg"]["ref"] == "self")
    sel = h.select(cands, metric="C1", fallback=d_cfg)
    table = [dict(name=x["cfg"]["name"], C1=float(x["per_bearing"].C1.mean()), eligible=x["eligible"],
                  grid_index=x["grid_index"]) for x in cands]
    return sel, table


# ============================================================================ stage 2 = TSO (D's downstream)
def tso_fit(feats, fpts, bearings):
    lives = [float(feats[b].rul_minutes.iloc[fpts[b]]) for b in bearings if fpts[b] >= 0]
    return float(np.median(lives)) if lives else np.nan


def tso_calibration(feats, fpts, train):
    """Inner LOBO OOF residual data for the TSO conformal: inner bearing i predicted with L from the
    other 13 training bearings, on its rows at/after its causal FPT (DV5)."""
    pred, y, g = [], [], []
    for i in train:
        if fpts[i] < 0:
            continue
        L = tso_fit(feats, fpts, [t for t in train if t != i])
        rul = feats[i].rul_minutes.to_numpy(float)[fpts[i]:]
        tso = np.arange(len(rul), dtype=float)
        pred.append(np.maximum(0.0, L - tso)); y.append(rul); g.append(np.full(len(rul), i))
    return np.concatenate(pred), np.concatenate(y), np.concatenate(g)


def tso_conformal_predict_fn(L, cal):
    pc, yc, gc = cal

    def f(fpt):
        idx = np.arange(len(fpt), dtype=float)
        on = fpt >= 0
        q50 = np.where(on, np.maximum(0.0, L - (idx - fpt)), np.nan)
        out = {"q50": q50}
        for a in (0.1, 0.05):
            lo, mid, hi, ok = h.conformal_point(pc, yc, gc, np.where(on, q50, 0.0), a, "global")
            out[f"lo_{a}"] = np.where(on, lo, np.nan); out[f"hi_{a}"] = np.where(on, hi, np.nan)
            out[f"lo_ok_{a}"] = ok
        return out
    return f


def p0_rate(feats, train, s1_states):
    """2.3: bearing-equal fleet rate P(RUL <= 120 | O = 0, row >= 20) on the training bearings."""
    rates = []
    for b in train:
        st = s1_states[b]; rul = feats[b].rul_minutes.to_numpy(float)
        m = (st == 0) & (np.arange(len(st)) >= c.COMMISSION)
        if m.any():
            rates.append(float((rul[m] <= h.HORIZON).mean()))
    return float(np.mean(rates)) if rates else np.nan


def run_pipeline(feats, b, cfg, L, cal, p0, ind_cache, pre_onset):
    ind = ind_cache[(b, cfg["ref"])]
    ps = h.pipeline_states(h.stage1_trigger(cfg, ind), cfg["k"], ind, tso_conformal_predict_fn(L, cal),
                           model_crit=("q50", None), S1=2.0, S2=None, hysteresis="latch", pre_onset=pre_onset)
    st = ps["states"]; pr = ps["pred"]; on = st >= 1
    vals = np.vstack([pr["lo_0.05"], pr["lo_0.1"], pr["q50"], pr["hi_0.1"], pr["hi_0.05"]])
    vals = np.where(np.isnan(vals), 0.0, vals)
    prob = np.where(on, h.prob_within(h.HORIZON, LEVELS, vals), p0)
    return dict(states=st, rul=np.where(on, pr["q50"], np.nan), lo=np.where(on, pr["lo_0.1"], np.nan),
                hi=np.where(on, pr["hi_0.1"], np.nan), prob=prob,
                reasons=dict(crit_reason=ps["crit_reason"], faulty_reason=ps["faulty_reason"]))


# ============================================================================ nested LOBO
def nested(feats, gt, ind_cache, dummy_pb, fixed_cfg=None, pre_onset="provisional", name="N1"):
    """Outer LOBO. fixed_cfg=None -> nested stage-1 selection (N1); otherwise a fixed stage 1 (D, D').
    Stage 2 is D's downstream in every case."""
    res = dict(states={}, rul={}, lo={}, hi={}, prob={}, reasons={}, p0={}, folds=[])
    for fold, b, train in h.outer_folds(feats):
        t0 = time.time()
        if fixed_cfg is None:
            sel, table = select_stage1(feats, gt, train, ind_cache, dummy_pb)
            cfg = sel["choice"]["cfg"]
            inner_C1 = float(sel["choice"]["per_bearing"].C1.mean())
            diag = dict(n_eligible=sel["n_eligible"], tie_size=sel["tie_size"], fell_back=sel["fell_back"],
                        best_inner_C1=sel.get("best_mean"), chosen_inner_C1=inner_C1,
                        tie_set_note="tie set = paired 1-SE on C1 over 14 training bearings")
        else:
            cfg = fixed_cfg; diag = {}
            inner_C1 = float(c.score(stage1_states(feats, cfg, train, ind_cache), feats, gt,
                                     bearings=sorted(train)).C1.mean())
        s1_tr = stage1_states(feats, cfg, train, ind_cache)
        fpts = {t: first_onset(s1_tr[t]) for t in train}
        L = tso_fit(feats, fpts, train)
        cal = tso_calibration(feats, fpts, train)
        p0 = p0_rate(feats, train, s1_tr)
        out = run_pipeline(feats, b, cfg, L, cal, p0, ind_cache, pre_onset)
        for k in ("states", "rul", "lo", "hi", "prob", "reasons"):
            res[k][b] = out[k]
        res["p0"][b] = p0
        # DV6 check: stage-1 replay non-healthy set == pipeline non-healthy set under hard latch
        s1_b = stage1_states(feats, cfg, [b], ind_cache)[b]
        dv6 = bool(np.array_equal(s1_b >= 1, out["states"] >= 1))
        outer_C1 = float(c.score({b: out["states"]}, feats, gt, bearings=[b]).C1.iloc[0])
        res["folds"].append(dict(fold=fold, held_out=b, stage1=cfg["name"], L=L, p0=p0,
                                 inner_C1_chosen=inner_C1, outer_C1_heldout=outer_C1,
                                 dv6_stage1_nonhealthy_equals_pipeline=dv6, seconds=round(time.time() - t0, 2),
                                 **diag))
    res["k"] = None  # stage-1 k varies by fold for N1
    return res


def modal_run(feats, gt, ind_cache, dummy_pb):
    """4.3: the configuration that would ship = the same stage-1 procedure on all 15 bearings."""
    sel, table = select_stage1(feats, gt, sorted(feats), ind_cache, dummy_pb)
    return dict(choice=sel["choice"]["cfg"]["name"], n_eligible=sel["n_eligible"], tie_size=sel["tie_size"],
                fell_back=sel["fell_back"], C1_table=sorted(table, key=lambda r: r["C1"]))


def evaluate_run(nm, run, feats, gt, base, k):
    return h.evaluate(nm, run["states"], feats, gt, k=k, rul_pred=run["rul"], q_lo=run["lo"], q_hi=run["hi"],
                      prob=run["prob"], reasons=run["reasons"], p0=run["p0"], baselines=base,
                      interval_note="TSO + global bearing-grouped conformal (alpha 0.1), inner-LOBO residuals")


# ============================================================================ main
def main():
    t_start = time.time()
    fr = h.check_frozen()
    if not all(fr.values()):
        raise SystemExit(f"frozen artefacts changed: {fr}")
    feats = c.load_features(); gt = c.load_gt()
    assert h.verify_gt(gt), "GT recompute != frozen onset_gt.csv"
    ind_cache = {(b, ref): c.indicators(d, ref) for b, d in feats.items() for ref in h.REFS}
    ev_d = {nm: h.evaluate(nm, st, feats, gt, k=3) for nm, st in c.dummy_states(feats, k=3).items()}
    dummy_pb = {nm: c.score(st, feats, gt) for nm, st in c.dummy_states(feats, k=3).items()}
    base = h.p1_baselines(feats, gt)

    def cfg_of(spec):
        return next(x for x in h.stage1_grid() if all(x[k] == v for k, v in spec.items()))

    D_cfg, DP_cfg = cfg_of(h.D_STAGE1), cfg_of(h.DP_STAGE1)
    runs, evs = {}, {}
    # D first (A.20), then D', then N1
    for nm, fixed, k in (("D", D_cfg, 3), ("Dprime", DP_cfg, 5), ("N1_onset-learned", None, None)):
        for po in ("provisional", "none"):
            key = nm if po == "provisional" else f"{nm}__pre_onset_none"
            runs[key] = nested(feats, gt, ind_cache, dummy_pb, fixed_cfg=fixed, pre_onset=po, name=nm)
            evs[key] = evaluate_run(key, runs[key], feats, gt, base, k)
    # references
    for nm, fn in (("R0", "baseline_oof.csv"), ("R1", "combined_oof.csv")):
        inp = h.reference_inputs(h.ROOT / "experiments/xjtu/results" / fn)
        evs[nm] = h.evaluate(nm, inp["states"], feats, gt, k=None, rul_pred=inp["rul_pred"], q_lo=inp["q_lo"],
                             q_hi=inp["q_hi"], prob=inp["prob"], baselines=base,
                             interval_note=f"served band pred +/- e90 ({inp['e90']:.2f})")
    N, D, DP = "N1_onset-learned", "D", "Dprime"
    main_evs = {k: evs[k] for k in (N, D, DP, "R0", "R1")}
    pairs = ((N, D), (N, "R0"), (D, "R0"), (DP, "R0"), (N, "R1"), (N, DP),
             (f"{N}__pre_onset_none", f"{D}__pre_onset_none"), (f"{N}__pre_onset_none", "R0"),
             (f"{D}__pre_onset_none", "R0"))
    cmp = h.compare({**main_evs, **{k: evs[k] for k in evs if k.endswith("__pre_onset_none")}},
                    pairs=pairs, dummies=ev_d, show=True)
    gates = {
        "G_N1_better_than_D": h.gate_G(cmp["paired"][f"{N}-{D}"]),
        "G_N1_better_than_R0": h.gate_G(cmp["paired"][f"{N}-R0"]),
        "G_D_better_than_R0": h.gate_G(cmp["paired"][f"{D}-R0"]),
        "G_Dprime_better_than_R0": h.gate_G(cmp["paired"][f"{DP}-R0"]),
        "P_N1": h.gates_P(evs[N], evs["R0"], evs[D], ev_d),
        "P_D": h.gates_P(evs[D], evs["R0"], evs[D], ev_d),
        "P_Dprime": h.gates_P(evs[DP], evs["R0"], evs[D], ev_d),
    }
    # decision per 4.4 (N1 is not the protocol's N -> rule 1 reported as exploratory only)
    n1_would = bool(gates["P_N1"]["passed"] and gates["G_N1_better_than_D"]["passed"])
    if gates["P_D"]["passed"]:
        dec = "D ships" + ("" if gates["P_Dprime"]["passed"] else
                           " (conditional on defaults chosen with all bearings visible: P(D') fails)")
    else:
        dec = "nothing ships (stage-1 track): P(D) fails " + str(gates["P_D"]["failed"])
    modal = modal_run(feats, gt, ind_cache, dummy_pb)
    folds = pd.DataFrame(runs[N]["folds"])
    stab = Counter(folds.stage1)
    nested_vs_D_identical = {b: bool(np.array_equal(runs[N]["states"][b], runs[D]["states"][b])) for b in sorted(feats)}

    # POST-HOC ORACLE DIAGNOSTIC (not selection, not confirmatory): every stage-1 config with the fixed D
    # downstream scored on the outer bearings, to show whether ANY stage-1 choice could pass E1/P2/P3.
    oracle = []
    for cfg in h.stage1_grid():
        r = nested(feats, gt, ind_cache, dummy_pb, fixed_cfg=cfg, pre_onset="provisional", name=cfg["name"])
        P = c.score(r["states"], feats, gt)
        el = h.eligibility(h.evaluate(cfg["name"], r["states"], feats, gt)["per_bearing"], evs["R0"]["per_bearing"],
                           {k: v["per_bearing"] for k, v in ev_d.items()})
        oracle.append(dict(stage1=cfg["name"], C_w5=float(P.C.mean()), MC=int(P.MC.sum()), EP=int(P.EP.sum()),
                           E1=el["E1"], E2=el["E2"], P2=int(P.EP.sum()) <= 2, P3=int(P.MC.sum()) <= 8))
    oracle = sorted(oracle, key=lambda r: r["C_w5"])
    print("oracle: min EP over 24 stage-1 configs =", min(r["EP"] for r in oracle),
          "| any E1&E2&P2&P3:", any(r["E1"] and r["E2"] and r["P2"] and r["P3"] for r in oracle))

    def ci_table(ev):
        P = ev["per_bearing"]
        return {col: dict(mean=float(P[col].mean()), ci95=c.boot_ci(P[col].to_numpy()))
                for col in ("C_w5", "C_w3", "C_w10", "C1", "LC", "ECh", "FDh", "LD", "EFh")}

    doc = dict(
        track="onset-learned (stage-1 track; stage 2 fixed to D's downstream)",
        protocol="experiments/xjtu/v2/PROTOCOL.frozen.md rev 2", frozen_hashes_ok=fr,
        deviations_file="experiments/xjtu/v2/results/onset-learned.deviations.txt",
        deviations=(V2 / "results/onset-learned.deviations.txt").read_text(encoding="utf-8").splitlines(),
        candidate_space="24 stage-1 rule configs (ratio/dual-gate x self/fallback); no supervised onset family "
                        "exists in the frozen space (A.8)",
        selection="nested LOBO: inner C1 on 14 training bearings, E2 stage-1 form, paired 1-SE on C1 with complexity "
                  "tie-break, fallback D stage 1; stage 2 = TSO + global conformal fixed",
        nested_selection=dict(per_fold=folds.to_dict("records"), stage1_choice_counts=dict(stab),
                              n_folds_fell_back=int(folds.fell_back.sum()),
                              inner_vs_outer_C1_gap=float(folds.outer_C1_heldout.mean() - folds.inner_C1_chosen.mean()),
                              dv6_all=bool(folds.dv6_stage1_nonhealthy_equals_pipeline.all()),
                              N1_states_identical_to_D=nested_vs_D_identical),
        modal_all15=modal,
        primary_table=cmp["table"].round(4).reset_index().to_dict("records"),
        bootstrap_ci_per_bearing={k: ci_table(evs[k]) for k in (N, D, DP, "R0", "R1")},
        paired={k: v for k, v in cmp["paired"].items()},
        gates=gates,
        decision=dict(stage1_track_N1_would_pass_rule1=n1_would,
                      note="N1 is not the protocol's N (downstream fixed, DV2); rule 1 for N1 is exploratory only.",
                      protocol_rule_2_3=dec),
        summaries={k: evs[k]["summary"] for k in evs},
        legacy_120_comparison_only={k: evs[k].get("legacy_120") for k in evs},
        rul_comparison_only={k: evs[k].get("rul") for k in evs},
        coverage={k: evs[k].get("coverage") for k in evs},
        posthoc_oracle_stage1_with_fixed_D_downstream=dict(
            note="POST-HOC diagnostic on outer bearings; NOT used for selection; shows the reach of the stage-1 lever "
                 "alone under the fixed TSO downstream.", rows=oracle,
            min_EP=min(r["EP"] for r in oracle),
            any_config_passes_E1_E2_P2_P3=any(r["E1"] and r["E2"] and r["P2"] and r["P3"] for r in oracle)),
        runtime_seconds=round(time.time() - t_start, 1),
    )
    OUT_JSON.write_text(json.dumps(h.jsonable(doc), indent=2), encoding="utf-8")
    pb = pd.concat({k: evs[k]["per_bearing"] for k in (N, D, DP, "R0", "R1")}, names=["model"])
    pb.to_csv(OUT_CSV)
    print("\nper-fold stage-1 choice:", dict(stab), "fell back:", int(folds.fell_back.sum()))
    print("modal (all 15):", modal["choice"], "tie", modal["tie_size"])
    print("N1 identical to D on", sum(nested_vs_D_identical.values()), "of 15 bearings")
    for g, v in gates.items():
        print(g, "PASS" if v["passed"] else f"FAIL {v.get('failed', '')} {v.get('G_c_failed', '')}"
              f" Ga={v.get('G_a')} Gb={v.get('G_b')}")
    print("decision:", dec, "| N1 rule-1 (exploratory):", n1_would)
    print("wrote", OUT_JSON, f"in {doc['runtime_seconds']} s")


if __name__ == "__main__":
    main()
