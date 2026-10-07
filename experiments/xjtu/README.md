# XJTU-SY RUL experiment harness

`harness.py` reproduces the leave-one-bearing-out (LOBO) evaluation of
`src/training/xjtu_rul.py::train_and_export` exactly (same folds, same seeds:
classifier `seed + fold`, regressor `42 + fold`) without writing any model
artifact. Every stage is a parameter so experiments don't touch `src/`.

## Quick start (from repo root)

```bash
python experiments/xjtu/harness.py   # baseline run + check vs models/xjtu_rul_evaluation.json
```

```python
import sys; sys.path.insert(0, ".")
from experiments.xjtu.harness import (load_context, evaluate, compute_report,
    summary_metrics, compare, save_oof, save_report, load_baseline)

ctx = load_context()                 # add_past_context output, cached in cache/context.parquet
res = evaluate(ctx,                  # all keyword args optional
    make_classifier=lambda seed, n_jobs: ...,   # sklearn estimator/pipeline
    make_regressor=lambda seed, n_jobs: ...,
    classifier_feature_fn=lambda t: [...],      # column selection
    regressor_feature_fn=lambda t: [...],
    label_fn=lambda y: y <= 120,                # classifier training target only
    threshold=0.6, smoothing_window=3, persistence=3,
    postprocess_fn=None,             # (table, raw_oof_probs) -> bool warning mask
    regressor_train_mask_fn=None,    # (table_train, y_train) -> bool mask
    rul_postprocess_fn=None,         # (table_test, raw_rul, probs_test) -> rul
    n_jobs=3)
compare(res)                         # vs results/baseline.json
save_oof(res, "experiments/xjtu/results/my_oof.csv")
```

New context features: pass a raw table plus `context_fn=my_context` to
`evaluate`, or build your own context table and pass it directly (it is
sorted by bearing_id, cycle internally).

Cheap post-processing sweeps: reuse OOF arrays via
`compute_report(table, y, res["oof"]["raw_prob"], res["oof"]["rul_pred"], threshold=..., ...)`
(table must be the sorted context table) -- no refitting needed.

## Files
- `results/baseline.json` - harness reproduction of the production report (+ `summary`)
- `results/baseline_oof.csv` - per-row OOF: bearing_id, cycle, condition, rul_minutes,
  in_horizon, raw_prob, smoothed_prob, warning, rul_pred
- `cache/context.parquet` - cached `add_past_context(outputs/xjtu_features.csv)`

## Caveats
- Only 15 bearings. Any threshold / hyperparameter chosen by looking at pooled
  OOF metrics is optimistically biased; report it as such or use nested LOBO.
- Features must be causal and computable from the live predictor state
  (`src/prediction/rul_realtime.py`: last `max_history`=60 snapshots plus the
  first 20 commissioning-baseline snapshots).
- Use n_jobs<=3 (shared machine).
