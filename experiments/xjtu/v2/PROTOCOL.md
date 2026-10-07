FROZEN — do not edit after this point

# XJTU-SY health-state redesign: pre-registered evaluation protocol (v2, revision 2)

Status: **FROZEN 2026-10-06, before any v2 model (stage-2 learner, nested selection) was built
or scored.** Revision 2 addresses the red-team review (Appendix A lists every point and how it
was handled). The evaluator code is frozen together with this text:

| frozen artefact | sha256 |
|---|---|
| `experiments/xjtu/v2/causal.py` (indicators, state machine, cost, statistics) | `d40b8b1144afa2dd1c4e51ea908395a9cff3763bc020c8016fc65e612239e9a7` |
| `experiments/xjtu/v2/run_dummies.py` (dummy/reference scoring) | `e3eb56c33ced537b37a96855553ff222ce00e930d268a11e1da556e907422210` |
| `experiments/xjtu/v2/onset_gt.csv` (offline onset GT) | `649ae1060e2f2dec005507ab52ae63f843148a810ce10e21494e7ed83153d143` |

Any later change to these files or to this text is a deviation. It must be written, dated and
justified in the "Deviations" section at the end **before** the affected result is looked at,
and the affected result is then reported as exploratory, not confirmatory. The only change
allowed without demoting results is a crash fix in `causal.py` that does not alter any value
already recorded in section 4.5 (the dummies must be re-run and must reproduce those numbers).
The draft that the red team reviewed is kept at `v2/redteam/PROTOCOL.draft_pre_redteam.md`.

Scope rules: read-only on `src/`, `models/`, `tests/`, `outputs/xjtu_features.csv`; write only
under `experiments/xjtu/v2/`; no state-changing git commands; `n_jobs <= 3`; sklearn/xgboost
only (no lightgbm). All features are causal and computable from what `RealTimeRULPredictor`
keeps (first 20 commissioning rows plus rolling history of at most 60 rows), plus the small
extra per-machine state listed in section 2.5.

Design (merged from `design_literature-fpt`, `design_learned-onset`, `design_product-risk`):
- **Stage 1**: a causal, rule-based onset detector anchored to the machine's own
  commissioning baseline. It latches the start of degradation. (The learned ET-onset
  detector of the draft is removed; see A.8.)
- **Stage 2**: a post-onset RUL model with bearing-grouped conformal bounds.
- **faulty / critical**: driven by stage-2 output and/or physical severity, with ratchet
  semantics. Only a maintenance/replacement reset clears them. Severity alone can never
  raise critical (A.9).

---

## 1. Offline onset ground truth (GT)

### 1.1 Definition (fixed)

Computed per bearing from that bearing's full run-to-failure trajectory only, by
`experiments/xjtu/v2/onset_gt.py`, written to `onset_gt.csv` (hash above). The GT is never a
feature, never a training label for any candidate, and nothing is fit to it.

1. **Whole-life healthy level per channel.** `L_c` = median of the lowest 30% of channel `c`
   over the whole life (minimum 5 rows). Deliberately *not* the 20-row commissioning window.
2. **Health indicator (HI) families.**
   - Energy: `HI_E(t) = max_c x_c(t) / L_c`, c in {h_rms, v_rms}.
   - Envelope: `HI_ENV`, the same over {h_envelope_rms, v_envelope_rms}.
   - High-frequency energy: `HI_HF`, the same over `rms * sqrt(e_5-10k + e_10-15k)` per axis.
   Each HI is smoothed with a **centred** 5-row median (looks ahead on purpose).
3. **Permanent exceedance.** Onset = earliest row `t` with `HI(t) > 1.3` AND `HI > 1.3` on at
   least 90% of rows from `t` to failure.
4. **Primary onset point `t_on`** = permanent exceedance on `HI_E` (`gt_on_rul`).
5. **Uncertainty interval `[t_early, t_late]`** = min and max over four constructions:
   permanent exceedance on `HI_E`, on `HI_ENV`, on `HI_HF`, and the two-phase exponential
   fit at fused fitted excess delta = 0.3 (`design_learned-onset/onset_delta.csv`).
6. **GT variants** (single-point; `t_early = t_late` = the variant's onset row; a bearing
   with no onset under a variant falls back to the primary interval):
   - **ENV-only**, **HF-only**, **two-phase** (each one of the four constructions alone),
     and **V-2x** (permanent exceedance at 2.0x). These four are the **robustness set** used
     by ship gate G(c) (section 4.4).
   - **V-hinge** (least-squares hinge on log `HI_E`) and **t_on** (energy point): reported
     only. The hinge collapses to the first rows on drifting bearings (1_2, 2_2, 3_5).
   - **V-comm** (permanent exceedance against the 20-row commissioning level) is **dropped
     from all gates**: it is effectively the stage-1 detector with look-ahead (A.7). It may
     appear in an appendix table only.

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

Notes: 3_2 degrades in stages (>2x permanently only from RUL 68; earlier stage about
1031-1473). 3_4's HF onset (390) widens its interval. 1_4's energy HI rises only in the last
minutes (gt_on 5); its ENV/HF onsets are 39-42. The interval is label uncertainty and is
reported as such.

### 1.3 Bearing flags, eligibility and structurally undetectable bearings

No bearing is excluded from any reported metric. Flags (columns of `onset_gt.csv`):
- `baseline_contaminated`: commissioning median of `HI_E` > 1.15x the whole-life level, OR
  commissioning RMS > 1.5x the same-condition leave-one-out fleet reference. Flagged (7 of
  15, the majority case, not an edge case): 1_5, 2_1, 2_5, 3_2, 3_3, 3_4, 3_5. Unflagged (8):
  1_1, 1_2, 1_3, 1_4, 2_2, 2_3, 2_4, 3_1.
