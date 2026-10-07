# XJTU-SY health-state redesign: pre-registered evaluation protocol (v2)

Status: **PRE-REGISTERED, 2026-10-06, before any v2 model building.** Everything below
(ground truth, state semantics, metrics, the selection rule, the candidate grid, the
reporting) is fixed now. Any deviation must be written in a dated "Deviations" section
at the end of this file, with the reason, before the affected result is looked at.
Results produced under a changed rule are reported as exploratory, not confirmatory.

Scope rules: read-only on `src/`, `models/`, `tests/`, `outputs/xjtu_features.csv`;
write only under `experiments/xjtu/v2/`; no state-changing git commands; `n_jobs <= 3`;
sklearn/xgboost only (no lightgbm). All features are causal and computable from what
`RealTimeRULPredictor` keeps (first 20 commissioning rows plus rolling history of at most
60 rows), plus the small extra per-machine state listed in section 2.5.

This design merges the three v2 proposals (`design_literature-fpt`, `design_learned-onset`,
`design_product-risk`):
- **Stage 1**: a causal onset detector, anchored to the machine's own commissioning
  baseline. It latches the start of degradation.
- **Stage 2**: a post-onset RUL model with bearing-grouped conformal intervals.
- **faulty / critical**: driven by the stage-2 output OR by physical severity, with
  ratchet semantics. Only a maintenance or replacement reset clears them.

---

## 1. Offline onset ground truth (GT)

### 1.1 Definition (fixed)

Computed per bearing, from that bearing's full run-to-failure trajectory only. Script:
`experiments/xjtu/v2/onset_gt.py`, which writes `experiments/xjtu/v2/onset_gt.csv`. The
GT is never used as a feature. It is never used as a training label for any model that
ships (see 1.4), and nothing is ever fit to it.

1. **Whole-life healthy level per channel.** For channel `c`, `L_c` = median of the
   lowest 30% of `c` over the whole life (minimum 5 rows). It is deliberately *not* the
   20-row commissioning window.
2. **Health indicator (HI) families.**
   - Energy: `HI_E(t) = max_c x_c(t) / L_c` over c in {h_rms, v_rms}.
   - Envelope: `HI_ENV`, the same over {h_envelope_rms, v_envelope_rms}.
   - High-frequency energy: `HI_HF`, the same over `rms * sqrt(e_5-10k + e_10-15k)` per axis.
   Each HI is smoothed with a **centred** 5-row median, which looks ahead on purpose.
3. **Permanent exceedance (primary statistic).** Onset = the earliest row `t` with
   `HI(t) > 1.3` AND `HI > 1.3` on at least 90% of the rows from `t` to failure.
4. **Primary onset point `t_on`** = permanent exceedance on `HI_E` (column `gt_on_rul`).
5. **Uncertainty interval `[t_early, t_late]`** = min and max over four constructions:
   - permanent exceedance on `HI_E`
   - permanent exceedance on `HI_ENV`
   - permanent exceedance on `HI_HF`
   - the two-phase exponential fit, fused fitted excess at delta = 0.3. This is computed
     by `design_learned-onset/onset_delta.py` and read from `onset_delta.csv`.

   The interval spans three HI families and two statistics (persistence fraction and
   global parametric fit).
6. **Sensitivity variants.** These are reported only; nothing is selected on them:
   - V-hinge: least-squares constant-then-rising hinge on log `HI_E`
   - V-2x: permanent exceedance at 2.0x
   - V-comm: permanent exceedance with the 20-row commissioning median as the level
     (the `design_product-risk` label)

   The hinge is not used in the interval. On gradually drifting bearings (1_2, 2_2, 3_5)
   its least-squares tau collapses to the first few rows.

### 1.2 Computed onsets (RUL minutes; produced by `onset_gt.py` on 2026-10-06)

`gt_early_rul` is the earlier edge in time, so it has the larger RUL.

