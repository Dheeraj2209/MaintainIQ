"""End-to-end confirmation of the chosen regressor variant through harness.evaluate()
(classifier refit from scratch, regressor = compact scale-free ExtraTrees leaf300).
Writes results/rul-regressor_verify.json.  Run: python experiments/xjtu/exp_rul-regressor_verify.py
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "3")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sklearn.ensemble import ExtraTreesRegressor  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
import pandas as pd  # noqa: E402

from experiments.xjtu.harness import RESULTS_DIR, compare, evaluate, load_context, summary_metrics  # noqa: E402
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("exp", Path(__file__).with_name("exp_rul-regressor.py"))
# only need compact_features; importing module runs light top-level setup (no fits)
exp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp)

ctx = load_context().sort_values(["bearing_id", "cycle"]).reset_index(drop=True)
XC = exp.compact_features(ctx).add_prefix("rr_")
table = pd.concat([ctx, XC], axis=1)
RCOLS = list(XC.columns)


def make_reg(seed, n_jobs):
    return Pipeline([("imputer", SimpleImputer(strategy="median")),
                     ("regressor", ExtraTreesRegressor(n_estimators=300, min_samples_leaf=300,
                                                       max_features=0.5, n_jobs=n_jobs,
                                                       random_state=seed))])


# classifier features must exclude the new rr_ columns -> use production selection on ctx cols
from src.training.xjtu_rul import classifier_feature_columns  # noqa: E402
CCOLS = classifier_feature_columns(ctx)
res = evaluate(table, classifier_feature_fn=lambda t: CCOLS, regressor_feature_fn=lambda t: RCOLS,
               make_regressor=make_reg, n_jobs=3, verbose=True)
compare(res)
out = {"summary": summary_metrics(res), "fit_seconds": res["fit_seconds"],
       "classifier_feature_count": res["classifier_feature_count"],
       "regressor_feature_count": res["regressor_feature_count"]}
(RESULTS_DIR / "rul-regressor_verify.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(out)
