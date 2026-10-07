"""Combined-candidate experiment for the XJTU-SY two-stage RUL model.

Builds the best combination of the independently tested levers with an
incremental ablation ladder, NESTED leave-one-bearing-out alarm-rule selection
and a seed-robustness check. Nothing under src/, models/, tests/ or
outputs/ is modified; everything is cached under experiments/xjtu/cache/combined
and written to experiments/xjtu/results/combined*.

Stages (run from repo root):
    python experiments/xjtu/combined.py clf  <variant> [seedset]     # outer LOBO classifier OOF probs
    python experiments/xjtu/combined.py reg  <variant> [seedset]     # outer LOBO regressor OOF RUL
    python experiments/xjtu/combined.py inner <variant> <outer folds csv|all> [seedset]
                                                                    # 15x14 inner LOBO for fully nested rules
    python experiments/xjtu/combined.py report                       # ladder + nested + seeds -> results

Fold order and seeding are identical to the harness / production training:
LeaveOneGroupOut over sorted bearing ids, fold = 1..15, classifier member seed
= seed + fold, regressor seed = reg_offset + fold (production: 42 + fold).

Classifier variants
  prod          production ExtraTreesClassifier (leaf 3, max_features 0.8), 203 features
  et_reg        ExtraTreesClassifier(leaf 20, max_features 0.3, balanced)   [model-family]
  et_reg_cap240 ExtraTreesRegressor(leaf 20, max_features 0.3) on min(rul, 240),
                balanced weights, P = sigmoid((120 - pred) / 20)            [labels-targets x model-family]
  prod_cap240   = labels-targets V8 (unregularised capped regressor)        [labels-targets]
  et_reg_rs     et_reg + 48 rs energy features                              [raw-signal x model-family]
  et_reg_cap240_rs  et_reg_cap240 + 48 rs energy features
Regressor variants
  prod          production ExtraTreesRegressor (342 features, leaf 3)
  compact300    ExtraTreesRegressor(300 trees, leaf 300, max_features 0.5) on 36
                scale-free compact features                                 [rul-regressor]
  compact300_rs compact300 + 48 rs energy features
Alarm rules: the 314-rule causal grid of exp_alarm-logic.py (median / EWMA /
hysteresis / latch / CUSUM). Selection objective: pooled F1 on the training
bearings only.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import Pipeline

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from experiments.xjtu.harness import (  # noqa: E402
    compute_report, default_make_classifier, evaluate, load_context, save_oof,
    summary_metrics,
)
from src.training.xjtu_rul import classifier_feature_columns, feature_columns  # noqa: E402

CACHE = HERE / "cache" / "combined"
CACHE.mkdir(parents=True, exist_ok=True)
RES = HERE / "results"
H = 120.0
N_JOBS = 3
SEEDSETS = {"s0": (42, 200, 1000), "s1": (7, 77, 777), "s2": (1, 11, 111)}
REG_OFFSET = {"s0": 42, "s1": 1042, "s2": 2042}


# ------------------------------------------------------------------ table
def _load_rs_module():
    spec = importlib.util.spec_from_file_location("exp_rs", HERE / "exp_raw-signal-features.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RSM = _load_rs_module()


def build_table() -> pd.DataFrame:
    p = CACHE / "table.parquet"
    if p.exists():
        return pd.read_parquet(p)
    t = RSM.build_table().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    t.to_parquet(p, index=False)
    return t


TBL = build_table()
Y = TBL["rul_minutes"].to_numpy(float)
TRUTH = Y <= H
BEARINGS = sorted(TBL["bearing_id"].unique())
IDX = {b: np.flatnonzero(TBL["bearing_id"].to_numpy() == b) for b in BEARINGS}
GROUPS = TBL["bearing_id"].astype(str)
FOLDS = list(LeaveOneGroupOut().split(TBL, Y, GROUPS))  # same order as harness


def BASE_CLF(t):
    return [c for c in classifier_feature_columns(t) if not c.startswith("rs_")]


def BASE_REG(t):
    return [c for c in feature_columns(t) if not c.startswith("rs_")]


def RS_ENERGY(t):
    return RSM.RS_ENERGY_REL(t)


# ------------------------------------------------------------- estimators
def sigmoid_map(pred, center=H, scale=20.0):
    return 1.0 / (1.0 + np.exp((np.asarray(pred, float) - center) / scale))


class CappedRULRiskClassifier(BaseEstimator, ClassifierMixin):
    """Regress min(rul, cap) with class-balanced weights; expose P(rul <= horizon).

    fit(X, rul_minutes) takes the *raw* RUL target; predict_proba maps the
    predicted capped RUL through a fixed logistic (0.5 at the horizon).
    """

    def __init__(self, cap=240.0, horizon=H, scale=20.0, min_samples_leaf=20,
                 max_features=0.3, n_estimators=350, random_state=42, n_jobs=N_JOBS):
        self.cap, self.horizon, self.scale = cap, horizon, scale
        self.min_samples_leaf, self.max_features = min_samples_leaf, max_features
        self.n_estimators, self.random_state, self.n_jobs = n_estimators, random_state, n_jobs

    def fit(self, X, y):
        y = np.asarray(y, float)
        pos = y <= self.horizon
        w = np.where(pos, 0.5 / max(pos.sum(), 1), 0.5 / max((~pos).sum(), 1))
        w = w / w.mean()
        self.imputer_ = SimpleImputer(strategy="median").fit(X)
        self.regressor_ = ExtraTreesRegressor(
            n_estimators=self.n_estimators, min_samples_leaf=self.min_samples_leaf,
            max_features=self.max_features, n_jobs=self.n_jobs, random_state=self.random_state,
        ).fit(self.imputer_.transform(X), np.minimum(y, self.cap), sample_weight=w)
        self.classes_ = np.array([False, True])
        return self

    def predict_proba(self, X):
        p = sigmoid_map(self.regressor_.predict(self.imputer_.transform(X)), self.horizon, self.scale)
        return np.column_stack([1 - p, p])


def et_reg_clf(seed, n_jobs=N_JOBS):
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", ExtraTreesClassifier(
            n_estimators=350, min_samples_leaf=20, max_features=0.3,
            class_weight="balanced", n_jobs=n_jobs, random_state=seed)),
    ])


def cap240_clf(seed, n_jobs=N_JOBS, leaf=20, mf=0.3):
    return CappedRULRiskClassifier(cap=240.0, min_samples_leaf=leaf, max_features=mf,
                                   random_state=seed, n_jobs=n_jobs)


HARD = lambda y: y <= H  # noqa: E731
RAW = lambda y: y  # noqa: E731  (capped classifier receives raw RUL)

CLF = {
    # name: (make(seed, n_jobs), features(t), training target(y))
    "prod": (default_make_classifier, BASE_CLF, HARD),
    "et_reg": (et_reg_clf, BASE_CLF, HARD),
    "et_reg_cap240": (cap240_clf, BASE_CLF, RAW),
    "prod_cap240": (lambda s, n: cap240_clf(s, n, leaf=3, mf=0.8), BASE_CLF, RAW),
    "et_reg_rs": (et_reg_clf, lambda t: BASE_CLF(t) + RS_ENERGY(t), HARD),
    "et_reg_cap240_rs": (cap240_clf, lambda t: BASE_CLF(t) + RS_ENERGY(t), RAW),
}


def compact_features(table: pd.DataFrame) -> pd.DataFrame:
    """36 scale-free causal features (identical to exp_rul-regressor.compact_features)."""
    X = pd.DataFrame(index=table.index)
    for c in ["m_rms", "h_peak", "v_peak", "m_envelope_rms", "h_kurtosis", "v_kurtosis"]:
        r = table[f"{c}_baseline_ratio"].clip(lower=1e-3)
        base = (table[c] / r).abs().clip(lower=1e-12)
        X[f"log_{c}_ratio"] = np.log(r)
        for w in (5, 20, 60):
            X[f"{c}_mean{w}_lr"] = np.log((table[f"{c}_mean_{w}"] / base).clip(lower=1e-3))
        for w in (20, 60):
            X[f"{c}_reltrend{w}"] = table[f"{c}_trend_{w}"] / table[c].abs().clip(lower=1e-9)
    return X


def _compact_reg(seed, leaf=300):
    return ExtraTreesRegressor(n_estimators=300, min_samples_leaf=leaf, max_features=0.5,
                               random_state=seed, n_jobs=N_JOBS)


REG = {
    "compact300": (lambda t: compact_features(t), _compact_reg),
    "compact300_rs": (lambda t: pd.concat([compact_features(t), t[RS_ENERGY(t)]], axis=1), _compact_reg),
}


# ----------------------------------------------------------------- runners
def clf_path(name, seedset):
    return CACHE / f"clf_{name}_{seedset}.npy"


def reg_path(name, seedset):
    return CACHE / f"reg_{name}_{seedset}.npy"


def run_clf(name, seedset="s0"):
    make, feats, target = CLF[name]
    t0 = time.perf_counter()
    res = evaluate(
        TBL, classifier_feature_fn=feats, regressor_feature_fn=lambda t: ["m_rms"],
        make_classifier=make, make_regressor=lambda s, n: DummyRegressor(),
        label_fn=target, ensemble_seeds=SEEDSETS[seedset], n_jobs=N_JOBS, verbose=True,
    )
    np.save(clf_path(name, seedset), res["oof"]["raw_prob"])
    print(f"clf {name} {seedset}: {res['classifier_feature_count']} feats "
          f"{time.perf_counter() - t0:.0f}s AUC {res['failure_detection']['roc_auc']:.4f} "
          f"AP {res['failure_detection']['average_precision']:.4f}", flush=True)


def run_reg(name, seedset="s0"):
    feats, make = REG[name]
    X = feats(TBL).to_numpy(float)
    out = np.full(len(TBL), np.nan)
    for fold, (tr, te) in enumerate(FOLDS, start=1):
        late = tr[TRUTH[tr]]
        m = make(REG_OFFSET[seedset] + fold).fit(X[late], Y[late])
        out[te] = np.clip(m.predict(X[te]), 0, H)
    np.save(reg_path(name, seedset), out)
    print(f"reg {name} {seedset}: MAE {np.mean(np.abs(out[TRUTH] - Y[TRUTH])):.3f}", flush=True)


def run_inner(name, outer_folds, seedset="s0"):
    """Inner LOBO over the 14 training bearings of each outer fold (3-seed ensemble)."""
    make, feats, target = CLF[name]
    cols = feats(TBL)
    X = TBL[cols]
    tgt = np.asarray(target(Y))
    for fold in outer_folds:
        p = CACHE / f"inner_{name}_{seedset}_f{fold:02d}.npy"
        if p.exists():
            continue
        tr_out, _ = FOLDS[fold - 1]
        inner = np.full(len(TBL), np.nan)
        g = GROUPS.iloc[tr_out]
        t0 = time.perf_counter()
        for j, (itr, ite) in enumerate(LeaveOneGroupOut().split(tr_out, groups=g), start=1):
            a, b = tr_out[itr], tr_out[ite]
            probs = []
            for seed in SEEDSETS[seedset]:
                m = make(seed + 100 * fold + j, N_JOBS)
                m.fit(X.iloc[a], tgt[a])
                probs.append(m.predict_proba(X.iloc[b])[:, 1])
            inner[b] = np.mean(probs, axis=0)
        np.save(p, inner)
        print(f"inner {name} outer fold {fold} done {time.perf_counter() - t0:.0f}s", flush=True)


# -------------------------------------------------------------- alarm rules
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
    return pd.Series(above.astype(int)).rolling(P, min_periods=P).sum().to_numpy() == P


def hysteresis(score, t_on, t_off, P):
    trig = persist(score >= t_on, P)
    out = np.zeros(len(score), bool)
    state = False
    for i in range(len(score)):
        if not state and trig[i]:
            state = True
        elif state and score[i] < t_off:
            state = False
        out[i] = state
    return out


THS = [0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75]
RULES = []
for w, T, P in itertools.product([1, 3, 5, 9], THS, [1, 3, 5]):
    RULES.append(("med", dict(w=w, T=T, P=P)))
for a, T, P in itertools.product([0.1, 0.2, 0.3, 0.5], THS, [1, 3, 5]):
    RULES.append(("ewma", dict(a=a, T=T, P=P)))
for w, T, P, d in itertools.product([3, 5], THS, [3, 5], [0.1, 0.2, 0.3]):
    RULES.append(("hyst", dict(w=w, T=T, P=P, d=d)))
for w, T, P in itertools.product([3, 5], THS, [3, 5, 10]):
    RULES.append(("latch", dict(w=w, T=T, P=P)))
for k, h in itertools.product([0.4, 0.5, 0.6, 0.7], [0.5, 1, 2, 4, 8]):
    RULES.append(("cusum", dict(k=k, h=h)))
PROD_RULE = RULES.index(("med", dict(w=3, T=0.6, P=3)))
assert len(RULES) == 314


def rule_mask(fam, prm, p):
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


def rname(i):
    fam, prm = RULES[i]
    return fam + "(" + ",".join(f"{k}={v}" for k, v in prm.items()) + ")"


def rule_counts(probs, bearings):
    """counts[rule, bearing] = (tp, fp, fn) and masks per bearing."""
    counts = np.zeros((len(RULES), len(bearings), 3), int)
    masks = {}
    for ri, (fam, prm) in enumerate(RULES):
        for bi, b in enumerate(bearings):
            idx = IDX[b]
            m = rule_mask(fam, prm, probs[idx])
            masks[(ri, b)] = m
            t = TRUTH[idx]
            counts[ri, bi] = [np.sum(m & t), np.sum(m & ~t), np.sum(~m & t)]
    return counts, masks


def f1c(c):
    tp, fp, fn = c
    return 2 * tp / max(2 * tp + fp + fn, 1)


def select(counts, cols, ids=None):
    ids = range(len(RULES)) if ids is None else ids
    best, val = None, -np.inf
    for ri in ids:
        v = f1c(counts[ri, cols].sum(axis=0))
        if v > val + 1e-12:
            best, val = ri, v
    return best


def pseudo_nested(probs, ids=None):
    """Rule for bearing b chosen on the other 14 bearings' OUTER OOF probs."""
    counts, masks = rule_counts(probs, BEARINGS)
    warn = np.zeros(len(Y), bool)
    chosen = {}
    for bi, b in enumerate(BEARINGS):
        ri = select(counts, [j for j in range(len(BEARINGS)) if j != bi], ids)
        chosen[b] = rname(ri)
        warn[IDX[b]] = masks[(ri, b)]
    deploy = rname(select(counts, list(range(len(BEARINGS))), ids))
    return warn, chosen, deploy


