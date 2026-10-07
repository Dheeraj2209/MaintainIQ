# Integration plan: combined XJTU-SY RUL candidate

Source of truth for the numbers: `experiments/xjtu/results/combined.json` (script
`experiments/xjtu/combined.py`, OOF in `results/combined_oof.csv`).

## What ships (and what does not)

| Lever | Decision | Why |
|---|---|---|
| Classifier: ExtraTrees `min_samples_leaf 3 -> 20`, `max_features 0.8 -> 0.3` (still 350 trees, `class_weight="balanced"`, seeds 42/200/1000) | **ship** | AUC 0.814 -> 0.840, AP 0.623 -> 0.652; the gain is larger than the seed spread (baseline AUC 0.814-0.817 across seed sets) |
| RUL regressor: 36 scale-free "compact" features, `ExtraTreesRegressor(300 trees, min_samples_leaf=300, max_features=0.5)` | **ship** | within-horizon MAE 30.85 -> 27.44, error_90 62.8 -> 48.4 (interval half-width shrinks accordingly) |
| Alarm rule: **latch** - rolling median 3, threshold 0.6, 3 consecutive snapshots, then the warning stays on until `reset_machine` | **ship** | chosen by the nested selection procedure applied to all 15 bearings; fully nested estimate of that procedure: F1 0.717 -> 0.732, FA 448 -> 427, misses 481 -> 455 on the same classifier |
| Capped-RUL(240) classifier target | not shipped | better AP but worse alarm metrics under the fully nested rule, needs a custom class |
| Raw-signal (rs) energy features | not shipped | AUC drop, needs ingestion change, no regressor gain |
| Degradation HI features | not shipped | more false alarms, needs 40 scalars of new per-machine state |

So **`src/ingestion/xjtu_sy.py` needs no change** and `outputs/xjtu_features.csv` does not need to be rebuilt.
The live predictor needs no new feature history: compact features use only existing
`add_past_context` columns (last 60 snapshots + first-20 commissioning baseline).
The only new per-machine state is **one boolean (latched warning) per machine**.

## 1. `src/training/xjtu_rul.py`

1. Constants (add next to the existing ones):
   ```python
   WARNING_LATCH = True
   REGRESSOR_FEATURE_SET = "xjtu_compact_v1"
   COMPACT_SOURCE_COLUMNS = ("m_rms", "h_peak", "v_peak", "m_envelope_rms", "h_kurtosis", "v_kurtosis")
   ARTIFACT_SCHEMA_VERSION = 2
   ```
   `FAILURE_PROBABILITY_THRESHOLD = 0.6`, `PROBABILITY_SMOOTHING_WINDOW = 3`,
   `WARNING_PERSISTENCE_SNAPSHOTS = 3`, `CLASSIFIER_ENSEMBLE_SEEDS` stay as they are.

2. New function (copy of `combined.compact_features`, must stay importable from `src`):
   ```python
   def compact_regressor_features(table: pd.DataFrame) -> pd.DataFrame:
       X = pd.DataFrame(index=table.index)
       for c in COMPACT_SOURCE_COLUMNS:
           r = table[f"{c}_baseline_ratio"].clip(lower=1e-3)
           base = (table[c] / r).abs().clip(lower=1e-12)       # commissioning baseline level
           X[f"log_{c}_ratio"] = np.log(r)
           for w in (5, 20, 60):
               X[f"{c}_mean{w}_lr"] = np.log((table[f"{c}_mean_{w}"] / base).clip(lower=1e-3))
           for w in (20, 60):
               X[f"{c}_reltrend{w}"] = table[f"{c}_trend_{w}"] / table[c].abs().clip(lower=1e-9)
       return X

   def compact_regressor_input_columns() -> list[str]:
       return [col for c in COMPACT_SOURCE_COLUMNS for col in
               (c, f"{c}_baseline_ratio", *(f"{c}_mean_{w}" for w in (5, 20, 60)),
                *(f"{c}_trend_{w}" for w in (20, 60)))]
   ```
   (36 output columns from 30 input columns.)

3. `make_classifier`: `min_samples_leaf=20`, `max_features=0.3` (keep `n_estimators=350`,
   `class_weight="balanced"`, imputer). Keep `n_jobs` as is in src (the harness overrides it).

4. `make_regressor`: keep the `Pipeline([("imputer", SimpleImputer(strategy="median")), ("regressor", ...)])`
   shape (`register_trained_model` reads `regressor.steps[-1]`), with
   `ExtraTreesRegressor(n_estimators=300, min_samples_leaf=300, max_features=0.5, random_state=...)`.
   The imputer is an identity here (no NaNs), so results equal the experiment.

5. `sustained_warning_mask`: after the persistence test, if `WARNING_LATCH`, apply
   `np.maximum.accumulate` per bearing (`active[idx] = np.maximum.accumulate(active[idx])`).

6. `train_and_export`:
   - `X_regressor = compact_regressor_features(table)`; `regressor_features = list(X_regressor.columns)`.
     Everything else in the LOBO loop is unchanged (regressor still trained on in-horizon rows,
     seed `42 + fold`, predictions clipped to [0, 120]).
   - `conformal_error_90_minutes` is recomputed by the existing code; expect about 48.4
     (was 62.76).
   - Artifact: keep every existing key and add/change:
     ```python
     "artifact_schema_version": 2,
     "regressor_feature_set": "xjtu_compact_v1",
     "regressor_feature_columns": regressor_features,            # 36 compact names
     "regressor_input_columns": compact_regressor_input_columns(),# 30 add_past_context columns
     "feature_columns": regressor_features,                       # legacy mirror, as today
     "warning_latch": True,
     "alarm_rule": "latch(median3 >= 0.6 for 3 snapshots)",
     ```
     `classifier_feature_columns`, `feature_bounds_99pct`, threshold/smoothing/persistence keys
     are unchanged.
   - Report: add `"warning_latch": True` and a limitations line: "the latch keeps the warning
     until the machine is reset after maintenance; nested LOBO estimate of the alarm-rule selection
     procedure: F1 0.732, 427 false alarms, 455 missed windows".

