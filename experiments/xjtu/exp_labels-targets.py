"""Label / target formulation experiments for the XJTU-SY RUL classifier.

Every variant runs through the shared harness (LOBO, same fold order, same
classifier seeds 42/200/1000 (+fold), same ExtraTrees hyperparameters, same
smoothing 3 / threshold 0.6 / persistence 3) and is scored on the ORIGINAL
truth y = rul_minutes <= 120. Only the classifier TRAINING target (and sample
weights) change. The RUL regressor is identical to production, so to save time
classifier-only variants use a dummy regressor and re-use the baseline OOF
rul_pred (bit-identical to what the production regressor would produce,
because the regressor's seeds/rows/features don't depend on the label).

Usage (repo root):
    python experiments/xjtu/exp_labels-targets.py run  V1 V2 ...   # fit variants
    python experiments/xjtu/exp_labels-targets.py summarize          # aggregate

Label encoding passed through harness.label_fn: float array, 1 = positive,
0 = negative, fractional = soft target, NaN = excluded from classifier training.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from sklearn.base import BaseEstimator, ClassifierMixin  # noqa: E402
from sklearn.dummy import DummyRegressor  # noqa: E402
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402

from experiments.xjtu.harness import (  # noqa: E402
    compute_report, evaluate, load_baseline, load_context, save_oof, summary_metrics,
)
from src.training.xjtu_rul import classifier_feature_columns  # noqa: E402

OUT = Path("experiments/xjtu/results")
VAR_DIR = OUT / "labels-targets"
VAR_DIR.mkdir(parents=True, exist_ok=True)
H = 120.0

CTX = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
BEARING = CTX["bearing_id"].astype(str).to_numpy()
Y = CTX["rul_minutes"].astype(float).to_numpy()
BASE_OOF = pd.read_csv(OUT / "baseline_oof.csv")
assert (BASE_OOF["bearing_id"].astype(str).to_numpy() == BEARING).all()
assert np.array_equal(BASE_OOF["cycle"].to_numpy(), CTX["cycle"].to_numpy())
BASE_RUL = BASE_OOF["rul_pred"].to_numpy()


# --------------------------------------------------------------- health index
def _health_index() -> np.ndarray:
    """Causal rolling-5 median of max(h, v) rms_baseline_ratio (row t uses rows <= t)."""
    hv = np.maximum(CTX["h_rms_baseline_ratio"].to_numpy(), CTX["v_rms_baseline_ratio"].to_numpy())
    s = pd.Series(hv)
    return s.groupby(BEARING).transform(lambda x: x.rolling(5, min_periods=1).median()).to_numpy()


HI = _health_index()


# ------------------------------------------------------------- estimators
def _et_params(seed, n_jobs):
    return dict(n_estimators=350, min_samples_leaf=3, max_features=0.8, n_jobs=n_jobs,
                random_state=seed)


def _bearing_weights(idx: np.ndarray, mode: str | None) -> np.ndarray:
    if mode is None:
        return np.ones(len(idx))
    b = BEARING[idx]
    counts = pd.Series(b).value_counts()
    w = 1.0 / pd.Series(b).map(counts).to_numpy()          # each bearing sums to 1
    if mode == "bearing_sqrt":
        w = np.sqrt(w)                                       # softer: sum ~ sqrt(n_b)
    return w / w.mean()


def _balanced(target: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted class balance (positive = target >= 0.5) so each side gets equal mass."""
    pos = target >= 0.5
    out = w.copy()
    if pos.any() and (~pos).any():
        out[pos] *= 0.5 / w[pos].sum()
        out[~pos] *= 0.5 / w[~pos].sum()
    return out / out.mean()


