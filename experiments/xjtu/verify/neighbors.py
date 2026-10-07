"""Hyperparameter-neighbourhood check for the et_reg classifier (was picked on pooled OOF
among 7 model-family variants). If the gain is real, neighbours should give similar AUC/AP."""
import sys, json, time
import numpy as np
sys.path.insert(0, '.')
import experiments.xjtu.combined as C
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.dummy import DummyRegressor
from experiments.xjtu.harness import evaluate
cfgs = [(int(a), float(b)) for a, b in (x.split(':') for x in sys.argv[1:])]
for leaf, mf in cfgs:
    p = C.HERE / 'verify' / f'clf_leaf{leaf}_mf{mf}_s0.npy'
    if p.exists(): continue
    mk = lambda seed, n_jobs, leaf=leaf, mf=mf: Pipeline([("imputer", SimpleImputer(strategy="median")),
        ("classifier", ExtraTreesClassifier(n_estimators=350, min_samples_leaf=leaf, max_features=mf,
         class_weight="balanced", n_jobs=3, random_state=seed))])
    t0 = time.perf_counter()
    res = evaluate(C.TBL, classifier_feature_fn=C.BASE_CLF, regressor_feature_fn=lambda t: ["m_rms"],
                   make_classifier=mk, make_regressor=lambda s, n: DummyRegressor(),
                   ensemble_seeds=C.SEEDSETS['s0'], n_jobs=3, verbose=False)
    np.save(p, res['oof']['raw_prob'])
    print(leaf, mf, round(time.perf_counter()-t0), res['failure_detection']['roc_auc'], res['failure_detection']['average_precision'], flush=True)