def fully_nested(probs, name, seedset="s0", ids=None):
    """Rule for bearing b chosen on INNER-LOBO probs of the other 14 bearings
    (models that never saw b), then applied to b's outer OOF probs."""
    warn = np.zeros(len(Y), bool)
    chosen = {}
    for fold, b in enumerate(BEARINGS, start=1):
        assert GROUPS.iloc[FOLDS[fold - 1][1][0]] == b
        inner = np.load(CACHE / f"inner_{name}_{seedset}_f{fold:02d}.npy")
        others = [x for x in BEARINGS if x != b]
        counts, _ = rule_counts(inner, others)
        ri = select(counts, list(range(len(others))), ids)
        chosen[b] = rname(ri)
        fam, prm = RULES[ri]
        warn[IDX[b]] = rule_mask(fam, prm, probs[IDX[b]])
    return warn, chosen


# ------------------------------------------------------------------ report
def report(probs, rul, warn=None):
    if warn is None:
        return compute_report(TBL, Y, probs, rul)
    return compute_report(TBL, Y, probs, rul, postprocess_fn=lambda t, p: warn)


def live_mae(rep):
    o = rep["oof"]
    live = np.where(o["warning"], o["rul_pred"], H)
    return float(np.mean(np.abs(live[TRUTH] - Y[TRUTH])))