- `short_healthy`: GT early-onset row < 30. Flagged: 2_4, 3_5.
- `abrupt`: gt_on <= 30 min. Flagged: 1_4, 1_5, 2_4, 3_3.
- **Short-life bearings**: 2_4 (life 42) and 1_5 (life 52). The forced 20-row warm-up is
  48% and 38% of their life. They are reported in their own rows in every stratified table.

**Eligibility, per k (corrects the draft).** All run counters start at row 20 (the first
post-commissioning row). A stage-1 detector with persistence k cannot confirm before row
`20 + k - 1`: **row 22 for k = 3, row 24 for k = 5.** (`detector_eligible_row = 24` in
`onset_gt.csv` is the k = 5 value.) Bearing 3_5 has `gt_early_row = 23`, which is *before*
the k = 5 eligibility row; the draft's claim "no bearing affected" was wrong. Detection delay
is measured against `max(t_late, eligible_row(k))`; false-degrading terms count only rows
before `t_early`, so 3_5 under k = 5 simply cannot accrue them.

**Latency floor** (`causal.latency_floor`, written to `results/dummies/latency_floor.csv`).
Assuming the trigger is true from the GT late edge onward, the earliest possible detection
is `t_late + 2 (5-row trailing-median lag) + k - 1`, and the earliest possible critical is
`t_late + 2 + max(k, m) - 1` (counters run concurrently, section 2.2). Resulting floors:

| bearing | best critical RUL, k=3 | best critical RUL, k=5 | consequence |
|---|---|---|---|
| 1_4 | 1 | -1 (impossible) | MC unavoidable for every rule; LC floor 0.97 / 1.0 |
| 2_4 | 7 | 5 | MC unavoidable; LC floor 0.77 / 0.83 |
| 1_5 | 11 | 9 | MC unavoidable at k = 5; LC floor 0.63 / 0.70 |
| 3_3 | 24 | 22 | partial LC shortfall unavoidable (0.20 / 0.27) |
| others | >= 34 | >= 32 | no structural floor |

**Structurally undetectable onsets (missed-onset term).** The missed-onset term MO is scored
only for bearings with `gt_late_rul >= 10 + (k_max - 1) + 2 = 16`, with `k_max = 5`, the
largest k in the grid. One fixed set for every candidate keeps paired differences paired.
**Excluded from MO: 1_4 (5), 2_4 (11), 1_5 (15).** They are listed as "structurally
undetectable at RUL >= 10" in every report. All their other cost terms still count.

### 1.4 Circularity: what the GT is and is not used for

1. The GT cannot be computed causally (centred smoothing, whole-life level, persistence to
   failure, global fit).
2. **Known shared statistic family (stated plainly).** The primary GT (`max(h,v)` RMS over
   whole-life level, 1.3x) and the stage-1 ratio rule (`max(h,v)` RMS over commissioning
   median, 1.3x) are the same statistic family. The 1.3/k3 rule has mean LD of a few minutes
   against it (dummy `onset_only`: LD 0.064 h). FD and LD therefore partly reward copying the
   GT. Consequences fixed here:
   - GT-based terms (FD, FDh, LD) carry a small share of C; the failure-anchored terms (LC,
     EP, ECh, EFh) carry the rest.
   - Ship gate G(c) requires the sign of every claimed difference to hold under the
     **ENV-only, HF-only, two-phase and V-2x** GT variants. Three of these are not RMS-energy
     permanent exceedance at 1.3x.
   - V-comm is not a robustness check (it is the detector with look-ahead) and is not used.
3. Nothing is trained on the GT. Stage 1 is rule-only; stage 2 trains on rows after the
   *causal* detector's onset with true RUL as target.
4. **Known optimism (A.15).** The stage-1 defaults of D (ratio 1.3, k 3, the `r5` RMS-ratio
   statistic) and the GT hyperparameters (1.3x, 90% persistence, lowest 30%, centred 5-row
   median) were chosen after the previous round looked at all 15 bearings. D's defaults are
   therefore **not untuned**; comparisons involving D or the GT carry this optimism. Two
   mitigations are pre-registered: (i) the D' sensitivity reference (ratio 1.2, k 5) is
   reported beside D everywhere, and (ii) if a conclusion about D (vs R0) does not hold for
   D' as well, the conclusion is reported as "conditional on defaults selected with all
   bearings visible".

---

## 2. Health-state semantics the model will output

### 2.1 Causal quantities (per machine, per snapshot t) — `causal.indicators`

- `med20_c`, `MAD20_c`: median and MAD of channel c over the 20 commissioning rows.
- Energy ratio `r_t = max_c rms_c(t) / ref_c`, c in {h, v}.
  - `ref_c = med20_c` (`self`).
  - `fallback`: from row 60 on, `ref_c = min(med20_c, m60_c)` when `m60_c < 0.9 * med20_c`,
    with `m60_c` = minimum trailing-10 median of `rms_c` over rows 0..59. Before row 60 the
    self reference is used (the fallback is not knowable earlier).
- Robust z: `z_t = max_c (rms_c - ref_c) / max(1.4826 * MAD20_c, 0.02 * ref_c)`.
- Peak ratio: `p_t = max_c peak_c / med20(peak_c)`.
- Trailing 5-row medians: `r5`, `z5`, `p5`.
- Warm-up: for t < 20 the state is `healthy` with the `warming_up` warning (as today).

### 2.2 States and escalation — `causal.replay`

**Counter arithmetic (pre-registered exactly).** Three run counters are kept: `trigger_run`
(stage-1 trigger), `faulty_run` (faulty condition), `critical_run` (critical condition). All
start counting at row 20 and count **concurrently and independently** of whether the onset
latch O has fired. O latches at the first row where `trigger_run >= k`. Faulty is entered at
a row where O = 1 and `faulty_run >= m`; critical where O = 1 and `critical_run >= m`;
m = 3. Hence O, faulty and critical can all become true in the same snapshot, and an abrupt
bearing can go healthy -> critical directly. The earliest possible entry is row
`20 + max(k, m) - 1`.

