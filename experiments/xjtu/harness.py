"""Reusable LOBO experiment harness for the XJTU-SY two-stage RUL model.

Reproduces the leave-one-bearing-out evaluation inside
``src.training.xjtu_rul.train_and_export`` exactly (same fold order, same
seeding: classifier ``seed + fold``, regressor ``42 + fold``), but every stage
is parameterized and nothing is written to models/.

Usage (from repo root):
    python -m experiments.xjtu.harness            # run baseline + verify
    # or in Python:
    from experiments.xjtu.harness import load_context, evaluate, summary_metrics
    ctx = load_context()
    res = evaluate(ctx)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import LeaveOneGroupOut

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.training import xjtu_rul as base  # noqa: E402
from src.training.xjtu_rul import (  # noqa: E402
    CLASSIFIER_ENSEMBLE_SEEDS,
    FAILURE_PROBABILITY_THRESHOLD,
    PROBABILITY_SMOOTHING_WINDOW,
    PROGNOSTIC_HORIZON_MINUTES,
    WARNING_PERSISTENCE_SNAPSHOTS,
    add_past_context,
    classifier_feature_columns,
    feature_columns,
)

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
RESULTS_DIR = HERE / "results"
FEATURES_CSV = REPO_ROOT / "outputs" / "xjtu_features.csv"
BASELINE_REPORT = REPO_ROOT / "models" / "xjtu_rul_evaluation.json"

SUMMARY_KEYS = (
    "roc_auc", "average_precision", "precision", "recall", "f1",
    "false_alarm_count", "missed_failure_window_count",
    "within_horizon_bearings", "early_warning_bearings", "no_warning_bearings",
    "rul_mae", "rul_rmse", "rul_error_90",
)


# ---------------------------------------------------------------- data loading
def load_raw_table(path: Path = FEATURES_CSV) -> pd.DataFrame:
    return pd.read_csv(path)


def load_context(refresh: bool = False, context_fn: Callable = add_past_context) -> pd.DataFrame:
    """Return add_past_context(features), cached under experiments/xjtu/cache.

    The cache only applies to the default ``add_past_context``; any other
    ``context_fn`` is computed fresh.
    """
    if context_fn is not add_past_context:
        return context_fn(load_raw_table())
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    parquet = CACHE_DIR / "context.parquet"
    pickle = CACHE_DIR / "context.pkl"
    if not refresh:
        if parquet.exists():
            try:
                return pd.read_parquet(parquet)
            except Exception:  # pragma: no cover - pyarrow missing/corrupt
                pass
        if pickle.exists():
            return pd.read_pickle(pickle)
    table = add_past_context(load_raw_table())
    try:
        table.to_parquet(parquet, index=False)
    except Exception:  # pragma: no cover
        table.to_pickle(pickle)
    return table


# ------------------------------------------------------------ model factories
def _with_n_jobs(pipeline, n_jobs: int):
    est_name = pipeline.steps[-1][0]
    pipeline.set_params(**{f"{est_name}__n_jobs": n_jobs})
    return pipeline


def default_make_classifier(random_state: int, n_jobs: int = 3):
    return _with_n_jobs(base.make_classifier(random_state), n_jobs)


def default_make_regressor(random_state: int, n_jobs: int = 3):
    return _with_n_jobs(base.make_regressor(random_state), n_jobs)


# --------------------------------------------------------- post-processing
def smooth_probabilities(table: pd.DataFrame, probabilities: np.ndarray, window: int) -> np.ndarray:
    smoothed = np.zeros(len(table), dtype=float)
    for _, indices in table.groupby("bearing_id", sort=False).groups.items():
        idx = np.asarray(list(indices), dtype=int)
        smoothed[idx] = (
            pd.Series(probabilities[idx]).rolling(window, min_periods=1).median().to_numpy()
        )
    return smoothed


def sustained_warning_mask(
    table: pd.DataFrame, probabilities: np.ndarray, threshold: float, persistence: int
) -> np.ndarray:
    active = np.zeros(len(table), dtype=bool)
    for _, indices in table.groupby("bearing_id", sort=False).groups.items():
        idx = np.asarray(list(indices), dtype=int)
        above = pd.Series(probabilities[idx] >= threshold)
        active[idx] = (
            above.rolling(persistence, min_periods=persistence).sum().eq(persistence).to_numpy()
        )
    return active


# ------------------------------------------------------------------ evaluate
def evaluate(
    table: pd.DataFrame,
    *,
    context_fn: Callable | None = None,
    classifier_feature_fn: Callable = classifier_feature_columns,
    regressor_feature_fn: Callable = feature_columns,
    make_classifier: Callable | None = None,
    make_regressor: Callable | None = None,
    label_fn: Callable = lambda y: y <= PROGNOSTIC_HORIZON_MINUTES,
    ensemble_seeds: Iterable[int] = CLASSIFIER_ENSEMBLE_SEEDS,
    threshold: float = FAILURE_PROBABILITY_THRESHOLD,
    smoothing_window: int = PROBABILITY_SMOOTHING_WINDOW,
    persistence: int = WARNING_PERSISTENCE_SNAPSHOTS,
    postprocess_fn: Callable | None = None,
    regressor_train_mask_fn: Callable | None = None,
    rul_postprocess_fn: Callable | None = None,
    n_jobs: int = 3,
    horizon: float = PROGNOSTIC_HORIZON_MINUTES,
    verbose: bool = False,
) -> dict:
    """Run LOBO and return the full metric report plus OOF arrays.

    ``table`` should already have past context (``load_context()``); pass
    ``context_fn`` to apply a context transform to a raw feature table first.

    Hooks:
      make_classifier(random_state, n_jobs) / make_regressor(random_state, n_jobs)
      label_fn(y) -> bool array used as classifier target (metrics always use
          the true horizon label y <= horizon).
      postprocess_fn(table, oof_raw_probabilities) -> bool warning mask
          (replaces smoothing+threshold+persistence for the warning mask; ROC/AP
          still use the default-smoothed probabilities).
      regressor_train_mask_fn(table_train, y_train) -> bool mask selecting
          regressor training rows (default: y_train <= horizon).
      rul_postprocess_fn(table_test, rul_pred, prob_test) -> rul array
          (default: clip to [0, horizon]).
    """
    if context_fn is not None:
        table = context_fn(table)
    table = table.sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
    make_classifier = make_classifier or default_make_classifier
    make_regressor = make_regressor or default_make_regressor
    ensemble_seeds = tuple(ensemble_seeds)

    regressor_features = regressor_feature_fn(table)
    classifier_features = classifier_feature_fn(table)
    groups = table["bearing_id"].astype(str)
    X_c = table[classifier_features]
    X_r = table[regressor_features]
    y = table["rul_minutes"].astype(float).to_numpy()
    in_horizon = y <= horizon
    train_label = np.asarray(label_fn(y))

    probs = np.full(len(table), np.nan)
    rul_pred = np.full(len(table), np.nan)
    t0 = time.perf_counter()
    for fold, (tr, te) in enumerate(LeaveOneGroupOut().split(X_r, y, groups), start=1):
        fold_probs = []
        for seed in ensemble_seeds:
            clf = make_classifier(seed + fold, n_jobs)
            clf.fit(X_c.iloc[tr], train_label[tr])
            fold_probs.append(clf.predict_proba(X_c.iloc[te])[:, 1])
        probs[te] = np.mean(fold_probs, axis=0)

        if regressor_train_mask_fn is None:
            late = tr[in_horizon[tr]]
        else:
            late = tr[np.asarray(regressor_train_mask_fn(table.iloc[tr], y[tr]), dtype=bool)]
        reg = make_regressor(42 + fold, n_jobs)
        reg.fit(X_r.iloc[late], y[late])
        raw = reg.predict(X_r.iloc[te])
        if rul_postprocess_fn is None:
            rul_pred[te] = np.clip(raw, 0, horizon)
        else:
            rul_pred[te] = rul_postprocess_fn(table.iloc[te], raw, probs[te])
        if verbose:
            print(f"fold {fold:2d} {groups.iloc[te[0]]} done {time.perf_counter() - t0:6.1f}s",
                  flush=True)

    return compute_report(
        table, y, probs, rul_pred,
        horizon=horizon, threshold=threshold, smoothing_window=smoothing_window,
        persistence=persistence, postprocess_fn=postprocess_fn,
        extra={
            "classifier_feature_count": len(classifier_features),
            "regressor_feature_count": len(regressor_features),
            "classifier_ensemble_size": len(ensemble_seeds),
            "fit_seconds": time.perf_counter() - t0,
        },
    )


def compute_report(
    table, y, probs, rul_pred, *, horizon=PROGNOSTIC_HORIZON_MINUTES,
    threshold=FAILURE_PROBABILITY_THRESHOLD, smoothing_window=PROBABILITY_SMOOTHING_WINDOW,
    persistence=WARNING_PERSISTENCE_SNAPSHOTS, postprocess_fn=None, extra=None,
) -> dict:
    """Metrics from OOF arrays (re-usable for cheap post-processing sweeps)."""
    in_horizon = y <= horizon
    smoothed = smooth_probabilities(table, probs, smoothing_window)
    if postprocess_fn is None:
        warning = sustained_warning_mask(table, smoothed, threshold, persistence)
    else:
        warning = np.asarray(postprocess_fn(table, probs), dtype=bool)
    smoothed_pred = smoothed >= threshold
    raw_pred = probs >= threshold
    late_err = np.abs(y[in_horizon] - rul_pred[in_horizon])

    per_bearing, first_warnings = [], []
    for bearing_id, indices in table.groupby("bearing_id").groups.items():
        idx = np.asarray(list(indices), dtype=int)
        truth, cls = in_horizon[idx], warning[idx]
        w = idx[cls]
        fw = None if len(w) == 0 else float(y[w[0]])
        first_warnings.append({
            "bearing_id": bearing_id,
            "actual_rul_minutes_at_first_warning": fw,
            "timing": "no_warning" if fw is None else "within_horizon" if fw <= horizon else "early",
        })
        per_bearing.append({
            "bearing_id": bearing_id,
            "samples": len(idx),
            "failure_precision": float(precision_score(truth, cls, zero_division=0)),
            "failure_recall": float(recall_score(truth, cls, zero_division=0)),
            "false_alarm_count": int(np.sum(cls & ~truth)),
            "within_horizon_rul_mae_minutes": float(
                mean_absolute_error(y[idx][truth], rul_pred[idx][truth])
            ),
        })

    def _det(pred):
        return {
            "precision": float(precision_score(in_horizon, pred, zero_division=0)),
            "recall": float(recall_score(in_horizon, pred, zero_division=0)),
            "false_alarm_count": int(np.sum(pred & ~in_horizon)),
            "missed_failure_window_count": int(np.sum(~pred & in_horizon)),
        }

    report = {
        "sample_count": int(len(table)),
        "bearing_count": int(table["bearing_id"].nunique()),
        "prognostic_horizon_minutes": horizon,
        "failure_probability_threshold": threshold,
        "probability_smoothing_window": smoothing_window,
        "warning_persistence_snapshots": persistence,
        **(extra or {}),
        "failure_detection": {
            **_det(warning),
            "f1": float(f1_score(in_horizon, warning, zero_division=0)),
            "average_precision": float(average_precision_score(in_horizon, smoothed)),
            "roc_auc": float(roc_auc_score(in_horizon, smoothed)),
        },
        "raw_failure_detection_without_temporal_smoothing": {
            **_det(raw_pred),
            "average_precision": float(average_precision_score(in_horizon, probs)),
            "roc_auc": float(roc_auc_score(in_horizon, probs)),
        },
        "smoothed_failure_detection_without_persistence": _det(smoothed_pred),
        "first_warning_events": {
            "within_horizon_bearings": sum(r["timing"] == "within_horizon" for r in first_warnings),
            "early_warning_bearings": sum(r["timing"] == "early" for r in first_warnings),
            "no_warning_bearings": sum(r["timing"] == "no_warning" for r in first_warnings),
            "per_bearing": first_warnings,
        },
        "within_horizon_rul": {
            "mae_minutes": float(mean_absolute_error(y[in_horizon], rul_pred[in_horizon])),
            "rmse_minutes": float(np.sqrt(mean_squared_error(y[in_horizon], rul_pred[in_horizon]))),
            "error_90_minutes": float(np.quantile(late_err, 0.90, method="higher")),
        },
        "per_bearing": per_bearing,
        "oof": {
            "table_keys": table[["bearing_id", "cycle", "condition", "rul_minutes"]].copy(),
            "in_horizon": in_horizon,
            "raw_prob": probs,
            "smoothed_prob": smoothed,
            "warning": warning,
            "rul_pred": rul_pred,
        },
    }
    return report


# ----------------------------------------------------------------- helpers
def summary_metrics(result: dict) -> dict:
    fd = result["failure_detection"]
    fw = result["first_warning_events"]
    rul = result["within_horizon_rul"]
    return {
        "roc_auc": fd["roc_auc"],
        "average_precision": fd["average_precision"],
        "precision": fd["precision"],
        "recall": fd["recall"],
        "f1": fd["f1"],
        "false_alarm_count": fd["false_alarm_count"],
        "missed_failure_window_count": fd["missed_failure_window_count"],
        "within_horizon_bearings": fw["within_horizon_bearings"],
        "early_warning_bearings": fw["early_warning_bearings"],
        "no_warning_bearings": fw["no_warning_bearings"],
        "rul_mae": rul["mae_minutes"],
        "rul_rmse": rul["rmse_minutes"],
        "rul_error_90": rul["error_90_minutes"],
    }


def load_baseline(path: Path = RESULTS_DIR / "baseline.json") -> dict:
    """Baseline report (harness reproduction if present, else models/ report)."""
    if not Path(path).exists():
        path = BASELINE_REPORT
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(result: dict, baseline: dict | None = None, labels=("baseline", "candidate")) -> dict:
    """Print side-by-side metrics; both args may be full reports or flat summaries."""
    baseline = baseline if baseline is not None else load_baseline()
    flat = lambda r: r if "roc_auc" in r else summary_metrics(r)  # noqa: E731
    b, c = flat(baseline), flat(result)
    print(f"{'metric':<30}{labels[0]:>14}{labels[1]:>14}{'delta':>14}")
    for k in SUMMARY_KEYS:
        d = c[k] - b[k]
        fmt = "{:>14.4f}" if isinstance(b[k], float) or isinstance(c[k], float) else "{:>14d}"
        print(f"{k:<30}" + fmt.format(b[k]) + fmt.format(c[k]) + "{:>+14.4f}".format(d))
    fwb = {r["bearing_id"]: r for r in baseline.get("first_warning_events", {}).get("per_bearing", [])}
    fwc = result.get("first_warning_events", {}).get("per_bearing", [])
    if fwb and fwc:
        print("\nfirst warning (actual RUL at first warning, min):")
        for r in fwc:
            o = fwb.get(r["bearing_id"], {}).get("actual_rul_minutes_at_first_warning")
            print(f"  {r['bearing_id']:<12}{str(o):>10}{str(r['actual_rul_minutes_at_first_warning']):>10}"
                  f"  {r['timing']}")
    return {k: c[k] - b[k] for k in SUMMARY_KEYS}


def save_oof(result: dict, path) -> Path:
    oof = result["oof"]
    frame = oof["table_keys"].reset_index(drop=True).copy()
    frame["in_horizon"] = oof["in_horizon"]
    frame["raw_prob"] = oof["raw_prob"]
    frame["smoothed_prob"] = oof["smoothed_prob"]
    frame["warning"] = oof["warning"]
    frame["rul_pred"] = oof["rul_pred"]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def save_report(result: dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {k: v for k, v in result.items() if k != "oof"}
    out["summary"] = summary_metrics(result)
    path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    return path


def check_reproduction(result: dict, reference_path: Path = BASELINE_REPORT, tol: float = 1e-6) -> list[str]:
    ref = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    mismatches = []
    a, b = summary_metrics(result), summary_metrics(ref)
    for k in SUMMARY_KEYS:
        if abs(a[k] - b[k]) > tol:
            mismatches.append(f"{k}: harness={a[k]} reference={b[k]}")
    for sec in ("raw_failure_detection_without_temporal_smoothing",
                "smoothed_failure_detection_without_persistence"):
        for k, v in ref[sec].items():
            if abs(result[sec][k] - v) > tol:
                mismatches.append(f"{sec}.{k}: harness={result[sec][k]} reference={v}")
    for r1, r2 in zip(result["first_warning_events"]["per_bearing"],
                      ref["first_warning_events"]["per_bearing"]):
        if r1 != r2:
            mismatches.append(f"first_warning {r1} != {r2}")
    for r1, r2 in zip(result["per_bearing"], ref["per_bearing"]):
        for k in ("failure_precision", "failure_recall", "within_horizon_rul_mae_minutes"):
            if abs(r1[k] - r2[k]) > tol:
                mismatches.append(f"per_bearing {r1['bearing_id']}.{k}: {r1[k]} vs {r2[k]}")
    return mismatches


def main() -> None:
    t0 = time.perf_counter()
    ctx = load_context()
    t1 = time.perf_counter()
    print(f"context loaded: {ctx.shape} in {t1 - t0:.1f}s")
    result = evaluate(ctx, verbose=True)
    t2 = time.perf_counter()
    print(f"LOBO run: {t2 - t1:.1f}s")
    mismatches = check_reproduction(result)
    print("REPRODUCED" if not mismatches else "MISMATCH:\n  " + "\n  ".join(mismatches))
    save_oof(result, RESULTS_DIR / "baseline_oof.csv")
    result["lobo_wall_seconds"] = t2 - t1
    result["reproduces_models_report"] = not mismatches
    save_report(result, RESULTS_DIR / "baseline.json")
    compare(result, json.loads(BASELINE_REPORT.read_text(encoding="utf-8")),
            labels=("models/report", "harness"))


if __name__ == "__main__":
    main()