class LabelAwareClassifier(BaseEstimator, ClassifierMixin):
    """ExtraTrees wrapper: drops NaN-labelled rows, optional per-bearing weights,
    and for soft targets fits a regressor whose clipped output is the probability.
    'target_map' (callable pred->prob) is used for regression-type targets."""

    def __init__(self, seed=42, n_jobs=3, weight_mode=None, soft=False, target_map=None):
        self.seed, self.n_jobs, self.weight_mode = seed, n_jobs, weight_mode
        self.soft, self.target_map = soft, target_map

    def fit(self, X, y):
        y = np.asarray(y, dtype=float)
        rows = X.index.to_numpy()                            # positions in sorted CTX
        keep = ~np.isnan(y)
        X, y, rows = X.loc[keep], y[keep], rows[keep]
        self.imp_ = SimpleImputer(strategy="median").fit(X)
        Xi = self.imp_.transform(X)
        w = _bearing_weights(rows, self.weight_mode)
        if self.soft or self.target_map is not None:
            # balance on the "would-be-positive" side so the scale matches a
            # class_weight='balanced' classifier
            pos_proxy = y if self.target_map is None else self.target_map(y)
            sw = _balanced(np.asarray(pos_proxy), w)
            self.est_ = ExtraTreesRegressor(**_et_params(self.seed, self.n_jobs)).fit(Xi, y, sample_weight=sw)
        else:
            yb = (y >= 0.5).astype(int)
            self.est_ = ExtraTreesClassifier(class_weight="balanced", **_et_params(self.seed, self.n_jobs))
            self.est_.fit(Xi, yb, sample_weight=w)
        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, X):
        Xi = self.imp_.transform(X)
        if isinstance(self.est_, ExtraTreesRegressor):
            p = self.est_.predict(Xi)
            if self.target_map is not None:
                p = self.target_map(p)
            p = np.clip(p, 0, 1)
        else:
            p = self.est_.predict_proba(Xi)[:, 1]
        return np.column_stack([1 - p, p])


# ------------------------------------------------------------- label defs
def hard(y, h=H):
    return (y <= h).astype(float)


def exclude_band(lo, hi):
    def f(y):
        lab = hard(y)
        lab[(y > lo) & (y <= hi)] = np.nan
        return lab
    return f


def ramp(lo, hi):
    """Soft label: 1 for rul <= lo, 0 for rul >= hi, linear between (0.5 at 120)."""
    return lambda y: np.clip((hi - y) / (hi - lo), 0.0, 1.0)


CAP = 360.0


def capped_rul(y):
    return np.minimum(y, CAP)


def rul_to_prob(pred):
    """A-priori fixed map from predicted capped RUL to P(rul<=120): logistic, 0.5 at 120 min,
    scale 20 min (not tuned)."""
    return 1.0 / (1.0 + np.exp((np.asarray(pred) - H) / 20.0))


def onset_anchored(y):
    """Positives only when the bearing is visibly degraded (causal HI >= 1.3);
    healthy-looking in-horizon rows are excluded from training (NaN)."""
    lab = hard(y)
    lab[(y <= H) & (HI < 1.3)] = np.nan
    return lab


def no_healthy_ref_excluded(y):
    """Hard label, but drop the 5 bearings with <=2 negative rows (their 20-row
    commissioning baseline is already in-horizon, so baseline ratios are ~1 while failing)."""
    lab = hard(y)
    neg = pd.Series(Y > H).groupby(BEARING).sum()
    bad = set(neg[neg <= 2].index)
    lab[np.isin(BEARING, list(bad))] = np.nan
    return lab


# ------------------------------------------------------------- feature sets
base_feats = classifier_feature_columns


def relative_feats(t):
    """Condition-free relative feature set from diagnosis 1 (baseline_ratio / std / trend)."""
    return [c for c in base_feats(t) if c not in ("speed_rpm", "load_kn")
            and ("baseline_ratio" in c or "_std_" in c or "_trend_" in c)]


def mk(weight_mode=None, soft=False, target_map=None):
    return lambda seed, n_jobs: LabelAwareClassifier(seed, n_jobs, weight_mode, soft, target_map)