def within_bearing_auc(probs):
    sm = np.zeros(len(Y))
    for b in BEARINGS:
        sm[IDX[b]] = roll_median(probs[IDX[b]], 3)
    vals = [roc_auc_score(TRUTH[IDX[b]], sm[IDX[b]]) for b in BEARINGS
            if 0 < TRUTH[IDX[b]].sum() < len(IDX[b])]
    return float(np.mean(vals))


def flat(rep, probs=None):
    s = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in summary_metrics(rep).items()}
    s["live_rul_mae"] = round(live_mae(rep), 3)
    if probs is not None:
        s["within_bearing_mean_auc"] = round(within_bearing_auc(probs), 4)
    return s


def first_warn(rep):
    return {r["bearing_id"]: r["actual_rul_minutes_at_first_warning"]
            for r in rep["first_warning_events"]["per_bearing"]}


def load_probs(name, seedset="s0"):
    if name == "prod" and seedset == "s0":
        return pd.read_csv(RES / "baseline_oof.csv")["raw_prob"].to_numpy()
    return np.load(clf_path(name, seedset))


def load_rul(name, seedset="s0"):
    if name == "prod":
        return pd.read_csv(RES / "baseline_oof.csv")["rul_pred"].to_numpy()
    return np.load(reg_path(name, seedset))


