"""Classifier model family & calibration experiment (XJTU-SY RUL, stage 1).

Isolates the classifier: features (production classifier_feature_columns),
120-min label, smoothing(3)/threshold(0.6)/persistence(3) alarm logic and the
production regressor are all held fixed.  Every variant is evaluated through
the shared harness ``evaluate`` (same LOBO folds and regressor seeds), so the
RUL metrics are identical to baseline unless the classifier changes nothing
about them (the regressor does not consume classifier output).

Variants (all configs fixed a priori, none tuned on the held-out bearing):
  et_bw        production ExtraTrees, class_weight replaced by bearing-balanced
               x class-balanced sample weights
  et_reg       ExtraTrees regularised for 14 training bearings
               (min_samples_leaf=20, max_features=0.3), class_weight=balanced
               -> BEST variant (see results/model-family.json)
  et_reg_bw    ExtraTrees regularised for 14 training bearings
               (min_samples_leaf=20, max_features=0.3) + bearing weights
  hgb          HistGradientBoosting (shallow, strong leaf/L2 reg, colsample 0.5),
               class_weight=balanced
  hgb_bw       same + bearing-balanced weights
  xgb_cw       xgboost depth-3, class-balanced sample weights
  xgb_bw       xgboost depth-3 (subsample/colsample, lambda=5) + bearing weights
  <best>_iso   nested isotonic calibration: inner GroupKFold(5) over the 14
               training bearings -> inner OOF probs -> isotonic map, applied to
               the outer test bearing (monotone, so AUC ~unchanged; it moves the
               operating point of the fixed 0.6 threshold honestly)

Variants actually run: et_reg, et_reg_bw, hgb, hgb_bw, xgb_cw, xgb_bw, xgb_bw_iso
(et_bw / *_iso for ET/HGB defined but not run: compute budget on the shared box).
Post-hoc analysis (FA-matched and leave-one-bearing-out threshold views):
experiments/xjtu/scratch_mf/analyze.py.

Usage (repo root):  python experiments/xjtu/exp_model-family.py [variant ...]
Per-variant reports are cached in experiments/xjtu/results/model-family/<v>.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from threadpoolctl import threadpool_limits

sys.path.insert(0, ".")
from experiments.xjtu.harness import (  # noqa: E402
    compare, evaluate, load_context, save_oof, save_report, summary_metrics,
)

OUT_DIR = Path("experiments/xjtu/results/model-family")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CTX = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
# Row index -> bearing id; harness passes X_c.iloc[tr] which keeps this index.
BEARING = CTX["bearing_id"].astype(str)


def bearing_class_weights(bearings: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Each bearing contributes equal total weight; then classes rebalanced to 50/50."""
    s = pd.Series(bearings)
    w = 1.0 / s.map(s.value_counts()).to_numpy(dtype=float)
    y = np.asarray(y, dtype=bool)
    pos, neg = w[y].sum(), w[~y].sum()
    w = np.where(y, w / pos, w / neg)
    return w / w.mean()


def class_weights(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=bool)
    w = np.where(y, 0.5 / y.mean(), 0.5 / (1 - y.mean()))
    return w / w.mean()


class Weighted(BaseEstimator, ClassifierMixin):
    """Pipeline wrapper that injects sample weights computed from bearing ids."""

    def __init__(self, pipeline, mode="bearing"):
        self.pipeline = pipeline
        self.mode = mode

    def fit(self, X, y):
        y = np.asarray(y)
        if self.mode == "bearing":
            w = bearing_class_weights(BEARING.loc[X.index].to_numpy(), y)
        else:
            w = class_weights(y)
        last = self.pipeline.steps[-1][0]
        self.pipeline.fit(X, y, **{f"{last}__sample_weight": w})
        self.classes_ = self.pipeline.classes_
        return self

    def predict_proba(self, X):
        return self.pipeline.predict_proba(X)