| bearing | cond | life | **gt_on** | **gt_early** | **gt_late** | env | hf | two-phase 0.3 | V-hinge | V-2x | V-comm | commission / life level | commission / fleet | baseline_contaminated | short_healthy | abrupt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_1 | 1 | 123 | **48** | 76 | 47 | 47 | 51 | 76 | 67 | 44 | 52 | 0.978 | 1.194 | F | F | F |
| 1_2 | 1 | 161 | **129** | 129 | 110 | 110 | 116 | 114 | 157 | 108 | 129 | 1.000 | 1.029 | F | F | F |
| 1_3 | 1 | 158 | **85** | 85 | 66 | 66 | 66 | 69 | 84 | 55 | 85 | 0.987 | 0.865 | F | F | F |
| 1_4 | 1 | 122 | **5** | 42 | 5 | 39 | 42 | 14 | 75 | none | none | 1.052 | 0.967 | F | F | T |
| 1_5 | 1 | 52 | **17** | 17 | 15 | 15 | 15 | 15 | 22 | 16 | 17 | 1.011 | 1.758 | T | F | T |
| 2_1 | 2 | 491 | **38** | 40 | 38 | 38 | 38 | 40 | 47 | 36 | 36 | 1.192 | 0.786 | T | F | F |
| 2_2 | 2 | 161 | **110** | 110 | 106 | 106 | 109 | 109 | 141 | 103 | 110 | 1.021 | 0.979 | F | F | F |
| 2_3 | 2 | 533 | **369** | 405 | 273 | 405 | 405 | 273 | 317 | 211 | 266 | 1.095 | 1.088 | F | F | F |
| 2_4 | 2 | 42 | **11** | 13 | 11 | 11 | 11 | 13 | 16 | 11 | 11 | 1.077 | 1.248 | F | T | T |
| 2_5 | 2 | 339 | **217** | 217 | 210 | 217 | 217 | 210 | 236 | 171 | 192 | 1.192 | 2.378 | T | F | F |
| 3_1 | 3 | 2538 | **157** | 159 | 128 | 128 | 159 | 158 | 175 | 126 | 149 | 1.058 | 0.684 | F | F | F |
| 3_2 | 3 | 2496 | **1031** | 1473 | 1031 | 1335 | 1473 | 1177 | 1225 | 68 | 1076 | 1.125 | 1.592 | T | F | F |
| 3_3 | 3 | 371 | **29** | 31 | 28 | 28 | 28 | 31 | 39 | 27 | 29 | 1.222 | 0.892 | T | F | T |
| 3_4 | 3 | 1515 | **97** | 390 | 97 | 97 | 390 | 104 | 115 | 93 | 97 | 1.275 | 0.756 | T | F | F |
| 3_5 | 3 | 114 | **90** | 90 | 84 | 84 | 84 | 90 | 110 | 42 | 102 | 0.792 | 3.422 | T | T | F |

Notes:
- 3_2 degrades in stages: >2x permanently only from RUL 68, with an earlier stage from
  about 1031-1473. 3_4's HF onset (390) is an outlier, which widens its interval.
- 1_4's energy HI rises above its whole-life level only in the last minutes (gt_on 5).
  Its envelope/HF onsets are 39-42.
- The interval absorbs these disagreements; it is label uncertainty and is reported as such.

### 1.3 Bearings without a clean healthy baseline

