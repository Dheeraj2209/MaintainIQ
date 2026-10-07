"""Post-hoc analysis of model-family OOF files (no refits).

For each variant: harness metrics at the fixed 0.6 threshold, plus
 * fa_matched: threshold chosen on pooled OOF so false alarms ~= baseline 400
   (DIAGNOSTIC ONLY: pooled-OOF selection, optimistic) -> compares ranking
   quality at a common operating point.
 * lobo_thr: per test bearing, threshold maximising F1 of the full alarm logic
   on the other 14 bearings' OOF probabilities (approximate nesting: those
   OOF probs come from models that saw the test bearing in training).
 * within-bearing AUC (mean over bearings with both classes), per-condition AUC.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, ".")
from experiments.xjtu.harness import compute_report, load_context, summary_metrics  # noqa: E402

D = Path("experiments/xjtu/results/model-family")
ctx = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
y = ctx["rul_minutes"].astype(float).to_numpy()
ih = y <= 120
bear = ctx["bearing_id"].astype(str).to_numpy()
GRID = np.round(np.arange(0.2, 0.951, 0.025), 3)


def load(name):
    f = Path("experiments/xjtu/results/baseline_oof.csv") if name == "baseline" else D / f"{name}_oof.csv"
    o = pd.read_csv(f)
    assert (o["bearing_id"].to_numpy() == bear).all() and (o["cycle"].to_numpy() == ctx["cycle"].to_numpy()).all()
    return o["raw_prob"].to_numpy(), o["rul_pred"].to_numpy()


def warn_at(p, thr):
    r = compute_report(ctx, y, p, RUL, threshold=thr)
    return r["oof"]["warning"]


def lobo_threshold_warning(p):
    warns = {t: warn_at(p, t) for t in GRID}
    out = np.zeros(len(p), bool)
    chosen = {}
    for b in np.unique(bear):
        m = bear != b
        best = max(GRID, key=lambda t: _f1(ih[m], warns[t][m]))
        chosen[b] = float(best)
        out[~m] = warns[best][~m]
    return out, chosen


def _f1(t, w):
    tp = np.sum(t & w)
    return 0 if tp == 0 else 2 * tp / (np.sum(t) + np.sum(w))


def analyze(name):
    global RUL
    p, RUL = load(name)
    base = summary_metrics(compute_report(ctx, y, p, RUL))
    sm = compute_report(ctx, y, p, RUL)["oof"]["smoothed_prob"]
    wb = [roc_auc_score(ih[bear == b], sm[bear == b]) for b in np.unique(bear)
          if 0 < ih[bear == b].sum() < (bear == b).sum() - 2]
    cond = ctx["condition"].to_numpy()
    out = {"fixed_0.6": base,
           "within_bearing_auc_mean": float(np.mean(wb)),
           "condition_auc": {int(c): float(roc_auc_score(ih[cond == c], sm[cond == c])) for c in np.unique(cond)}}
    # FA-matched (diagnostic)
    best = None
    for t in np.round(np.arange(0.2, 0.99, 0.005), 3):
        s = summary_metrics(compute_report(ctx, y, p, RUL, threshold=t))
        if s["false_alarm_count"] <= 400:
            best = (t, s)
            break
    out["fa_matched_pooled_DIAGNOSTIC"] = {"threshold": best[0], **best[1]}
    w, chosen = lobo_threshold_warning(p)
    s = summary_metrics(compute_report(ctx, y, p, RUL, postprocess_fn=lambda t, pr: w))
    out["lobo_threshold_approx_nested"] = {"thresholds": chosen, **s}
    return out


if __name__ == "__main__":
    names = sys.argv[1:] or ["baseline"] + sorted(f.stem[:-4] for f in D.glob("*_oof.csv"))
    res = {n: analyze(n) for n in names}
    keys = ["roc_auc", "average_precision", "f1", "false_alarm_count", "missed_failure_window_count",
            "within_horizon_bearings", "early_warning_bearings", "no_warning_bearings"]
    for n, r in res.items():
        print(f"\n== {n}  wbAUC={r['within_bearing_auc_mean']:.3f} condAUC={r['condition_auc']}")
        for blk in ("fixed_0.6", "fa_matched_pooled_DIAGNOSTIC", "lobo_threshold_approx_nested"):
            s = r[blk]
            print(f"  {blk:<32}" + " ".join(f"{k[:8]}={s[k]:.3f}" if isinstance(s[k], float) else f"{k[:8]}={s[k]}" for k in keys)
                  + (f" thr={s['threshold']}" if "threshold" in s else ""))
    (D / "analysis.json").write_text(json.dumps(res, indent=2, default=float))