| state | entry condition | alert | promise to the engineer |
|---|---|---|---|
| **healthy** | O = 0 | none | "No detectable departure from this machine's own baseline." No minimum time to failure is promised (section 2.3). |
| **degrading** | O = 1 | low | "Damage has started; create a work order." |
| **faulty** | O = 1 AND run of (`q50 <= 60` OR `r5 >= S1` OR `p5 >= S2`) | medium | "Advanced damage; replace at the next planned stop; do not run unattended." |
| **critical** | O = 1 AND run of (`model_crit` OR (`p5 >= S2` AND `q50 <= 60`)) | high + email page | "Failure possible within 30 min; stop or replace now." |

- Peak severity alone escalates only to **faulty**. For critical it must coincide with a
  stage-2 median of at most 60 min (A.9: causal p5 >= 4 is reached at RUL 193 on 2_3 and at
  RUL 70-95 on 1_2, 2_2, 2_5, 3_1).
- `model_crit` is a candidate choice: `q50 <= 30`, or `q10 <= 30` at alpha in {0.1, 0.2}.
  **`q10` is used for a row only if the conformal quantile of that row's bin is computed
  from at least 5 calibration bearings; otherwise that row uses `q50 <= 30`** (A.2, A.17).
- Every non-healthy state carries `reason_code` in {onset, rul, severity}; for critical,
  "severity" means the `p5 AND q50` path.

### 2.3 RUL and probability outputs (output contract; corrected, A.10)