def evaluate_step(clf, reg, seedset="s0", nested="pseudo"):
    probs, rul = load_probs(clf, seedset), load_rul(reg, seedset)
    fixed = report(probs, rul)
    out = {"classifier": clf, "regressor": reg, "seedset": seedset,
           "fixed_prod_rule": flat(fixed, probs), "fixed_first_warning": first_warn(fixed)}
    warn, chosen, deploy = pseudo_nested(probs)
    pn = report(probs, rul, warn)
    out["pseudo_nested_rule"] = flat(pn, probs)
    out["pseudo_nested_chosen"] = chosen
    out["pseudo_nested_first_warning"] = first_warn(pn)
    out["rule_picked_on_all_15"] = deploy
    reps = {"fixed": fixed, "pseudo_nested": pn}
    if nested == "full":
        warn, chosen = fully_nested(probs, clf, seedset)
        fn = report(probs, rul, warn)
        out["fully_nested_rule"] = flat(fn, probs)
        out["fully_nested_chosen"] = chosen
        out["fully_nested_first_warning"] = first_warn(fn)
        reps["fully_nested"] = fn
    return out, reps


def summarize():
    t0 = time.perf_counter()
    ladder_spec = [
        # (step, classifier, regressor, description)
        ("L0_baseline", "prod", "prod", "production model"),
        ("L1_regressor_compact300", "prod", "compact300",
         "+ compact 36-feature heavily regularised RUL regressor (leaf 300)"),
        ("L2_classifier_et_reg", "et_reg", "compact300",
         "+ regularised ExtraTrees classifier (leaf 20, max_features 0.3)"),
        ("L3_target_capped240", "et_reg_cap240", "compact300",
         "+ capped-RUL(240) regression target with logistic map, on the regularised trees"),
        ("L4_rs_energy_features", "et_reg_cap240_rs", "compact300",
         "+ 48 raw-signal energy-distribution features in the classifier"),
        ("L4b_rs_energy_in_regressor", "et_reg_cap240", "compact300_rs",
         "+ 48 raw-signal energy features in the compact regressor instead"),
    ]
    alternatives = [
        ("A_prod_cap240_V8", "prod_cap240", "compact300", "labels-targets V8 classifier (unregularised capped)"),
        ("A_et_reg_rs", "et_reg_rs", "compact300", "et_reg + rs energy (hard label)"),
    ]
    out = {"ladder": {}, "alternatives": {}}
    reps_keep = {}
    for key, specs in (("ladder", ladder_spec), ("alternatives", alternatives)):
        for step, c, r, desc in specs:
            try:
                res, reps = evaluate_step(c, r)
            except FileNotFoundError as e:
                print("skip", step, e)
                continue
            res["description"] = desc
            out[key][step] = res
            reps_keep[step] = reps
            f, p = res["fixed_prod_rule"], res["pseudo_nested_rule"]
            print(f"{step:30s} fixed AUC {f['roc_auc']:.3f} AP {f['average_precision']:.3f} "
                  f"F1 {f['f1']:.3f} FA {f['false_alarm_count']:4d} miss {f['missed_failure_window_count']:4d} "
                  f"{f['within_horizon_bearings']}/{f['early_warning_bearings']}/{f['no_warning_bearings']} "
                  f"MAE {f['rul_mae']:.2f} | pnested F1 {p['f1']:.3f} FA {p['false_alarm_count']:4d} "
                  f"miss {p['missed_failure_window_count']:4d} "
                  f"{p['within_horizon_bearings']}/{p['early_warning_bearings']}/{p['no_warning_bearings']} "
                  f"deploy={res['rule_picked_on_all_15']}", flush=True)
    (RES / "combined_ladder_raw.json").write_text(json.dumps(out, indent=2, default=float))
    print(f"summarize {time.perf_counter() - t0:.0f}s")
    return out, reps_keep


