"""Temporal decision logic over the (unchanged) baseline OOF probabilities.

No refit: the classifier/regressor are the production ones, so the baseline
OOF raw probabilities (experiments/xjtu/results/baseline_oof.csv) ARE the
model output. Every variant is a causal per-bearing rule p[0..t] -> warning[t]
scored through harness.compute_report (postprocess_fn hook).

Families (all strictly causal, state = a few floats/ints per machine):
  med    : rolling-median(w) >= T for P consecutive snapshots   (baseline family)
  ewma   : EWMA(alpha) >= T for P consecutive snapshots
  hyst   : rolling-median(w); ON after P consecutive >= T_on, OFF when < T_on - d
  latch  : like hyst with d = inf (once on, stays on - bearings do not heal)
  cusum  : S_t = max(0, S_{t-1} + p_t - k); warning while S_t >= h

Headline = NESTED leave-one-bearing-out: for each held-out bearing, choose the
family/params maximising pooled F1 on the other 14 bearings' OOF rows, apply to
the held-out bearing. Also per-family nested results. Pooled-OOF "best" values
are reported only as (optimistically biased) references.

RUL: a few a-priori (untuned) causal temporal RUL post-processors are scored on
the baseline OOF rul_pred (regressor unchanged).
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.xjtu.harness import (  # noqa: E402
    compute_report, load_baseline, load_context, save_oof, summary_metrics, compare,
)

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
H = 120.0

ctx = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
oof = pd.read_csv(RES / "baseline_oof.csv").sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
assert (oof.bearing_id.values == ctx.bearing_id.values).all() and (oof.cycle.values == ctx.cycle.values).all()
y = ctx.rul_minutes.to_numpy(float)
P_RAW = oof.raw_prob.to_numpy(float)
RUL = oof.rul_pred.to_numpy(float)
TRUTH = y <= H
BEARINGS = sorted(ctx.bearing_id.unique())
IDX = {b: np.flatnonzero(ctx.bearing_id.values == b) for b in BEARINGS}


# ------------------------------------------------------------ causal scores
def roll_median(p, w):
    return pd.Series(p).rolling(w, min_periods=1).median().to_numpy()


def ewma(p, a):
    out = np.empty_like(p)
    s = p[0]
    for i, v in enumerate(p):
        s = v if i == 0 else a * v + (1 - a) * s
        out[i] = s
    return out


def cusum(p, k):
    out = np.empty_like(p)
    s = 0.0
    for i, v in enumerate(p):
        s = max(0.0, s + v - k)
        out[i] = s
    return out


def persist(above, P):
    if P <= 1:
        return above.copy()
    c = pd.Series(above.astype(int)).rolling(P, min_periods=P).sum().to_numpy()
    return c == P


def hysteresis(score, t_on, t_off, P):
    on_trigger = persist(score >= t_on, P)
    out = np.zeros(len(score), bool)
    state = False
    for i in range(len(score)):
        if not state and on_trigger[i]:
            state = True
        elif state and score[i] < t_off:
            state = False
        out[i] = state
    return out


# ------------------------------------------------------------ variant grid
THS = [0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75]
VARIANTS = []
for w, T, P in itertools.product([1, 3, 5, 9], THS, [1, 3, 5]):
    VARIANTS.append(("med", dict(w=w, T=T, P=P)))
for a, T, P in itertools.product([0.1, 0.2, 0.3, 0.5], THS, [1, 3, 5]):
    VARIANTS.append(("ewma", dict(a=a, T=T, P=P)))
for w, T, P, d in itertools.product([3, 5], THS, [3, 5], [0.1, 0.2, 0.3]):
    VARIANTS.append(("hyst", dict(w=w, T=T, P=P, d=d)))
for w, T, P in itertools.product([3, 5], THS, [3, 5, 10]):
    VARIANTS.append(("latch", dict(w=w, T=T, P=P)))
for k, h in itertools.product([0.4, 0.5, 0.6, 0.7], [0.5, 1, 2, 4, 8]):
    VARIANTS.append(("cusum", dict(k=k, h=h)))


def rule(fam, prm, p):
    if fam == "med":
        return persist(roll_median(p, prm["w"]) >= prm["T"], prm["P"])
    if fam == "ewma":
        return persist(ewma(p, prm["a"]) >= prm["T"], prm["P"])
    if fam == "hyst":
        return hysteresis(roll_median(p, prm["w"]), prm["T"], prm["T"] - prm["d"], prm["P"])
    if fam == "latch":
        return np.maximum.accumulate(persist(roll_median(p, prm["w"]) >= prm["T"], prm["P"]))
    if fam == "cusum":
        return cusum(p, prm["k"]) >= prm["h"]
    raise ValueError(fam)


def vname(fam, prm):
    return fam + "(" + ",".join(f"{k}={v}" for k, v in prm.items()) + ")"


# per-variant per-bearing confusion counts
print(f"{len(VARIANTS)} variants", flush=True)
MASKS = {}
COUNTS = np.zeros((len(VARIANTS), len(BEARINGS), 3), int)  # tp, fp, fn
for vi, (fam, prm) in enumerate(VARIANTS):
    m = np.zeros(len(y), bool)
    for bi, b in enumerate(BEARINGS):
        idx = IDX[b]
        wb = rule(fam, prm, P_RAW[idx])
        m[idx] = wb
        t = TRUTH[idx]
        COUNTS[vi, bi] = [np.sum(wb & t), np.sum(wb & ~t), np.sum(~wb & t)]
    MASKS[vi] = m


def f1_from(c):
    tp, fp, fn = c
    return 2 * tp / max(2 * tp + fp + fn, 1)


OBJECTIVES = {
    "f1": lambda c: f1_from(c),
    "errors": lambda c: -(c[1] + c[2]),  # FA + missed, equal cost
}


def report_for_mask(mask):
    return compute_report(ctx, y, P_RAW, RUL, postprocess_fn=lambda t, p: mask)


def flat(r):
    s = summary_metrics(r)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}


def fw_detail(r):
    return {x["bearing_id"]: x["actual_rul_minutes_at_first_warning"] for x in r["first_warning_events"]["per_bearing"]}


def fa_per_bearing(r):
    return {x["bearing_id"]: x["false_alarm_count"] for x in r["per_bearing"]}


# ------------------------------------------------------------ nested LOBO
def nested(candidate_ids, objective):
    obj = OBJECTIVES[objective]
    mask = np.zeros(len(y), bool)
    chosen = {}
    for bi, b in enumerate(BEARINGS):
        others = [j for j in range(len(BEARINGS)) if j != bi]
        best, best_val = None, -np.inf
        for vi in candidate_ids:
            val = obj(COUNTS[vi, others].sum(axis=0))
            if val > best_val + 1e-12:
                best, best_val = vi, val
        chosen[b] = vname(*VARIANTS[best])
        mask[IDX[b]] = MASKS[best][IDX[b]]
    return mask, chosen


def pooled_best(candidate_ids, objective):
    obj = OBJECTIVES[objective]
    vals = [obj(COUNTS[vi].sum(axis=0)) for vi in candidate_ids]
    return candidate_ids[int(np.argmax(vals))]


fam_ids = {}
for vi, (fam, _) in enumerate(VARIANTS):
    fam_ids.setdefault(fam, []).append(vi)
all_ids = list(range(len(VARIANTS)))

baseline = load_baseline()
base_flat = flat(baseline) if "oof" in baseline else {k: baseline["summary"][k] for k in baseline["summary"]}
base_vi = next(i for i, (f, p) in enumerate(VARIANTS) if f == "med" and p == dict(w=3, T=0.6, P=3))
base_check = flat(report_for_mask(MASKS[base_vi]))
assert base_check["false_alarm_count"] == 400 and base_check["missed_failure_window_count"] == 607, base_check

results = {"baseline": base_check, "pooled_oof_best_BIASED": {}, "nested": {}, "fixed_a_priori": {}}
nested_reports = {}
for objective in OBJECTIVES:
    for scope, ids in [("all_families", all_ids), *[(f"family_{f}", v) for f, v in fam_ids.items()]]:
        mask, chosen = nested(ids, objective)
        r = report_for_mask(mask)
        key = f"{scope}|obj={objective}"
        nested_reports[key] = r
        results["nested"][key] = {
            "summary": flat(r),
            "chosen_per_heldout_bearing": chosen,
            "distinct_choices": sorted(set(chosen.values())),
            "first_warning_rul": fw_detail(r),
            "false_alarms_per_bearing": fa_per_bearing(r),
        }
        pb = pooled_best(ids, objective)
        rp = report_for_mask(MASKS[pb])
        results["pooled_oof_best_BIASED"][key] = {"variant": vname(*VARIANTS[pb]), "summary": flat(rp)}
        s = flat(r)
        print(f"NESTED {key:32s} F1 {s['f1']:.3f} FA {s['false_alarm_count']:4d} miss "
              f"{s['missed_failure_window_count']:4d} wh/early/no {s['within_horizon_bearings']}/"
              f"{s['early_warning_bearings']}/{s['no_warning_bearings']}  | pooled-best "
              f"{vname(*VARIANTS[pb])} F1 {flat(rp)['f1']:.3f}", flush=True)

# A-priori (not tuned) principled variants at baseline threshold: what does each
# mechanism do on its own?
apriori = {
    "latch(w=3,T=0.6,P=3)": ("latch", dict(w=3, T=0.6, P=3)),
    "latch(w=3,T=0.6,P=5)": ("latch", dict(w=3, T=0.6, P=5)),
    "latch(w=3,T=0.6,P=10)": ("latch", dict(w=3, T=0.6, P=10)),
    "hyst(w=3,T=0.6,P=3,d=0.2)": ("hyst", dict(w=3, T=0.6, P=3, d=0.2)),
    "ewma(a=0.3,T=0.6,P=3)": ("ewma", dict(a=0.3, T=0.6, P=3)),
    "med(w=9,T=0.6,P=3)": ("med", dict(w=9, T=0.6, P=3)),
    "cusum(k=0.6,h=2)": ("cusum", dict(k=0.6, h=2)),
}
for name, (fam, prm) in apriori.items():
    vi = next(i for i, v in enumerate(VARIANTS) if v == (fam, prm))
    r = report_for_mask(MASKS[vi])
    results["fixed_a_priori"][name] = {"summary": flat(r), "false_alarms_per_bearing": fa_per_bearing(r),
                                       "first_warning_rul": fw_detail(r)}

# Latch x early-warning interaction (baseline threshold): FA delta per bearing
lat = results["fixed_a_priori"]["latch(w=3,T=0.6,P=3)"]["false_alarms_per_bearing"]
basefa = fa_per_bearing(report_for_mask(MASKS[base_vi]))
results["latch_fa_interaction"] = {
    b: {"baseline_fa": basefa[b], "latch_fa": lat[b]} for b in BEARINGS if basefa[b] or lat[b]
}

# Continuous score ranking quality (AP/AUC) of causal smoothers - pooled, no threshold
score_q = {}
for name, fn in {
    "raw": lambda p: p, "median3(baseline)": lambda p: roll_median(p, 3), "median5": lambda p: roll_median(p, 5),
    "median9": lambda p: roll_median(p, 9), "ewma0.3": lambda p: ewma(p, 0.3), "ewma0.1": lambda p: ewma(p, 0.1),
    "cummax_median3": lambda p: np.maximum.accumulate(roll_median(p, 3)),
    "cusum_k0.6": lambda p: cusum(p, 0.6),
}.items():
    s = np.zeros(len(y))
    for b in BEARINGS:
        s[IDX[b]] = fn(P_RAW[IDX[b]])
    score_q[name] = {"roc_auc": round(roc_auc_score(TRUTH, s), 4),
                     "average_precision": round(average_precision_score(TRUTH, s), 4)}
results["score_ranking_quality"] = score_q
print(json.dumps(score_q, indent=1))


# ------------------------------------------------------------ RUL temporal post-proc (a priori)
def rul_variant(fn):
    out = np.zeros(len(y))
    for b in BEARINGS:
        out[IDX[b]] = fn(RUL[IDX[b]])
    return out


def monotone(r, slack=0.0):
    # causal: RUL may not rise above previous estimate minus 1 min elapsed (+slack)
    out = np.empty_like(r)
    prev = np.inf
    for i, v in enumerate(r):
        prev = min(v, prev - 1 + slack)
        prev = max(prev, 0.0)
        out[i] = prev
    return out


rul_res = {}
base_mask = MASKS[base_vi]
for name, fn in {
    "baseline": lambda r: r,
    "median5": lambda r: roll_median(r, 5),
    "ewma0.3": lambda r: ewma(r, 0.3),
    "monotone_slack2": lambda r: monotone(r, 2.0),
}.items():
    rr = rul_variant(fn)
    rep = compute_report(ctx, y, P_RAW, rr, postprocess_fn=lambda t, p: base_mask)
    live = np.where(base_mask, rr, H)[TRUTH]
    rul_res[name] = {k: round(v, 3) for k, v in rep["within_horizon_rul"].items()} | {
        "live_equivalent_mae": round(float(np.mean(np.abs(live - y[TRUTH]))), 3)}
results["rul_temporal_postprocess_a_priori"] = rul_res
results["rul_constant_60_mae"] = round(float(np.mean(np.abs(60 - y[TRUTH]))), 3)
print(json.dumps(rul_res, indent=1))

# ------------------------------------------------------------ headline + save
HEAD = "all_families|obj=f1"
head_r = nested_reports[HEAD]
results["headline"] = {"key": HEAD, "summary": flat(head_r)}
results["notes"] = (
    "No refit. Variants applied to baseline OOF raw probabilities via harness.compute_report "
    "postprocess_fn. Nested LOBO picks params on the other 14 bearings' OOF rows; those OOF probs "
    "come from fold models that did see the held-out bearing in training (mild second-order leak). "
    "AP/ROC in summaries are the harness's rolling-median-3 values (decision logic does not change them)."
)
(RES / "alarm-logic.json").write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
save_oof(head_r, RES / "alarm-logic_oof.csv")
compare(head_r)