No bearing is excluded from any metric. Flags (columns in `onset_gt.csv`) are used only
to stratify reporting:
- `baseline_contaminated`: the 20-row commissioning median of `HI_E` is above 1.15x the
  whole-life level, OR the commissioning RMS is above 1.5x the same-condition fleet
  reference (leave-one-out median of the other bearings' first 20 rows). Flagged: 1_5,
  2_1, 2_5, 3_2, 3_3, 3_4, 3_5.
- `short_healthy`: GT early onset row < 30, so fewer than 10 healthy rows exist after
  the commissioning window. Flagged: 2_4, 3_5.
- `abrupt`: gt_on <= 30 min. Flagged: 1_4, 1_5, 2_4, 3_3. These are near-irreducible
  from vibration and are reported explicitly, never tuned away.

Detector eligibility: a causal detector cannot confirm before row `20 + k - 1`.
Detection delay is measured against `max(t_late, eligible_row)`. No bearing is
currently affected, because the minimum `t_late` row is 29 (3_5).

Because the GT uses the whole-life level, contaminated commissioning windows do not
shift the label. They do blunt the causal detector, which is exactly what the
stratified reporting is meant to expose. A contaminated-baseline fallback is a
candidate in section 5.

### 1.4 Why the GT is not circular with the detector

1. **The GT cannot be computed causally.** It uses centred smoothing, a whole-life
   level, and persistence until failure; the two-phase variant also uses a global fit.
   The live detector sees only past rows and the 20-row commissioning baseline.
2. **Different reference and different statistic.** The GT level is the median of the
   lowest 30% of life, against the detector's first-20-row median/MAD. The GT uses a
   persistence fraction and a parametric fit, against the detector's sigma/ratio test
   with k consecutive triggers. The GT interval also includes two non-RMS families
   (ENV, HF).
3. **Nothing is fit to the GT.**
   - Stage 1 rule thresholds are chosen by the failure-anchored cost (section 4).
   - The only learned onset candidate (ET-onset, section 5) trains on the label
     "t >= gt_on" of the inner-fold training bearings. It must beat the rule under the
     same cost.
   - Stage 2 trains on rows after the *causal* detector's onset, with true RUL as the
     target.
4. **The primary decision is failure-anchored.** Missed critical, early critical,
   lateness, false-episode counts and lead times are all measured against actual
   failure time. The GT enters only two cost terms (FD, LD; see 3.4), and those must
   keep their ranking under V-hinge, V-2x and V-comm (section 4.3).
5. **Shared energy family.** We state plainly that onset agreement is partly built in:
   the causal 1.3x rule matches the GT at Spearman about 0.98. Detector-vs-GT delay is
   therefore never the primary score on its own.

---

## 2. Health-state semantics the model will output

### 2.1 Causal quantities (per machine, per snapshot t)

- `med20_c`, `MAD20_c`: median and MAD of channel c over the 20 commissioning rows.
  The predictor already keeps these.
- Energy ratio `r_t = max_c rms_c(t) / ref_c`, over c in {h, v}.
  - `ref_c = med20_c` by default.
  - Under the fallback candidate: `ref_c = min(med20_c, m60_c)` when
    `m60_c < 0.9 * med20_c`. Here `m60_c` is the minimum trailing-10 median of `rms_c`
    over rows 0..59.
- Robust z: `z_t = max_c (rms_c - ref_c) / max(1.4826 * MAD20_c, 0.02 * ref_c)`.
- Peak ratio: `p_t = max_c peak_c / med20(peak_c)`.
- Trailing 5-row medians: `r5`, `z5`, `p5`.
- Warm-up: for t < 20 the state is `healthy` with the `warming_up` warning (as today).

### 2.2 States, evaluated in priority order (escalation needs `m = 3` consecutive qualifying snapshots)

| state | entry condition | alert | promise to the engineer |
|---|---|---|---|
| **healthy** | onset latch `O = 0` | none | "No detectable departure from this machine's own baseline." This promises NO minimum time to failure. The fleet residual risk is shown instead: 1/15 bearings with no precursor, 4/15 with <= 30 min. |
| **degrading** | `O = 1`: the stage-1 detector has confirmed onset (k consecutive triggers) | low | "Damage has started; create a work order." |
| **faulty** | `O = 1` AND (`q50 <= 60` OR `r5 >= S1`) | medium | "Advanced damage; replace at the next planned stop; do not run unattended." |
| **critical** | `O = 1` AND (`model_crit` OR `p5 >= S2`), where `model_crit` = `q50 <= 30` or calibrated `q10 <= 30` (a candidate choice) | high + email page | "Failure possible within 30 min; stop or replace now." |

Further rules:
- `O` and the severity tests may become true in the same snapshot. Abrupt bearings can
  go healthy -> faulty/critical directly.
- Every non-healthy state carries `reason_code` in {onset, rul, severity}.

### 2.3 RUL and probability outputs (keeps the existing API shape)

- **`O = 0`:**
  - `rul_estimate_kind = "lower_bound"`, `predicted_rul_minutes = 120`, interval `[120, None]`.
  - `failure_within_horizon_probability = 0`, plus a `no_onset` warning.
- **`O = 1`:**
  - `rul_estimate_kind = "post_onset"`, `predicted_rul_minutes = q50`, interval
    `[q10, q90]` (bearing-grouped conformal).
  - `failure_within_horizon_probability` = P(RUL <= 120), interpolated from the
    conformalized predictive quantiles.
- `within_horizon` = state in {faulty, critical}.

### 2.4 Latch, ratchet and reset

- **degrading** clears back to healthy only through hysteresis, and only while
  `max_state_reached == degrading`. The hysteresis rule (`r5 < 1.1` for H consecutive
  rows, or a hard latch) is a candidate, see section 5. A clear is logged as
  "transient cleared" and counted as an episode end.
- **faulty / critical ratchet.** `max_state_reached` is monotone. The displayed state is
  never demoted within an episode; a cleared condition is annotated `condition_receded`.
- **Reset.** Only a maintenance record of type replacement (or with `resets_baseline`)
  or an explicit `reset_machine` resets the state. Reset clears history, the baseline,
  `O`, `fpt` and the ratchet, and starts a new 20-row commissioning capture.
  - Alert acknowledgement does not reset the state.
  - "Sensor remount: re-baseline only" keeps `max_state_reached`.
  - These src/ changes are integration work and are out of scope here.

### 2.5 New per-machine state (minimal, must persist)

| field | meaning |
|---|---|
| `onset_latched` | the latch `O` |
| `fpt_row` | snapshot index of the confirmed onset |
| `log_r5_at_fpt` | log `r5` at onset |
| `peak_r5_since_fpt` | running maximum of `r5` since onset |
| `trigger_run` | consecutive stage-1 triggering snapshots |
| `calm_run` | consecutive calm snapshots (hysteresis) |
| `faulty_run`, `critical_run` | consecutive qualifying snapshots for each escalation |
| `max_state_reached` | ratchet level |
| `m60_h`, `m60_v` | fallback reference minima (only if the fallback is selected) |

That is 9-11 scalars. Rolling windows of <= 60 rows already exist.

---

## 3. Metrics (all computed on outer leave-one-bearing-out predictions)

### 3.1 Evaluation setup

- Outer LOBO uses LeaveOneGroupOut over the sorted bearing ids: 15 folds, the same order
  as `experiments/xjtu/harness.py`.
- The replay is causal per bearing, row by row, using exactly the state machine in
  section 2. All 9216 rows are scored; warm-up rows are healthy for every model.
- Per-bearing metric first. The pooled value is the equal-weight mean over bearings,
  unless stated otherwise.
- Row-pooled values are shown only as secondary, because 3_1 and 3_2 hold about 54% of
  rows.

### 3.2 Reference models (same rows, same folds)

- **R0 (production).** `experiments/xjtu/results/baseline_oof.csv`, the reproduced
  production outer-LOBO OOF. State = healthy if `warning` is False. Otherwise the
  state follows the artifact thresholds on `rul_pred`: critical <= 30, faulty <= 60,
  degrading <= 120, else healthy.
- **R1 (previous-round candidate).** `experiments/xjtu/results/combined_oof.csv`
  (et_reg classifier, compact300 regressor, fully nested latch rule). It is mapped with
  the same thresholds as R0.
- **D (simplest new pipeline, fixed defaults, no tuning).**
  - Stage 1: ratio rule `r5 >= 1.3` for k = 3, self baseline, hard latch.
  - Stage 2: time-since-onset baseline (2.6) with global conformal.
  - Thresholds: S1 = 2.0, S2 = 5.0, `model_crit = q10 <= 30` at alpha = 0.1.
- **N (nested-selected new pipeline).** Sections 4-5.

### 3.3 Onset / degrading metrics

| id | metric |
|---|---|
| O1 | Detection delay per bearing. `t_det` = the first row in degrading-or-above. Delay = 0 if `t_det` is inside `[t_early, t_late]`; `t_det - t_late` (positive, min) if later; `t_det - t_early` (negative) if earlier. Reported as the distribution (median, IQR, min, max) and the number of bearings with \|delay\| <= 15. |
| O2 | Pre-onset false degrading. Per bearing: (a) the number of non-healthy episodes that start before `t_early`, and (b) the non-healthy minutes before `t_early`. Pooled as episodes per 1000 pre-onset machine-hours and as the number of bearings affected. The same is reported against `t_on` (less conservative). |
| O3 | Missed-onset bearings: no degrading-or-above at true RUL >= 10. Listed by name. |
| O4 | Degrading lead = RUL at `t_det`. Median, minimum, and the number of bearings with lead >= 30. |

### 3.4 Faulty / critical timing (failure-anchored)

| id | metric |
|---|---|
| F1 | Critical lead = RUL at first entry to critical. Per bearing, median, minimum. "Late critical" = entry at RUL < 10. |
| F2 | Missed critical (MC): no critical entered at RUL >= 10. Count, plus the list of bearings. |
| F3 | Early-critical burden (EC): hours in critical while true RUL > 60. Also the number of bearings with any such row, split by reason_code. |
| F4 | Critical precision `P(RUL <= 30 \| critical)` and recall of RUL <= 30 rows, per bearing averaged. |
| F5 | Early-faulty burden (EF): hours in faulty-or-above while true RUL > 120. |
| F6 | Faulty lead = RUL at first faulty-or-above. |
| F7 | Demotion fraction: rows whose displayed state is below the previous row's within an episode. Must be 0 for N and D; reported for R0 and R1. |

### 3.5 Post-onset RUL and intervals

Rows: each model's own post-onset rows (`O = 1`). Additionally, and like-for-like across
all models, the common set of rows after `t_late`. For R0 and R1 the point estimate is
`rul_pred`.

| id | metric |
|---|---|
| P1 | Bearing-averaged MAE, and MAE on rows with RUL <= 60. Compared against (i) a constant: the median post-onset RUL of the training bearings' rows, and (ii) the time-since-onset (TSO) baseline `max(0, median_train_post_onset_life - tso)`. Both baselines are fit inside the outer fold. |
| P2 | Coverage of `[q10, q90]` (nominal 0.80) and of the lower bound `P(RUL >= q10)` (nominal 0.90). Per bearing average, worst bearing, and per predicted-RUL bin (0-30, 30-60, 60-120, 120+). Mean interval width. |
| P3 | Late-prediction rate: `pred > true` on rows with true <= 30. |

### 3.6 Legacy 120-min metrics (comparison only, never used for selection)

- AP and ROC-AUC of `failure_within_horizon_probability` against the label RUL <= 120.
  Pooled, plus the within-bearing mean.
- Precision, recall, F1 and false-alarm rows (alarm at RUL > 120), with the alarm
  defined two ways: alarm = state >= faulty, and alarm = state >= degrading.

---

## 4. PRIMARY selection rule (single explicit procedure)

### 4.1 Per-bearing composite cost (lower is better)

```
C_b = 10 * MC_b                   # missed critical (no critical at RUL >= 10)
    +  1 * EC_b                   # hours in critical while RUL > 60
    + 0.5 * FD_b                  # false degrading episodes starting before t_early
    +  1 * LD_b                   # onset lateness, hours = max(0, t_det - t_late)/60
                                  #   (t_det := failure row if never detected)
    + 0.25 * EF_b                 # hours in faulty-or-above while RUL > 120
C   = mean over bearings of C_b   (equal bearing weight)
```

The weights encode the product cost asymmetry:
- A missed page is very expensive.
- Nuisance paging is expensive.
- An early work order is cheap.
- A degrading state on a bearing that really is degrading is free.

The weights are fixed now and are not tuned.

The stage-1-only cost `C1_b = 10 * MO_b + 0.5 * FD_b + 1 * LD_b` (MO = missed onset,
O3) is used to select stage 1 (4.2, step 1).

### 4.2 Nested selection (inside each outer fold; held-out bearing never touched)

Staged and lexicographic:

1. **Stage 1.** Evaluate every stage-1 candidate on the 14 training bearings and pick
   the one minimising mean `C1`.
   - Rule candidates have no fitted parameters, so their score on training bearings is
     honest.
   - ET-onset is scored on inner-LOBO (14-fold) predictions.
2. **Stage 2 and state thresholds.**
   - With the chosen stage 1 fixed, compute its causal FPT on each training bearing.
   - For each stage-2 family, produce inner-LOBO (14-fold) post-onset predictions.
   - Conformal scores for inner bearing i come only from the other 13 inner bearings'
     OOF residuals (leave-one-bearing-out conformal; each bearing gets equal weight in
     the quantile).
   - Replay the full state machine on the 14 inner bearings for every combination of
     {stage-2 family x conformal binning x S1 x S2 x model_crit x hysteresis}.
   - Pick the combination minimising mean inner `C`.
3. **Tie-break toward simplicity (1-SE rule).**
   - Let `c*` be the best candidate and `SE*` the standard error of its per-bearing
     inner `C_b` (sd / sqrt(14)).
   - The tie set is every candidate with mean `C <= C(c*) + SE*`.
   - Within it, choose by the complexity order in section 5, lexicographically. Ties left
     after that go to the candidate with smaller EC, then the larger S2 (more
     conservative paging), then the lower grid index.
4. **Refit.** Refit the chosen stage 2 on all 14 bearings, with conformal scores from
   their 14 inner OOF residuals, and apply the result to the held-out bearing.

The selected configuration may differ by fold. The per-fold choices are reported, and
choice instability is reported as a finding. The configuration that would ship is the
modal choice when the procedure is applied to all 15 bearings; it is reported, but its
performance estimate is the nested one.

### 4.3 Ship decision (outer level, pre-registered)

The final recommendation follows this order:

1. **N vs R0.** N is ship-eligible only if all of these hold:
   - (a) The 95% bearing-bootstrap CI of `mean_b(C_b(N) - C_b(R0))` lies entirely below 0.
   - (b) MC count for N <= MC count for R0.
   - (c) O3 for N <= O3 for D.
   - (d) The sign of the N-vs-R0 point difference is unchanged when FD/LD are recomputed
     under V-hinge, V-2x and V-comm.
2. **N vs D (simplicity gate).** If the 95% CI of `mean_b(C_b(N) - C_b(D))` does not lie
   entirely below 0, OR condition (d) fails for N vs D, then **D ships** instead of N,
   provided D itself passes 1(a)-(c) against R0.
3. **Fallback.** If neither passes, nothing ships. The report states which condition
   failed. R1 is reported for context, under the same tests.

---

## 5. Finite candidate space (everything tuned by nested LOBO)

Complexity order is given left to right within each item (simplest first). It is applied
in the order of the items.

1. **Stage-1 family**: `ratio` < `dual-gate` < `ET-onset`.
   - `ratio`: `r5 >= G` for k consecutive; G in {1.2, 1.3}, k in {3, 5}. 4 configs.
   - `dual-gate` (Li 2015 / XJTU-SY 3-sigma): `z5 > S` AND `r5 > G`, k consecutive;
     S in {3, 4}, G in {1.2, 1.3}, k in {3, 5}. 8 configs.
   - `ET-onset`: ExtraTreesClassifier(200 trees, min_samples_leaf 20, max_features 0.3,
     n_jobs 3, seed 42+fold).
     - Trained on the label "t >= gt_on" of training bearings, with rows inside
       `[t_early, t_late]` dropped and bearing-equal weights.
     - Features: commissioning-ratio rolling-5 median, rolling-20 median and rolling-10
       std of rms, envelope_rms, peak, kurtosis, envelope_kurtosis, crest factor,
       spectral entropy, and the 5-10k/10-15k energy ratios, each as max over h/v.
     - No speed, load, condition, cycle or elapsed time.
     - Alarm: rolling-5 mean score > theta for k consecutive; theta in {0.5, 0.7},
       k in {3, 5}. 4 configs.
2. **Baseline reference**: `self` < `self + min60 fallback`, for every stage-1 family.
   This doubles item 1, giving 32 stage-1 configs in total.
3. **Degrading hysteresis**: `hard latch` < `clear if r5 < 1.1 for 30 rows`. 2 options.
4. **Stage-2 family** (trained on post-causal-FPT rows of training bearings, bearing-equal
   weights, no condition/speed/load). Order: `TSO` < `ET` < `HGB-q`.
   - Features: tso, log r5, log r5 - log_r5_at_fpt, log peak_r5_since_fpt, 10- and
     30-row slopes of log r5, kurtosis z5, log p5, log envelope-rms ratio.
   - `TSO`: `max(0, median_post_onset_life - tso)`, with conformal on log1p residuals.
   - `ET`: ExtraTreesRegressor(200 trees) on `log1p(min(RUL, 600))`;
     min_samples_leaf in {10, 20}, max_features in {0.3, 0.5}. 4 configs.
   - `HGB-q`: HistGradientBoostingRegressor(loss="quantile") at q = 0.1, 0.5, 0.9 on
     `log1p(RUL)`; max_iter 200, learning_rate 0.05, max_leaf_nodes 15,
     min_samples_leaf in {20, 40}. 2 configs.
   - That is 7 configs in total.
5. **Conformal binning**: `global` < Mondrian by predicted RUL {0-30, 30-60, 60-120, 120+}.
   Bins with fewer than 3 contributing bearings fall back to global.
6. **model_crit**: `q50 <= 30` < `q10 <= 30` at alpha = 0.1 < `q10 <= 30` at alpha = 0.2.
7. **S1** (faulty severity, r5) in {2.0, 3.0}.
8. **S2** (critical peak severity, p5) in {8, 6, 5, 4}, ordered most conservative first.

Fixed, not searched:
- The escalation persistence m = 3.
- The output horizon 120.
- RUL cap 600 for ET.
- All the weights in 4.1.

Grid size: 32 stage-1 configs. Downstream, 7 x 2 x 3 x 2 x 4 x 2 = 672 state-machine
replays per outer fold. These replays are cheap (vectorised per bearing).

### Compute budget (n_jobs <= 3)

| work | fits | estimate |
|---|---|---|
| ET-onset | (15 x 14 + 15) = 225 ET fits on about 9k rows | about 10 min |
| Stage-2 ET/HGB | 15 outer x 6 learned configs x (14 inner + 1) = 1350 fits on <= 3k rows (only for the fold's selected stage 1) | about 30-45 min |
| Replays and bootstrap | — | about 10 min |
| **Total** | | **< 1.5 CPU-hours** |

Planned scripts, all under `experiments/xjtu/v2/`:
- `onset_gt.py` (done)
- `causal.py`: indicators and the state machine
- `run_nested.py`: caches per-fold OOF to `v2/cache/`
- `report.py`: writes `v2/results/`

---

## 6. Reporting requirements

1. **Primary table.** C, MC, EC, FD, LD and EF for N, D, R0 and R1.
   - Each shows the bearing-mean, with a 95% CI from a **per-bearing bootstrap**: resample
     the 15 bearings with replacement, 2000 reps, seed 20261006, percentile intervals.
   - The **paired** differences N-R0, N-R1, N-D and D-R0 are also shown, each with its CI.
2. **Per-bearing table** (15 rows), with columns:
   - GT interval and degrading lead
   - delay (O1) and false episodes (O2)
   - faulty lead and critical lead
   - EC hours, reason_code at first critical
   - post-onset MAE
   - flags

   The table is shown for N and D, plus the critical lead and FA episodes for R0 and R1.
3. **Stratified summaries** by the `abrupt`, `baseline_contaminated` and `short_healthy`
   flags, and by condition. Abrupt bearings are listed as irreducible misses where
   applicable.
4. **GT robustness.** O1, O2, FD, LD and C recomputed under V-hinge, V-2x and V-comm, plus
   the GT point `t_on`, with the ranking of N, D, R0 and R1 under each.
5. **Secondary metrics.** P1-P3 (with TSO and constant baselines) and legacy section 3.6
   metrics, clearly labelled "comparison only".
6. **Selection diagnostics.**
   - The per-fold chosen configuration and the size of the 1-SE tie set.
   - The modal configuration.
   - The inner-vs-outer cost gap, which estimates optimism.
7. **Format.** Results are written to `experiments/xjtu/v2/results/protocol_results.json`
   and `per_bearing.csv`. No pooled-OOF tuning number may appear without its nested
   counterpart.

## Deviations

(none yet)