def _blend(seedset="s0"):
    return 0.5 * (load_probs("et_reg", seedset) + load_probs("et_reg_cap240", seedset))


def _inner_available(name, seedset="s0"):
    return all((CACHE / f"inner_{name}_{seedset}_f{f:02d}.npy").exists() for f in range(1, 16))


def full_report(final_clf: str, final_reg: str = "compact300"):
    """Ladder (fixed / pseudo-nested / fully nested rules), final candidate, seed spread."""
    ladder, _ = summarize()
    # fully nested rule selection wherever the 15x14 inner probs exist
    for key in ("ladder", "alternatives"):
        for step, res in ladder[key].items():
            if _inner_available(res["classifier"]):
                full, reps = evaluate_step(res["classifier"], res["regressor"], nested="full")
                res.update({k: v for k, v in full.items() if k.startswith("fully_nested")})
    # post-hoc blend of the two classifier formulations (selection-biased: defined after
    # seeing L2/L3 disagree); fully nested via averaged inner probs when both exist
    probs = _blend()
    rul = load_rul(final_reg)
    blend = {"classifier": "blend(et_reg, et_reg_cap240)", "regressor": final_reg,
             "fixed_prod_rule": flat(report(probs, rul), probs)}
    w, ch, dep = pseudo_nested(probs)
    blend["pseudo_nested_rule"] = flat(report(probs, rul, w), probs)
    blend["rule_picked_on_all_15"] = dep
    if _inner_available("et_reg") and _inner_available("et_reg_cap240"):
        for f in range(1, 16):
            p = CACHE / f"inner_blend_s0_f{f:02d}.npy"
            a = np.load(CACHE / f"inner_et_reg_s0_f{f:02d}.npy")
            b = np.load(CACHE / f"inner_et_reg_cap240_s0_f{f:02d}.npy")
            np.save(p, 0.5 * (a + b))
        w, ch = fully_nested(probs, "blend")
        blend["fully_nested_rule"] = flat(report(probs, rul, w), probs)
        blend["fully_nested_chosen"] = ch
    ladder["alternatives"]["A_blend_post_hoc"] = blend

    # final candidate (fully nested headline)
    final, reps = evaluate_step(final_clf, final_reg, nested="full")
    deploy_name = final["rule_picked_on_all_15"]
    deploy_idx = [rname(i) for i in range(len(RULES))].index(deploy_name)
    seeds = {}
    for ss in SEEDSETS:
        try:
            pr, ru = load_probs(final_clf, ss), load_rul(final_reg, ss)
        except FileNotFoundError:
            continue
        fam, prm = RULES[deploy_idx]
        dmask = np.zeros(len(Y), bool)
        for b in BEARINGS:
            dmask[IDX[b]] = rule_mask(fam, prm, pr[IDX[b]])
        w, ch, dep = pseudo_nested(pr)
        seeds[ss] = {
            "classifier_seeds": SEEDSETS[ss], "regressor_seed_offset": REG_OFFSET[ss],
            "fixed_prod_rule": flat(report(pr, ru), pr),
            "deployed_rule_" + deploy_name: flat(report(pr, ru, dmask), pr),
            "pseudo_nested_rule": flat(report(pr, ru, w), pr),
            "pseudo_nested_rule_picked_on_all_15": dep,
        }
    base_seeds = {}
    for ss in SEEDSETS:
        try:
            pr = load_probs("prod", ss)
        except FileNotFoundError:
            continue
        base_seeds[ss] = {"fixed_prod_rule": flat(report(pr, load_rul("prod")), pr)}

    def spread(block):
        keys = ["roc_auc", "average_precision", "f1", "false_alarm_count",
                "missed_failure_window_count", "early_warning_bearings", "no_warning_bearings",
                "rul_mae", "rul_error_90", "live_rul_mae"]
        out = {}
        for rule_key in next(iter(block.values())):
            if not isinstance(next(iter(block.values()))[rule_key], dict):
                continue
            vals = {k: [block[s][rule_key][k] for s in block] for k in keys}
            out[rule_key] = {k: {"min": min(v), "max": max(v), "mean": round(float(np.mean(v)), 4)}
                             for k, v in vals.items()}
        return out

    # Diagnostic only (pooled OOF, NOT used for selection): near-failure behaviour of the
    # compact regressor vs leaf size, and the production regressor.
    Xc = compact_features(TBL).to_numpy(float)
    leaf_diag = {}
    m30, m60 = Y <= 30, TRUTH & (Y > 60)
    for leaf in (30, 80, 150, 300):
        pr = np.zeros(len(Y))
        for fold, (tr, te) in enumerate(FOLDS, start=1):
            late = tr[TRUTH[tr]]
            pr[te] = np.clip(_compact_reg(42 + fold, leaf).fit(Xc[late], Y[late]).predict(Xc[te]), 0, H)
        leaf_diag[f"compact_leaf{leaf}"] = pr
    leaf_diag["production"] = load_rul("prod")
    regressor_diag = {}
    for k, pr in leaf_diag.items():
        e = np.abs(pr[TRUTH] - Y[TRUTH])
        regressor_diag[k] = {
            "mae": round(float(e.mean()), 3),
            "error_90": round(float(np.quantile(e, 0.9, method="higher")), 2),
            "mae_rul_le_30": round(float(np.abs(pr[m30] - Y[m30]).mean()), 2),
            "frac_pred_le_30_when_rul_le_30": round(float((pr[m30] <= 30).mean()), 3),
            "frac_pred_le_30_when_rul_61_120": round(float((pr[m60] <= 30).mean()), 3),
            "min_in_horizon_prediction": round(float(pr[TRUTH].min()), 1),
        }

    decisions = {
        "L1_regressor_compact300": "KEPT: MAE 30.85->27.44, error_90 62.8->48.4, live MAE down; alarms untouched; no predictor state",
        "L2_classifier_et_reg": "KEPT: AUC +0.026, AP +0.030 (larger than seed noise), more ranking per bearing; pure hyperparameter change",
        "L3_target_capped240": "DROPPED: AP +0.056 but under the fully nested rule F1 0.728 vs 0.732, FA 463 vs 427, early-warning bearings 7 vs 5; "
                               "fixed 0.6 rule leaves Bearing1_4 with no warning; needs a custom wrapper class",
        "L4_rs_energy_features": "DROPPED: AUC 0.841->0.803, pseudo-nested F1 0.725->0.691; needs raw-signal extraction changes",
        "L4b_rs_energy_in_regressor": "DROPPED: MAE 27.44->27.58",
        "A_blend_post_hoc": "DROPPED: fully nested F1 0.719, FA 507",
        "L5_nested_alarm_rule": "KEPT: fully nested F1 0.717->0.732, FA 448->427, misses 481->455, early 6->5 vs fixed production rule on the same classifier",
        "degradation_HI_features": "NOT STACKED: on its own it raised FAs (509) at 0.6 and needs 40 scalars of new per-machine state; its HI regressor (MAE 29.87) is dominated by compact300",
    }

    result = {
        "decisions": decisions,
        "regressor_near_failure_diagnostic_pooled_oof": regressor_diag,
        "description": (
            "Combined XJTU-SY candidate. Ladder adds independently tested levers one at a time; "
            "each step is scored with (a) the production rule median3/T0.6/P3, (b) a pseudo-nested "
            "rule (rule for bearing b picked by pooled F1 on the other 14 bearings' OUTER OOF probs) "
            "and (c) where computed, a FULLY nested rule (rule for b picked on inner-LOBO probs of "
            "the other 14 bearings from 13-bearing models that never saw b). Headline = (c)."),
        "rule_grid": "314 causal rules from exp_alarm-logic.py (median/EWMA/hysteresis/latch/CUSUM), objective pooled F1",
        "ladder": ladder["ladder"],
        "alternatives": ladder["alternatives"],
        "final": {
            "classifier": final_clf, "regressor": final_reg,
            "deployed_rule_picked_on_all_15_outer_oof": deploy_name,
            **final,
        },
        "seed_robustness": {"final": seeds, "final_spread": spread(seeds) if seeds else None,
                            "baseline_prod_classifier": base_seeds},
    }
    (RES / "combined.json").write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    save_oof(reps["fully_nested"], RES / "combined_oof.csv")
    print(json.dumps(result["final"]["fully_nested_rule"], indent=1))
    print(json.dumps(result["seed_robustness"]["final_spread"], indent=1))
    return result


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "report":
        full_report(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "compact300")
        sys.exit(0)
    if cmd == "clf":
        run_clf(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "s0")
    elif cmd == "reg":
        run_reg(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "s0")
    elif cmd == "inner":
        folds = range(1, 16) if sys.argv[3] == "all" else [int(x) for x in sys.argv[3].split(",")]
        run_inner(sys.argv[2], folds, sys.argv[4] if len(sys.argv) > 4 else "s0")
    elif cmd == "ladder":
        summarize()