- **O = 0 (healthy):**
  - `rul_estimate_kind = "unknown_no_onset"`, `predicted_rul_minutes = null`, interval
    `null`. No lower bound is promised: 1_4 is healthy at RUL 5 and 2_4 until about RUL 8.
  - `failure_within_horizon_probability = p0`, the fleet rate P(RUL <= 120 | O = 0, row >= 20),
    estimated on the training bearings of each outer fold with equal bearing weight. Plus a
    `no_onset` warning.
  - Calibration of `p0` is reported per held-out bearing (mean `p0` vs the observed fraction
    of that bearing's O = 0 rows with RUL <= 120) and as a bearing-averaged Brier score.
- **O = 1:** `rul_estimate_kind = "post_onset"`, `predicted_rul_minutes = q50`, interval
  `[q10, q90]` (bearing-grouped conformal), `failure_within_horizon_probability` = P(RUL <= 120)
  interpolated from the conformalized quantiles (q10/q50/q90 and the 0.05/0.95 extensions,
  linear in the quantile level, clipped to [0, 1]).
- `within_horizon` = state in {faulty, critical}.
- **Integration item (out of scope here).** `src/kpi/calculations.py` and
  `src/alerts/live.py` consume RUL numerically; a `null` RUL for healthy machines needs
  handling there before anything ships.

### 2.4 Latch, ratchet and reset

- **degrading** clears back to healthy only under the hysteresis candidate (`r5 < 1.1` for 30
  consecutive rows), and only while `max_state_reached == degrading`. After a clear,
  `trigger_run` restarts from 0. A clear is logged "transient cleared" and ends the episode.
  Under the hard-latch candidate it never clears.
- **faulty / critical ratchet.** `max_state_reached` is monotone; the displayed state is never
  demoted. A receded condition is annotated `condition_receded`.
- **Reset**: only a maintenance record of type replacement (or with `resets_baseline`) or an
  explicit `reset_machine`. Alert acknowledgement does not reset. "Sensor remount: re-baseline
  only" keeps `max_state_reached`. These src/ changes are integration work, out of scope.
- Under the ratchet the operator gets **one** page per episode. This is why the primary cost
  scores paging by entry events (section 4.1), not by rows.

### 2.5 New per-machine state (minimal, must persist)

| field | meaning |
|---|---|
| `onset_latched` | the latch O |
| `fpt_row` | snapshot index of the confirmed onset |
| `log_r5_at_fpt` | log `r5` at onset |
| `peak_r5_since_fpt` | running maximum of `r5` since onset |
| `trigger_run`, `faulty_run`, `critical_run` | the three concurrent counters |
| `calm_run` | consecutive calm snapshots (hysteresis) |
| `max_state_reached` | ratchet level |
| `m60_h`, `m60_v` | fallback reference minima (only if the fallback is selected) |

10-12 scalars. Rolling windows of <= 60 rows already exist.

---

## 3. Metrics (all on outer leave-one-bearing-out predictions)

### 3.1 Evaluation setup

- Outer LOBO: LeaveOneGroupOut over sorted bearing ids, 15 folds, the order of
  `experiments/xjtu/harness.py`.
- Replay is causal per bearing with exactly `causal.replay`. All 9216 rows are scored;
  warm-up rows are healthy for every model.
- Per-bearing metric first; pooled = equal-weight mean over bearings. Row-pooled values are
  secondary only (3_1 and 3_2 hold about 54% of rows).

### 3.2 Reference models (same rows, same folds)

- **R0 (production).** `experiments/xjtu/results/baseline_oof.csv`. State = healthy unless
  `warning`; then critical <= 30, faulty <= 60, degrading <= 120 on `rul_pred` (no ratchet).
  R0 has early critical entries on 2_3 (RUL 151) and 2_5 (RUL 144). Note: R0's OOF for an
  inner bearing came from a model trained with the outer held-out bearing included; R0 is a
  fixed, untuned comparator, so this is accepted and stated.
- **R1 (previous-round candidate).** `experiments/xjtu/results/combined_oof.csv`, same
  mapping. **R1 cannot express critical**: its `rul_pred` never falls below 30.9 min, so it
  never enters critical. It is reported for context only and is never a gate comparator.
- **D (primary comparator; simplest new pipeline; fixed defaults, not untuned — see 1.4.4).**
  - Stage 1: ratio rule `r5 >= 1.3`, k = 3, `self` reference, hard latch.
  - Stage 2: TSO (`max(0, L - tso)`, L = bearing-equal median post-onset life of the training
    bearings), global bearing-grouped conformal.
  - `model_crit = q50 <= 30` (changed from the draft's `q10 <= 30`, which collapsed to
    "critical at onset", A.1). S1 = 2.0, S2 = off.
  - D is a member of the candidate grid (section 5).
- **D' (sensitivity reference)**: D with stage 1 = ratio 1.2, k = 5.
- **Dummies** (section 4.5): `never`, `fire_at_eligibility`, `fire_at_eligibility_critical`,
  `onset_eq_critical`, `onset_only`.
- **N**: the nested-selected pipeline (sections 4-5).

### 3.3 Onset / degrading metrics

| id | metric |
|---|---|
| O1 | Detection delay per bearing, against `max(t_late, eligible_row(k))`: 0 inside `[t_early, t_late]`, positive if later, negative if earlier. Distribution and number of bearings with \|delay\| <= 15. |
| O2 | Pre-onset false degrading: episodes starting before `t_early` and non-healthy hours before `t_early`; pooled per 1000 pre-onset machine-hours and number of bearings affected. Also against `t_on`. |
| O3 | Missed onset on MO-scorable bearings (no degrading-or-above at RUL >= 10), listed by name. The 3 structurally undetectable bearings are listed separately. |
| O4 | Degrading lead = RUL at detection. Median, minimum, number with lead >= 30. |

### 3.4 Faulty / critical timing: entry-event metrics (replace the draft's row-level F2-F4)

| id | metric |
|---|---|
| F1 | **First-critical-entry RUL** per bearing, binned `never`, `<10 (miss)`, `10-60 (useful)`, `>60 (early page)`, with bearing names and the `reason_code` at entry. |
| F2 | The same for **first faulty-or-above entry**. |
| F3 | MC event count = bearings with critical never entered or entered at RUL < 10; EP count = bearings with first critical entry at RUL > 60. Both listed by name. |
| F4 | Hours in critical while RUL > 60 and hours in faulty-or-above while RUL > 120 (uncapped, descriptive), split by reason_code. |
| F5 | Demotion fraction (rows displayed below the previous row within an episode). Must be 0 for N, D, D'; reported for R0, R1. |

Row-level critical precision/recall is not reported for ratcheted pipelines (it is
mechanical under the ratchet).

### 3.5 Post-onset RUL and intervals

Rows: each model's own post-onset rows, plus the common like-for-like set of rows after
`t_late`. R0/R1 use `rul_pred`.

| id | metric |
|---|---|
| P1 | Bearing-averaged MAE, and MAE on rows with RUL <= 60, against (i) constant = median post-onset RUL of training rows and (ii) TSO, both fit inside the outer fold. |
| P2 | **Coverage, bearing-level (A.17).** Rows are autocorrelated, so the confirmatory check uses one event per bearing: (a) `true RUL >= q10` at the bearing's FPT row, and (b) the same at its first post-onset row with true RUL <= 60. The count over bearings with an onset gets an exact Clopper-Pearson 95% interval, compared with the nominal 0.90 (alpha 0.1) or 0.80 (alpha 0.2). Row-level coverage per bearing, per predicted-RUL bin (0-30, 30-60, 60-120, 120+), and mean width are descriptive. |
| P3 | Late-prediction rate (`q50 > true` on rows with true <= 30). |
| P4 | **Diagnostic**: q10 and q50 at the onset row, per bearing, for every model with intervals. |

Conformal caveat (stated): with leave-one-bearing-out calibration over 13 inner bearings,
the 10% bearing-equal quantile is roughly the 1.3rd-worst bearing. Inner bearing i's
calibration residuals come from models whose training sets included i (a jackknife-style
dependence). Coverage claims are therefore approximate and checked only at the bearing
level.

### 3.6 Legacy 120-min metrics (comparison only, never used for selection)

AP and ROC-AUC of `failure_within_horizon_probability` vs RUL <= 120 (pooled and
within-bearing mean). Precision, recall, F1 and false-alarm rows with alarm = state >= faulty
and alarm = state >= degrading.

---

## 4. PRIMARY selection rule

All cost code is `causal.bearing_cost` (frozen). Weights and caps are fixed and not tuned.

### 4.1 Per-bearing composite cost (lower is better)

```
C_b = 10   * LC_b      # graded late/missed critical:
                       #   1                         if critical is never entered
                       #   max(0, 30 - lead)/30      lead = true RUL at first critical entry
                       #   (0 if lead >= 30; 0.7 at lead 9, 0.63 at lead 11: no cliff)
    + w_EP * EP_b      # early page: 1[first critical entry at RUL > 60]; w_EP = 5 primary
    + 0.5  * min(ECh_b, 4)    # hours in critical while RUL > 60 (small, capped)
    + 0.5  * FD_b             # non-healthy episodes starting before t_early
    + 0.25 * min(FDh_b, 8)    # non-healthy hours before t_early (duration term)
    + 1    * min(LD_b, 4)     # onset lateness hours: (t_det - t_late)+/60, t_det = start of the
                              #   first episode still active at/after t_late (failure row if none)
    + 0.25 * min(EFh_b, 4)    # hours faulty-or-above while RUL > 120
C   = mean over bearings of C_b (equal bearing weight)
```

- **Caps** (A.6): `min(h, cap)` per bearing (cap 4 h; 8 h for FDh), chosen over `log1p`
  because the cap keeps units interpretable. Without caps, 3_2 alone gave R0 an LD of 13.5 h.
- **MC:EP exchange rate (product decision, pre-registered).** Primary 10:5. Every ranking
  claim must also hold at **10:3 and 10:10** (`causal.W_EP_SENS`).
- **Stage-1 cost** `C1_b = 10 * MO_b + 0.5 * FD_b + 0.25 * min(FDh_b, 8) + min(LD_b, 4)`,
  with MO_b = 1[no degrading-or-above at RUL >= 10], scored only on the 12 MO-scorable
  bearings (1.3). The duration term makes "fire at eligibility" cost about 1.2 instead of
  0.5 (section 4.5).

### 4.2 Eligibility constraints applied to every candidate (inner and outer)

A candidate is **ineligible** if any of these holds on the bearings being evaluated:
- **E1 Exchange constraint (A.1)**: `EP(c) - EP(R0) > MC(R0) - MC(c)`, i.e. it pages early
  on more additional bearings than the misses it removes relative to R0 (event counts, F3).
- **E2 Dummy sanity (A.3, A.20)**: its mean C is not strictly below the mean C of every
  dummy in 4.5, at **each** of w_EP = 3, 5, 10, on the same bearings. For stage-1 selection:
  its mean C1 is not strictly below that of `never` and `fire_at_eligibility`.
- **E3**: any demotion within an episode (structural; should never trigger).

If no candidate is eligible in a fold, that fold uses D's configuration, and this is
logged and reported as a finding.

### 4.3 Nested selection (inside each outer fold; the held-out bearing is never touched)

1. **Stage 1.** Score every stage-1 candidate (24, section 5) on the 14 training bearings with
   mean C1 (rules have no fitted parameters, so this is honest). Apply E2 (stage-1 form). Pick
   by the paired 1-SE rule of step 3, using C1.
2. **Stage 2 and state thresholds.** With the chosen stage 1 fixed:
   - compute its causal FPT on each training bearing;
   - for each stage-2 family/config, produce inner-LOBO (14-fold) post-onset predictions;
   - conformal scores for inner bearing i come only from the other 13 inner bearings' OOF
     residuals (bearing-equal weights in the quantile);
   - replay the full state machine on the 14 inner bearings for all 672 downstream
     combinations (section 5); apply E1-E3; pick by step 3 using C (w_EP = 5).
3. **Paired 1-SE tie rule (A.12).** Let `c*` minimise the mean cost among eligible
   candidates. For each eligible candidate c, take the per-bearing paired differences
   `d_b = C_b(c) - C_b(c*)`. The tie set is every c with `mean(d) <= SE(d)`, where
   `SE(d) = sd(d) / sqrt(14)`. Within the tie set choose by the complexity order of section 5
   (lexicographic in item order); then fewer EP; then smaller capped ECh; then the more
   conservative S2 (off > 8 > 6 > 4); then lower grid index. **Tie-set sizes are reported per
   fold.**
4. **Refit** the chosen stage 2 on all 14 bearings (conformal from their 14 inner OOF
   residuals) and apply it to the held-out bearing; replay with the chosen state thresholds.
5. `p0` (2.3) is estimated on the 14 training bearings under the chosen stage 1.

The selected configuration may differ by fold; per-fold choices and instability are
reported. The configuration that would ship is the modal choice when the same procedure is
run on all 15 bearings (reported; its performance estimate is the nested one).

### 4.4 Ship decision (outer level, pre-registered)

**Superiority gate G(X better than Y)**, on the 15 outer per-bearing paired differences
`d_b = C_b(X) - C_b(Y)` (w_EP = 5 unless stated):
- **G(a)** The 95% per-bearing percentile bootstrap CI of `mean(d)` (2000 reps, seed 20261006)
  lies entirely below 0.
- **G(b)** Exact one-sided paired sign-flip permutation test (all 2^15 = 32,768 sign
  patterns, statistic = mean) gives p < 0.05 (`causal.signflip_p`).
- **G(c)** The sign of `mean(d)` stays negative under all of:
  - w_EP = 3 and w_EP = 10;
  - each GT variant in the robustness set {ENV-only, HF-only, two-phase, V-2x};
  - every single-bearing deletion (15 leave-one-out means) and the deletion of {3_1, 3_2}
    together (`causal.influence_signs`);
  - the 8 unflagged (`baseline_contaminated = F`) bearings only.

**Product gates P(X)** for a pipeline X in {N, D}:
- **P1** G(X better than R0).
- **P2** EP count(X) <= EP count(R0) = 2 (no increase in early pages over production).
- **P3** MC event count(X) <= MC event count(R0) = 8.
- **P4** E1 and E2 hold on the 15 outer bearings (beats every dummy at all three w_EP).
- **P5** O3(X) <= O3(D) on the MO-scorable bearings.
- **P6** Demotion fraction 0.

**Decision order:**
1. **N ships** if P(N) holds AND G(N better than D) holds.
2. Otherwise **D ships** if P(D) holds. D's result is reported beside D'; if P(D') fails,
   the D claim is labelled "conditional on defaults chosen with all bearings visible" (1.4.4).
3. Otherwise **nothing ships**, and the report names every failed condition. R1 is reported
   for context only.

### 4.5 Frozen dummy and reference scores (recorded before any model was built)

Produced by `python experiments/xjtu/v2/run_dummies.py` with the frozen `causal.py`
(`results/dummies/summary.csv`, `per_bearing.csv`, `latency_floor.csv`). Dummy definitions
(stage-1 k = 3, eligibility row 22):
- `never`: always healthy.
- `fire_at_eligibility`: degrading from row 22, never escalates.
- `fire_at_eligibility_critical`: critical from row 22.
- `onset_eq_critical`: ratio 1.3/k3 latch, and critical at onset (the degenerate state the
  draft's D collapsed to).
- `onset_only`: ratio 1.3/k3 latch, never escalates.

| model | C (w_EP 5) | C (w_EP 3) | C (w_EP 10) | C1 | sum LC | MC events | EP | mean ECh | FD | mean FDh | mean LD | mean EFh | MO | C, ENV | C, HF | C, two-phase | C, V-2x | C without 3_1, 3_2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R0 | **6.740** | 6.474 | 7.407 | 0.517 | 8.1 | 8 | 2 (2_3@151, 2_5@144) | 0.154 | 5 | 0.072 | 0.332 | 0.318 | 0 | 6.834 | 7.088 | 6.934 | 6.760 | 6.290 |
| R1 | 10.574 | 10.574 | 10.574 | 0.546 | 15.0 | 15 | 0 | 0 | 5 | 0.210 | 0.327 | 0.114 | 0 | 10.644 | 10.899 | 10.736 | 10.542 | 10.355 |
| never | 11.598 | 11.598 | 11.598 | 9.598 | 15.0 | 15 | 0 | 0 | 0 | 0 | 1.598 | 0 | 12 | 11.643 | 11.853 | 11.707 | 11.257 | 11.371 |
| fire_at_eligibility | 11.216 | 11.216 | 11.216 | 1.216 | 15.0 | 15 | 0 | 0 | 15 | 2.862 | 0 | 0 | 0 | 11.240 | 11.236 | 11.271 | 11.324 | 11.018 |
| fire_at_eligibility_critical | 7.404 | **5.670** | 11.737 | 1.216 | 0.4 | 0 | 13 | 2.246 | 15 | 2.862 | 0 | 1.861 | 0 | 7.428 | 7.424 | 7.459 | 7.512 | 6.927 |
| onset_eq_critical | **5.371** | **4.171** | 8.371 | 0.098 | 2.5 | 2 | 9 | 0.946 | 1 | 0.001 | 0.064 | 0.533 | 0 | 5.795 | 6.162 | 5.575 | 5.779 | 5.135 |
| onset_only | 10.098 | 10.098 | 10.098 | 0.098 | 15.0 | 15 | 0 | 0 | 1 | 0.001 | 0.064 | 0 | 0 | 10.522 | 10.890 | 10.302 | 10.506 | 10.113 |

`onset_eq_critical` early pages: 1_2@125, 1_3@81, 2_2@106, 2_3@262, 2_5@188, 3_1@145,
3_2@1260, 3_4@93, 3_5@91.

**What these numbers already establish (honest reading, recorded before modelling):**
- R0 is weak on misses: 7 critical entries at RUL < 10 plus 1 never (MC events 8).
- **The cost alone does not stop "critical = onset"**: `onset_eq_critical` has a lower C than
  R0 at 10:5 (5.37 vs 6.74) and at 10:3, and `fire_at_eligibility_critical` beats R0 at 10:3.
  They are stopped by the **constraints**, not by C: E1 (`onset_eq_critical`: +7 EP vs 6 MC
  removed; `fire_at_eligibility_critical`: +11 EP vs 8 removed), P2 (EP 9 and 13 > 2), and
  the 10:10 rate (8.37 and 11.74 > 7.41). This is why E1, P2 and the three-rate requirement
  are binding parts of the rule and not optional reporting.
- C1 now ranks trigger-happy stage-1 behaviour correctly: `fire_at_eligibility` (1.216) is
  worse than `onset_only` (0.098) and R0 (0.517).
- E2 is strict on purpose: R0 itself fails it (6.740 > 5.371 at 10:5), so a shipped pipeline
  must beat the degenerate pagers on cost as well as on the constraints.
- Every future result table must show these seven rows beside N, D and D'.

---

## 5. Finite candidate space (everything tuned by nested LOBO)

Complexity order is simplest first within each item, applied in item order.

1. **Stage-1 family**: `ratio` < `dual-gate`. (ET-onset removed, A.8.)
   - `ratio`: `r5 >= G` for k consecutive; G in {1.3, 1.2}, k in {3, 5}. 4 configs.
   - `dual-gate`: `z5 > S` AND `r5 > G`, k consecutive; S in {3, 4}, G in {1.3, 1.2},
     k in {3, 5}. 8 configs.
2. **Baseline reference**: `self` < `self + min60 fallback`. Stage 1 total: 12 x 2 = **24**.
3. **Degrading hysteresis**: `hard latch` < `clear if r5 < 1.1 for 30 rows`. 2.
4. **Stage-2 family**, trained on post-causal-FPT rows of training bearings with
   bearing-equal weights, no condition/speed/load/cycle/elapsed time: `TSO` < `ET` < `HGB-q`.
   - Features (ET, HGB-q): tso, log r5, log r5 - log_r5_at_fpt, log peak_r5_since_fpt, 10-
     and 30-row OLS slopes of log r5, kurtosis z5 (max h/v, commissioning-referenced),
     log p5, log envelope-rms ratio (max h/v over commissioning median).
   - `TSO`: `q50 = max(0, L - tso)`; L as in D.
   - `ET`: ExtraTreesRegressor(200 trees, n_jobs 3, seed 42 + fold) on
     `log1p(min(RUL, 600))`; min_samples_leaf in {10, 20} x max_features in {0.3, 0.5}. 4.
   - `HGB-q`: HistGradientBoostingRegressor(loss="quantile") at q in {0.1, 0.5, 0.9} on
     `log1p(RUL)`; max_iter 200, learning_rate 0.05, max_leaf_nodes 15, min_samples_leaf in
     {20, 40}, seed 42 + fold. 2 configs (3 fits each).
   - 7 configs in total.
   - **Conformal bounds**, in log1p space with bearing-equal weighted quantiles:
     - TSO, ET: `q50 = pred`; `q_lo = expm1(log1p(pred) + Q_alpha(e))`,
       `q_hi = expm1(log1p(pred) + Q_{1-alpha}(e))`, e = log1p(true) - log1p(pred).
     - HGB-q: `q50` = the 0.5 model; one-sided CQR for each bound:
       `q_lo = expm1(lo - Q_{1-alpha}(lo - y))`, `q_hi = expm1(hi + Q_{1-alpha}(y - hi))`.
     - All bounds clipped at 0; `q10 <= q50 <= q90` enforced by sorting.
5. **Conformal binning**: `global` < Mondrian by predicted q50 in {0-30, 30-60, 60-120, 120+}.
   **A bin needs residuals from at least 5 distinct calibration bearings**; otherwise it falls
   back to global. The q10 rule of 2.2 uses the same 5-bearing test.
6. **model_crit**: `q50 <= 30` < `q10 <= 30` at alpha 0.1 < `q10 <= 30` at alpha 0.2.
7. **S1** (faulty, r5) in {3.0, 2.0} (more conservative first).
8. **S2** (peak; faulty trigger, and critical only with q50 <= 60) in {off, 8, 6, 4}.

Fixed, not searched: m = 3; horizon 120; RUL cap 600 for ET; all weights and caps in 4.1;
calm rule 1.1/30 rows.

Grid: 24 stage-1 configs; downstream 2 x 7 x 2 x 3 x 2 x 4 = **672** replays per outer fold
on the inner bearings. D = {ratio 1.3/k3, self, hard latch, TSO, global, q50, S1 2.0, S2 off}.

### 5.1 Compute budget and time cap (corrected, A.18)

| work | count | estimate (n_jobs 3) |
|---|---|---|
| Stage-2 fits | per training set 4 ET + 2 x 3 HGB = 10 fits; 15 outer x (14 inner + 1 refit) = 225 training sets -> **2,250 fits**, plus 160 for the all-15 modal run | about 60-75 min |
| Stage-1 scoring | 24 configs x 15 folds, vectorised | < 1 min |
| State replays | 15 x 14 x 672 = 141,120 bearing replays (~87M row-steps). Hard-latch replays are vectorised (`run_length` + cumulative max); hysteresis replays use the O(n) loop in `causal.replay` | about 2-5 min |
| Bootstrap, sign-flip (2^15), influence | — | < 2 min |

- **Hard wall-clock cap: 3 h** for `run_nested.py` end to end.
- **Pilot first**: run outer fold 0 completely and time it. If the projected total exceeds
  2.5 h, shrink the grid **before any other fold runs**, in this pre-authorised order:
  (1) S2 -> {off, 6}; (2) model_crit -> {q50, q10 at alpha 0.1}; (3) ET -> {leaf 20, mf 0.3}
  only. Record the shrink in Deviations; it does not demote results because it is pre-set
  and outcome-blind.
- Per-fold OOF predictions are cached to `v2/cache/`.

Scripts (all under `experiments/xjtu/v2/`): `onset_gt.py` (done), `causal.py` (frozen),
`run_dummies.py` (frozen, run), `run_nested.py` (to write; must import every indicator,
replay, cost and statistic from `causal.py` and must not re-implement them), `report.py`
(writes `v2/results/`).

---

## 6. Reporting requirements

1. **Primary table**: C (w_EP 5, and 3 and 10), LC sum, MC events, EP, capped ECh, FD, FDh,
   LD, EFh for N, D, D', R0, R1 and the five dummies. Bearing-mean with 95% per-bearing
   bootstrap CI. Paired differences N-D (primary), N-R0, D-R0, D'-R0 and N-R1 with bootstrap CI
   **and** exact sign-flip p.
2. **Gate table**: each G(a), G(b), G(c) item and each P1-P6, pass/fail, for N and D (and D').
3. **Per-bearing table** (15 rows): GT interval, latency floor, degrading lead, delay,
   false episodes, first faulty and first critical entry RUL with bin and reason_code, capped
   ECh, post-onset MAE, q10/q50 at onset, flags, for N, D, D'; critical entry, MC/EP and FA
   episodes for R0 and R1.
4. **Stratified**: N vs D and D vs R0 separately on flagged (7) and unflagged (8) bearings; by
   `abrupt`, `short_healthy`, short-life (2_4, 1_5), and condition. Structurally undetectable
   bearings (1_4, 2_4, 1_5) and MC-unavoidable bearings are listed as such.
5. **Influence**: the 15 leave-one-bearing-out means and the {3_1, 3_2}-dropped mean for every
   paired difference in item 1.
6. **GT robustness**: O1, O2, FD, LD and C under ENV-only, HF-only, two-phase, V-2x (gate set)
   and V-hinge, t_on (reported only), with the ranking of N, D, D', R0 under each.
7. **Secondary**: P1-P4 and 3.6, labelled "comparison only"; `p0` calibration (2.3).
8. **Selection diagnostics**: per-fold chosen configuration, eligible-set size, tie-set size,
   folds that fell back to D (4.2), modal configuration, and the inner-vs-outer cost gap.
9. **Format**: `v2/results/protocol_results.json` and `per_bearing.csv`. No pooled-OOF
   tuning number may appear without its nested counterpart, and every table shows the 4.5
   dummy rows.

---

## Appendix A. Disposition of red-team points (2026-10-06)

Evidence for the red-team review: `v2/redteam/quick_checks.py`, `v2/redteam/tso_d_check.py`.
Their key numbers were reproduced before revising (degenerate "critical at onset" C 3.83 /
MC 2 vs R0 C 6.69 / MC 8 under the draft cost; "fire at row 20" C1 0.50 vs ratio 1.3/k3
1.43). Status per point: **Accepted**, **Accepted (modified)**, or **Partly rejected**.

| # | point | status | how it was handled |
|---|---|---|---|
| 1 | "critical = onset" can win | Accepted (modified) | EC hours replaced by per-bearing early-page event EP (weight 5) plus a small capped hours term; MC:EP rates 10:3/10:5/10:10 pre-registered and required (G(c)); hard exchange constraint E1 vs **R0** (the fixed production comparator; using D would let a weak D loosen it); P2 "no more EPs than R0"; D's `model_crit` changed to `q50 <= 30`. **Recorded finding (4.5): with the red team's suggested weight, C alone still prefers `onset_eq_critical` to R0 at 10:5 and 10:3; it is blocked by E1, P2 and the 10:10 rate.** I kept weight 5 rather than raising it, because the rate is a product decision and is now tested at three values instead of being tuned until the dummy loses. |
| 2 | Drop or rebuild `q10 <= 30` | Accepted (modified); partly rejected | q10 is used only where the row's conformal bin has >= 5 calibration bearings, else q50; q10 at onset per bearing is reported (P4); D uses q50. **Rejected part**: q10 options are not removed from the grid. The global bin (13 bearings) passes the 5-bearing test, so a degenerate global q10 stays a candidate. It is now penalised by EP, E1, P2 and the multi-rate rule, and removing it before measuring would pre-judge the result. |
| 3 | C1 rewards trigger-happy detectors | Accepted | Duration term `0.25 x min(h before t_early, 8)` added to C1 and C, episode count kept. Dummy sanity E2. Result: `fire_at_eligibility` C1 = 1.216 > `onset_only` 0.098 and R0 0.517. |
| 4 | MO relative to achievable | Accepted (modified) | MO scored only if `gt_late_rul >= 10 + (k-1) + 2`. Modification: one fixed set at k_max = 5 (threshold 16) for every candidate, so paired differences stay paired. Excluded: 1_4, 2_4, 1_5. |
| 5 | Graded MC; latency floor | Accepted | LC = `max(0, 30 - lead)/30`, 1 if never. Latency floors per k computed and tabulated (1.3); MC unavoidable on 1_4, 2_4 (and 1_5 at k = 5). The binary MC event count is kept for gates P3/E1. |
| 6 | Cap hour terms; drop 3_1/3_2 | Accepted | `min(h, 4)` (FDh `min(h, 8)`), pre-registered over log1p. Leave-one-bearing-out influence and the {3_1, 3_2} drop are in G(c). |
| 7 | Circularity of stage-1 vs GT | Accepted | V-comm dropped from gates; robustness set = ENV-only, HF-only, two-phase, V-2x; circularity stated in 1.4. V-2x is the same energy family at a different level; it is kept in the set as a level check, and three non-1.3x-energy variants carry the independence. |
| 8 | ET-onset circular and 3-level nesting | Accepted | Removed from the candidate space. No exploratory version is pre-registered here. |
| 9 | p5 not specific to imminent failure | Accepted | p5 >= S2 escalates to faulty; critical requires `p5 >= S2 AND q50 <= 60`. S2 grid {off, 8, 6, 4}; 5 dropped (between 4 and 6 and adds no distinct regime given the 2_3/1_2/2_2/2_5/3_1 ranges); `off` added as the simplest option. |
| 10 | Healthy-state output contract | Accepted | `predicted_rul_minutes = null`, `rul_estimate_kind = "unknown_no_onset"`, probability = OOF fleet rate p0 with calibration reported; src/kpi and src/alerts flagged as an integration item. |
| 11 | Entry-event metrics | Accepted | F1-F3 now entry bins <10 / 10-60 / >60 with names and reason codes, for critical and faulty; row precision/recall removed. |
| 12 | Paired SE in the 1-SE rule | Accepted | Tie set uses `mean(d) <= SE(d)` of paired differences to c*; tie-set sizes reported. |
| 13 | Stronger ship statistics | Accepted | Exact one-sided sign-flip test over 2^15 patterns, p < 0.05, alongside the bootstrap CI; leave-one-bearing-out signs in G(c). One-sided because every gate is a directional superiority claim. |
| 14 | Non-trivial R0 gate; comparator D | Accepted | D is the primary comparator for N; P2 forbids more early pages than R0 (2: 2_3@151, 2_5@144); R1 explicitly cannot express critical and is context only. |
| 15 | D's defaults are not untuned | Accepted | Stated in 1.4.4; D' (1.2/k5) reported everywhere; D claims conditional if D' disagrees. |
| 16 | Latency arithmetic | Accepted | Concurrent counters pre-registered (2.2) and implemented in `causal.replay`; eligibility per k (22/24); 3_5 `gt_early_row` 23 < 24 corrected. |
| 17 | Conformal honesty | Accepted | Minimum 5 bearings per Mondrian bin; bearing-level coverage events with exact Clopper-Pearson intervals; jackknife dependence and 1.3rd-worst-bearing caveat stated (3.5). |
| 18 | Compute budget | Accepted | 2,250 fits (+160); vectorised hard-latch replay; 3 h cap with a pilot and a pre-authorised, outcome-blind shrink order. |
| 19 | Short-life and contaminated bearings | Accepted | Short-life bearings get their own rows; flagged vs unflagged stratification of N vs D; G(c) must also hold on the 8 unflagged bearings. |
| 20 | Freeze evaluator first | Accepted (modified) | `causal.py` and `run_dummies.py` were written, run and hashed; the dummy, R0 and R1 scores are recorded in 4.5. Modification: D's score was **not** computed before the freeze. D needs the stage-2/conformal code, which belongs to the not-yet-written `run_nested.py`, and computing the primary comparator while the weights were still editable would have exposed it to the weight choice. D will be computed first by `run_nested.py`, before any other candidate. |

## Deviations

(none yet)