class NestedIsotonic(BaseEstimator, ClassifierMixin):
    """Fit base on all training bearings; isotonic map from inner group-CV OOF probs."""

    def __init__(self, base, n_inner=5):
        self.base = base
        self.n_inner = n_inner

    def fit(self, X, y):
        y = np.asarray(y)
        groups = BEARING.loc[X.index].to_numpy()
        inner = np.full(len(y), np.nan)
        for tr, te in GroupKFold(n_splits=self.n_inner).split(X, y, groups):
            m = clone(self.base).fit(X.iloc[tr], y[tr])
            inner[te] = m.predict_proba(X.iloc[te])[:, 1]
        self.iso_ = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(inner, y)
        self.model_ = clone(self.base).fit(X, y)
        self.classes_ = np.array([False, True])
        return self

    def predict_proba(self, X):
        p = self.iso_.predict(self.model_.predict_proba(X)[:, 1])
        return np.column_stack([1 - p, p])


# ------------------------------------------------------------------ factories
def et(seed, n_jobs, leaf=3, mf=0.8, cw="balanced"):
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", ExtraTreesClassifier(
            n_estimators=350, min_samples_leaf=leaf, max_features=mf,
            class_weight=cw, n_jobs=n_jobs, random_state=seed)),
    ])


def hgb(seed, n_jobs, cw="balanced"):
    # NaNs handled natively; no imputer needed but kept for parity of inputs.
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=300, max_leaf_nodes=15, max_depth=4,
            min_samples_leaf=80, l2_regularization=1.0, max_features=0.5,
            class_weight=cw, early_stopping=False, random_state=seed)),
    ])


def xgb(seed, n_jobs):
    from xgboost import XGBClassifier
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", XGBClassifier(
            n_estimators=300, learning_rate=0.05, max_depth=3, min_child_weight=20,
            subsample=0.7, colsample_bytree=0.5, reg_lambda=5.0, tree_method="hist",
            n_jobs=n_jobs, random_state=seed, verbosity=0)),
    ])


VARIANTS = {
    "et_bw": lambda s, j: Weighted(et(s, j, cw=None), "bearing"),
    "et_reg": lambda s, j: et(s, j, leaf=20, mf=0.3),
    "et_reg_bw": lambda s, j: Weighted(et(s, j, leaf=20, mf=0.3, cw=None), "bearing"),
    "hgb": lambda s, j: hgb(s, j),
    "hgb_bw": lambda s, j: Weighted(hgb(s, j, cw=None), "bearing"),
    "xgb_cw": lambda s, j: Weighted(xgb(s, j), "class"),
    "xgb_bw": lambda s, j: Weighted(xgb(s, j), "bearing"),
    "et_reg_bw_iso": lambda s, j: NestedIsotonic(
        Weighted(et(s, j, leaf=20, mf=0.3, cw=None), "bearing")),
    "hgb_bw_iso": lambda s, j: NestedIsotonic(Weighted(hgb(s, j, cw=None), "bearing")),
    "xgb_bw_iso": lambda s, j: NestedIsotonic(Weighted(xgb(s, j), "bearing")),
}


def run(name: str) -> dict:
    out_json = OUT_DIR / f"{name}.json"
    if out_json.exists():
        return json.loads(out_json.read_text())
    t0 = time.perf_counter()
    # MF_THREADS=1 avoids OpenMP spin-wait collapse on the saturated shared box
    # (sklearn HGB ran ~4x slower with 3 OpenMP threads than with 1).
    threads = int(os.environ.get("MF_THREADS", "3"))
    with threadpool_limits(threads):
        res = evaluate(CTX, make_classifier=VARIANTS[name], n_jobs=threads, verbose=True)
    print(f"{name}: {time.perf_counter() - t0:.0f}s")
    compare(res)
    save_report(res, out_json)
    save_oof(res, OUT_DIR / f"{name}_oof.csv")
    return json.loads(out_json.read_text())


if __name__ == "__main__":
    for v in (sys.argv[1:] or list(VARIANTS)):
        run(v)