VARIANTS = {
    # sanity: wrapper with hard labels must reproduce the baseline classifier exactly
    "V0_wrapper_hard": dict(label_fn=hard, make_classifier=mk()),
    "V1_exclude_100_160": dict(label_fn=exclude_band(100, 160), make_classifier=mk()),
    "V2_bearing_weight": dict(label_fn=hard, make_classifier=mk("bearing")),
    "V2b_bearing_sqrt_weight": dict(label_fn=hard, make_classifier=mk("bearing_sqrt")),
    "V3_soft_ramp_90_150": dict(label_fn=ramp(90, 150), make_classifier=mk(soft=True)),
    "V4_capped_rul360_reg": dict(label_fn=capped_rul, make_classifier=mk(target_map=rul_to_prob)),
    "V5_wide_horizon_180": dict(label_fn=lambda y: hard(y, 180.0), make_classifier=mk()),
    "V6_onset_anchored": dict(label_fn=onset_anchored, make_classifier=mk()),
    "V7_drop_no_healthy_ref": dict(label_fn=no_healthy_ref_excluded, make_classifier=mk()),
    # interactions with the condition-free relative feature set (diagnosis-1 lever)
    "R0_relative_hard": dict(label_fn=hard, make_classifier=mk(), classifier_feature_fn=relative_feats),
    "R1_relative_soft_ramp": dict(label_fn=ramp(90, 150), make_classifier=mk(soft=True),
                                  classifier_feature_fn=relative_feats),
    "R2_relative_bearing_weight": dict(label_fn=hard, make_classifier=mk("bearing"),
                                       classifier_feature_fn=relative_feats),
    # band-width sensitivity for V1 (run AFTER V1 looked good -> robustness check, not selection)
    "V1b_exclude_110_140": dict(label_fn=exclude_band(110, 140), make_classifier=mk()),
    "V1c_exclude_90_200": dict(label_fn=exclude_band(90, 200), make_classifier=mk()),
    "V8_capped_rul240_reg": dict(label_fn=lambda y: np.minimum(y, 240.0),
                                 make_classifier=mk(target_map=rul_to_prob)),
    "R4_relative_capped_rul360": dict(label_fn=capped_rul, make_classifier=mk(target_map=rul_to_prob),
                                      classifier_feature_fn=relative_feats),
    "R3_relative_exclude_100_160": dict(label_fn=exclude_band(100, 160), make_classifier=mk(),
                                        classifier_feature_fn=relative_feats),
}