7. Optional, for honest reporting only: the training report's detection metrics now use the
   latch rule on OOF probabilities; those are in-sample for the rule choice (F1 ~0.753, FA ~547,
   misses ~327) and should be labelled as such. The nested numbers above are the headline.

## 2. `src/ingestion/xjtu_sy.py`

No change.

## 3. `src/prediction/rul_realtime.py`

1. Import `compact_regressor_features` from `src.training.xjtu_rul`.
2. `__init__`:
   ```python
   self.regressor_feature_set = artifact.get("regressor_feature_set")          # None for old artifacts
   if self.regressor_feature_set not in (None, "xjtu_compact_v1"):
       raise ValueError(f"unknown regressor_feature_set {self.regressor_feature_set!r}")
   self.regressor_input_columns = artifact.get("regressor_input_columns", self.regressor_feature_columns)
   self.warning_latch = bool(artifact.get("warning_latch", False))              # old artifacts: no latch
   self._latched: dict[str, bool] = defaultdict(bool)
   ```
3. `reset_machine`: also `self._latched.pop(machine_id, None)`. This is the only way to clear
   a latched warning, so the API reset (`src/api/routes/predictions.py:91`) must be wired to
   maintenance / bearing replacement.
4. `_predict_from_base`:
   - Missing-feature check: `needed = set(classifier_feature_columns) | set(regressor_input_columns
     if regressor_feature_set else regressor_feature_columns)`.
   - After `within_horizon` is computed from the persistence deque (unchanged):
     ```python
     if self.warning_latch:
         if within_horizon:
             self._latched[machine_id] = True
         latched = self._latched[machine_id]
         within_horizon = within_horizon or latched
     ```
     (inside the existing `with self._locks[machine_id]` block). Add `"warning_latched": latched`
     to the result and a warning string `"latched_warning: late-life signature confirmed earlier;
     cleared only by reset after maintenance"` when the latch, not the current persistence window,
     is what keeps the warning on. Suppress `confirming_warning` while latched.
   - Regressor input:
     ```python
     if self.regressor_feature_set == "xjtu_compact_v1":
         X_regressor = compact_regressor_features(latest)[self.regressor_feature_columns]
     else:
         X_regressor = latest[self.regressor_feature_columns]
     ```
   - Everything else (probability smoothing deque, threshold, persistence, horizon clip,
     conformal interval from `conformal_error_90_minutes`) is unchanged.
5. State persistence: `_latched` is in memory like the existing deques; if the service persists
   per-machine predictor state, persist this flag too, otherwise a restart silently un-latches a
   warning (the persistence deque would re-trigger within 3 snapshots if the signature is still
   present).

## 4. Backward compatibility of the artifact contract

- Old artifact + new predictor: no `regressor_feature_set` -> raw column selection as today; no
  `warning_latch` -> no latch. Behaviour identical to now; existing test fixtures
  (`tests/test_rul_realtime.py`, `tests/api/test_rul_persistence.py`, `tests/prediction/test_rul_store.py`,
  `tests/api/test_ingestion_control.py`, `tests/ingestion/test_replay_service.py`) keep passing.
- New artifact + old predictor: fails loudly with "live feature vector is missing trained
  features" (the compact names are not produced by `add_past_context`) instead of predicting
  wrongly. `artifact_schema_version: 2` documents this.
- `classifiers` (list of 3 pipelines exposing `predict_proba`) keeps its type and interface.

## 5. Tests to add/update (in `tests/`, by whoever implements this)

- `compact_regressor_features` is causal: value at row t equals the value recomputed on rows <= t,
  and equal when recomputed from the first-20 + last-60 context the predictor keeps.
- Predictor latch: after 3 snapshots >= threshold the warning stays on when probabilities fall,
  and `reset_machine` clears it; old artifacts without `warning_latch` do not latch.
- `tests/test_xjtu_rul.py`: update any assertion on regressor feature count / hyperparameters.

## 6. Retrain and verify

`python -m src.training.xjtu_rul --no-register` then check `models/xjtu_rul_evaluation.json`
against `experiments/xjtu/results/combined.json["final"]["fixed_prod_rule"]` for AUC/AP/RUL
(same folds/seeds -> should match to ~1e-6 except the detection block, which now uses the latch).

## 7. Known risks

- **Near-failure RUL**: the leaf-300 regressor never predicts below about 31 min in LOBO
  (production: 43% of rul<=30 rows predicted <=30). With `health_thresholds_minutes.critical = 30`
  the "critical" state becomes practically unreachable. MAE for rul<=30 rises 28.3 -> 33.1.
  Either accept it (RUL interval is +/-48 anyway) or map "critical" from the interval lower bound /
  a separate signal; lowering leaf size restores range but costs MAE (leaf 30: MAE 30.3) - see
  `regressor_near_failure_diagnostic_pooled_oof` in combined.json (diagnostic, not tuned).
- **Latch**: bearings with multi-stage or early degradation (Bearing3_2, 2_3, 2_5, 3_1 here) stay in
  warning from the first confirmed episode; operators must reset after maintenance.
- 15 bearings only; selection bias notes are in combined.json and the final report.