def run(name: str) -> None:
    cfg = dict(VARIANTS[name])
    res = evaluate(CTX, make_regressor=lambda seed, n_jobs: DummyRegressor(), n_jobs=3,
                   verbose=True, **cfg)
    prob = res["oof"]["raw_prob"]
    np.save(VAR_DIR / f"{name}_prob.npy", prob)
    rep = compute_report(CTX, Y, prob, BASE_RUL)          # production regressor RUL
    out = {"variant": name, "fit_seconds": res["fit_seconds"],
           "classifier_feature_count": res["classifier_feature_count"],
           "summary": summary_metrics(rep),
           "first_warnings": {e["bearing_id"]: e["actual_rul_minutes_at_first_warning"]
                              for e in rep["first_warning_events"]["per_bearing"]}}
    (VAR_DIR / f"{name}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(name, json.dumps({k: round(v, 4) for k, v in out["summary"].items()}), flush=True)


# ------------------------------------------------------- nested threshold
def nested_threshold_report(prob, grid=np.round(np.arange(0.40, 0.81, 0.05), 2)):
    """Per outer fold, pick the threshold maximizing F1 on the OTHER 14 bearings' OOF
    probabilities, apply it to the held-out bearing. Caveat: other bearings' OOF probs
    come from models that saw the held-out bearing in training (cheap pseudo-nesting,
    no inner refits); labels of the held-out bearing are never used for its threshold."""
    from experiments.xjtu.harness import smooth_probabilities, sustained_warning_mask
    sm = smooth_probabilities(CTX, prob, 3)
    warn_by_t = {t: sustained_warning_mask(CTX, sm, t, 3) for t in grid}
    truth = Y <= H
    final = np.zeros(len(Y), bool)
    chosen = {}
    for b in np.unique(BEARING):
        test = BEARING == b
        best, bt = -1, None
        for t in grid:
            w = warn_by_t[t][~test]
            tp = np.sum(w & truth[~test]); fp = np.sum(w & ~truth[~test]); fn = np.sum(~w & truth[~test])
            f1 = 2 * tp / max(2 * tp + fp + fn, 1)
            if f1 > best:
                best, bt = f1, t
        chosen[b] = float(bt)
        final[test] = warn_by_t[bt][test]
    rep = compute_report(CTX, Y, prob, BASE_RUL, postprocess_fn=lambda t, p: final)
    return rep, chosen


ENSEMBLES = {
    # chosen AFTER seeing single-variant pooled OOF results -> optimistic (selection bias)
    "E1_label_ensemble_V1_V3_V4_V5": ["V1_exclude_100_160", "V3_soft_ramp_90_150",
                                      "V4_capped_rul360_reg", "V5_wide_horizon_180"],
    "E2_V1_V4": ["V1_exclude_100_160", "V4_capped_rul360_reg"],
}


def _row(name, prob, extra=None):
    rep = compute_report(CTX, Y, prob, BASE_RUL)
    nrep, chosen = nested_threshold_report(prob)
    s, n = summary_metrics(rep), summary_metrics(nrep)
    warn = nrep["oof"]["warning"]
    m = Y <= H
    live = np.where(warn, BASE_RUL, H)
    d = {"variant": name, **(extra or {}), "summary": s,
         "first_warnings": {e["bearing_id"]: e["actual_rul_minutes_at_first_warning"]
                            for e in rep["first_warning_events"]["per_bearing"]},
         "nested_threshold": {"summary": n, "chosen_thresholds": chosen,
                              "first_warnings": {e["bearing_id"]: e["actual_rul_minutes_at_first_warning"]
                                                 for e in nrep["first_warning_events"]["per_bearing"]},
                              "live_equivalent_rul_mae": float(np.abs(live[m] - Y[m]).mean())}}
    return d, rep, nrep


def summarize() -> None:
    probs = {"baseline": BASE_OOF["raw_prob"].to_numpy()}
    meta = {}
    for f in sorted(VAR_DIR.glob("*.json")):
        d = json.loads(f.read_text())
        probs[d["variant"]] = np.load(VAR_DIR / f"{d['variant']}_prob.npy")
        meta[d["variant"]] = {k: d[k] for k in ("fit_seconds", "classifier_feature_count")}
    for e, members in ENSEMBLES.items():
        if all(m in probs for m in members):
            probs[e] = np.mean([probs[m] for m in members], axis=0)
            meta[e] = {"members": members}
    rows, store = [], {"baseline": load_baseline()["summary"], "variants": {}}
    for name, prob in probs.items():
        d, _, _ = _row(name, prob, meta.get(name))
        store["variants"][name] = d
        s, n = d["summary"], d["nested_threshold"]["summary"]
        rows.append((name, s["roc_auc"], s["average_precision"], s["f1"], s["false_alarm_count"],
                     s["missed_failure_window_count"], s["early_warning_bearings"],
                     s["no_warning_bearings"], n["f1"], n["false_alarm_count"],
                     n["missed_failure_window_count"], n["early_warning_bearings"], n["no_warning_bearings"],
                     d["nested_threshold"]["live_equivalent_rul_mae"]))
    df = pd.DataFrame(rows, columns=["variant", "auc", "ap", "f1@0.6", "fa@0.6", "miss@0.6", "early@0.6",
                                     "nowarn@0.6", "f1_nest", "fa_nest", "miss_nest",
                                     "early_nest", "nowarn_nest", "liveMAE_nest"])
    pd.set_option("display.width", 250)
    print(df.round(4).to_string(index=False))
    store["table"] = df.round(5).to_dict(orient="records")
    (OUT / "labels-targets.json").write_text(json.dumps(store, indent=1), encoding="utf-8")


def save_best(name: str, nested: bool) -> None:
    prob = (np.mean([np.load(VAR_DIR / f"{m}_prob.npy") for m in ENSEMBLES[name]], axis=0)
            if name in ENSEMBLES else np.load(VAR_DIR / f"{name}_prob.npy"))
    _, rep, nrep = _row(name, prob)
    save_oof(nrep if nested else rep, OUT / "labels-targets_oof.csv")
    store = json.loads((OUT / "labels-targets.json").read_text())
    store["best"] = {"variant": name, "oof_uses_nested_threshold": nested}
    (OUT / "labels-targets.json").write_text(json.dumps(store, indent=1), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "run":
        for v in sys.argv[2:]:
            run(v)
    elif cmd == "summarize":
        summarize()
    elif cmd == "save_best":
        save_best(sys.argv[2], sys.argv[3] == "nested")
