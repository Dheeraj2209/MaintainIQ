# v3 re-run of the frozen v2 protocol on XJTU-SY + FEMTO: deviations

Written 2026-10-07T07:12+05:30, **before** any confirmatory pass (pooled nested LOBO, modal run, transfer
selection) was started and before any v3 N, D, D' or pooled-R0 result existed.

What had been seen when this was written: the pooled table validation (`results/pooled_validation.json`),
the GT-derived latency floors below (computed from the GT only, no model involved), and the legacy
120-minute summary metrics of the two transfer R0 fits (`results/r0_runs_summary.json`; XJTU-trained R0
never warns on any FEMTO bearing). None of these choices depends on them. The stage-1 selection on the
15 XJTU bearings was run once as a plumbing check. It reproduced the v2 modal stage 1
(`ratio1.3/k3/fallback`, 24 eligible, tie set 7) and no downstream or outer result was computed.

Protocol: `experiments/xjtu/v2/PROTOCOL.frozen.md` (rev 2). It is re-used **unchanged**, including
`causal.py` (cost, replay, indicators, statistics), the onset-GT rules, the 24 x 672 candidate space,
factorized nested LOBO (4.3), the paired 1-SE rule, E1-E3, G/P1-P6 and the decision order (4.4).
The frozen hashes are asserted at import (`pooled_env.py`) and again by every `rerun.py` part.
`harness2.py` and `build_post-onset-rul.py` are imported, not copied. Only their data sources are
redirected in memory (`pooled_env.py`).

Code: `experiments/xjtu/v3/pooled_env.py`, `rerun.py`, `r0_runs.py`.
Results: `results/rerun.json`, `results/rerun_per_bearing.csv`.

## Deviations forced by the new data

**V1. Data.** Each of the 32 trajectories is one bearing: 15 XJTU and 17 FEMTO (`FEMTO_` prefix).
The pooled table has 13,356 one-minute rows (`cache/combined_features.csv`, concat6 mode, see
`DOMAIN_ALIGNMENT.md`). XJTU rows equal `outputs/xjtu_features.csv` exactly.
- Outer LOBO is over the sorted ids, as in the protocol. XJTU sorts first, so XJTU outer folds keep indices
  0-14 and therefore the v2 seeds (42 + fold). FEMTO folds are 15-31.
- FEMTO conditions are 4, 5 and 6.

**V2. Onset GT for FEMTO.** The GT comes from the v3 copy of the frozen `onset_gt` rules
(`pooled_validate.pooled_gt`). Its 15 XJTU rows equal the frozen `onset_gt.csv` exactly.
- FEMTO has no two-phase exponential fit (`design_learned-onset/onset_delta.csv` covers XJTU only). Its
  uncertainty interval is therefore the min/max over **3** constructions (energy, ENV, HF), not 4.
- The two-phase GT variant (gate G(c)) has no FEMTO onset. The frozen `gt_variant_rows` rule then falls
  back to the primary interval for those bearings. FEMTO_2_7 also has no ENV, HF or V-2x onset and falls
  back the same way.
- The `baseline_contaminated` fleet reference is the leave-one-out median of the same-condition FEMTO
  bearings (the frozen rule applied to conditions 4-6).
- Resulting flags:
  - unflagged (not contaminated): 8 XJTU + FEMTO_2_1, 2_2, 2_7 = 11 bearings;
  - abrupt: 12 of 17 FEMTO bearings.
- Wide GT intervals: FEMTO_1_1 (RUL 248-64), 1_7 (212-22), 2_1 (126-13), 2_2 (101-8). FD, FDh and LD for
  these bearings are therefore weakly identified.

**V3. MO scoring.** The rule is unchanged: score MO only when gt_late_rul >= 16. Only 5 FEMTO bearings
qualify: 1_1, 1_3, 1_4, 1_7, 3_3. The other 12 are "structurally undetectable at RUL >= 10" under the
frozen rule.

**V4. Latency floors (GT-derived, before any model).** These use `causal.latency_floor` at k = 3.
- Critical at RUL >= 10 is **unachievable** on 12 of 17 FEMTO bearings: 1_2, 1_5, 1_6, 2_1, 2_2, 2_3,
  2_4, 2_5, 2_6, 2_7, 3_1 and 3_2.
- 1_7 (best 18) and 3_3 (best 14) carry partial LC floors.
- Only 1_1 (60), 1_3 (104) and 1_4 (52) have no structural floor.
- Consequence: on most FEMTO bearings MC and LC are fixed by the onset GT and cannot separate the
  candidates. Their C differences come mainly from EP, ECh, EFh and the pre-onset terms.

**V5. Cadence and short lives.** One row is still one minute (6 FEMTO snapshots per row), so every
minute-based protocol constant keeps its meaning without change: 20-row commissioning, k, m = 3, the
60-row fallback window, 30 calm rows, 60/120 horizons and the hour caps.
- FEMTO_2_7 lives 38 min (the warm-up is 53% of its life). With fewer than 60 rows, the frozen
  `indicators` never applies the fallback reference to it.
- FEMTO_3_3 (72 min) and 3_1 (85 min) are also short.
- Rules are unchanged; these bearings are reported in their own strata.

**V6. R0.** Protocol 3.2 fixes R0 as the OOF file `experiments/xjtu/results/baseline_oof.csv`. In v3, R0 is
the **production architecture retrained out-of-fold on the same folds**:
- Retraining uses `experiments/xjtu/harness.py` unchanged (same factories, features, seeds, smoothing,
  threshold and persistence): `r0_runs.py` -> `cache/r0_pooled_lobo_oof.csv`, a 32-fold LOBO with harness
  fold numbering.
- The R0 state mapping is the frozen `causal.states_r`.
- R0 keeps `speed_rpm`/`load_kn` among its classifier features, as production does. These reveal the
  dataset (the FEMTO and XJTU ranges do not overlap). This is not corrected, because R0 is "production
  as is". The protocol stage-2 features (N, D, D') exclude condition, speed and load by design.
- The protocol's leakage caveat carries over: an inner bearing's R0 OOF comes from a model trained with
  the outer held-out bearing included.
- E1, P2 and P3 compare against **the pooled R0's own EP and MC counts on the same bearings**. The frozen
  numbers "2" and "8" are the XJTU values of the same rule.

**V7. Exact sign-flip infeasible at n = 32.** `causal.signflip_p` enumerates 2^n patterns. The patched
version (in memory) works as follows:
- n <= 20: the frozen exact enumeration is used unchanged.
- n > 20: a seeded Monte-Carlo sign-flip with 2^20 draws (seed 20261006), p = (hits + 1)/(draws + 1).
- Only G(b) and the reported p-values use it. Selection does not.

**V8. G(c) at n = 32.**
- Single-bearing deletions cover all 32 bearings.
- The {3_1, 3_2} deletion is kept (XJTU long-life bearings).
- The "8 unflagged bearings" item becomes **all unflagged bearings (11)** (V2).
- w_EP 3/10 and the four robustness GT variants are unchanged (two-phase falls back on FEMTO, V2).

**V9. Strata.** `short_life` is defined as life <= 60 min. This reproduces {2_4, 1_5} on XJTU and adds
FEMTO_2_7 (reporting only). The dataset (XJTU/FEMTO) is added as a reporting stratum.

**V10. Selection at 31 inner bearings.**
- The paired 1-SE rule uses SE = sd/sqrt(31). This is the protocol's formula with the actual number of
  inner bearings; the protocol's "14" is the XJTU instance.
- The 5-bearing conformal-bin rule and everything else are unchanged.

**V11. Compute (5.1).** The 3-h hard cap and the pilot-triggered shrink order are **not applied**.
- The task requires the same candidate space, so the full 672 grid is used.
- The pooled run needs about 32 x 31 inner LOBO sets, roughly 4.5x the v2 fits and replays.
- The outer folds run in 3 single-threaded processes (fold i -> shard i mod 3), so total threads <= 3.
- ExtraTrees results do not depend on n_jobs. HGB-q runs with 1 OpenMP thread in every process, so it is
  deterministic and identical across shards. It can differ from the v2 3-thread HGB fits at
  float-rounding level.
- Per-fold results are checkpointed to `cache/rerun/`.

**V12. Seeds.**
- Outer fold i uses 42 + i, as in v2.
- The all-32 modal run uses 42 + 32 = 74.
- Transfer selection uses 42 + n_source: XJTU 57, the same as the v2 all-15 modal run; FEMTO 59.

**V13. D and D'.** These are fixed, deterministic configurations (TSO). They are computed in the report
part, after N; no selection depends on them. The fold fallback (4.2) uses D's fixed configuration, as in
v2. This differs from v2's ordering (DV12), but the result cannot depend on the order.

**V14. Not re-run.** The v2 seed-robustness runs (FD2) and the exploratory variants (N_simplest,
N_oracle, D_oracle, FD7 R0 ratchet) are not re-run.

## Additions that are not part of the protocol (reported, never decision inputs)

**V15. (b) Cross-dataset transfer (report only, not a ship gate).** The procedure for source S and
target T is the protocol's all-bearings modal procedure on S:
- Stage 1 is chosen by inner C1 over S. The 672-grid downstream is chosen by inner C on |S|-fold LOBO
  over S. Both use the paired 1-SE rule and E1-E3.
- Refit on all of S (conformal from the |S| inner OOF), then apply to each bearing of T.
- The inner E1 reference R0 is the within-source LOBO R0: the v2 `baseline_oof.csv` for XJTU, and the
  17-fold FEMTO LOBO `cache/r0_femto_lobo_oof.csv` for FEMTO.
- The target-side R0 is the production recipe fit on all of S with the final-model seeds (classifier
  42/200/1000, regressor 42): `cache/r0_{xjtu_to_femto,femto_to_xjtu}.csv`.
- D and D' are refit on S and applied to T.
- The P1 RUL comparators are fit on S.
- The R0 interval band e90 is computed on target rows. It is descriptive only and does not affect
  states or cost.
- The gates and "decision" are computed for information. The transfer models are also compared, on the
  same target bearings, with the pooled-LOBO models (same bearings, in-domain data available).

**V16. Failure-anchored subtotal.** `FA_w = 10*LC + w*EP + 0.5*min(ECh,4) + 0.25*min(EFh,4)`, at
w = 3/5/10. These are the C terms that do not use the onset GT. It is reported because the GT-based terms
(FD, FDh, LD) are partly circular (protocol 1.4). Paired differences use the same bootstrap and sign-flip.
It is not a decision input.

**V17. Post-onset discrimination (descriptive).** Rows used: rows at or after the GT late edge with true
RUL < 30 (positive) or 60-120 (negative).
- Reported metrics:
  - row AUC and bearing/class-weighted AUC of the negative published predicted RUL;
  - median predicted RUL per class;
  - the within-bearing median gap;
  - the fraction of eligible rows with a published prediction (N publishes only when non-healthy).
- Reported per dataset, for the pooled models, the v2 XJTU-only N/R0 (from `v2/results/final_timeline.csv`)
  and the transfer models.

**V18. Inherited interpretation choices.** DV1-DV9 of `v2/results/post-onset-rul.deviations.txt` are
inherited unchanged:
- provisional pre-onset stage-2 conditions;
- warm-up gating for pipelines, R0 ungated;
- the hard-latch FPT for stage-2 training;
- p0 from inner states;
- published [q10, q90] at alpha 0.1.

**V19. Output names.** `results/rerun.json` and `results/rerun_per_bearing.csv` (the task's names). The
per-bearing CSV has one row per (run, model, bearing).

## Addendum (2026-10-07T07:40+05:30, after the XF transfer selection had started, before any pooled N fold ran)

**V20. Report smoke test.** The report code was smoke-tested once, in a temporary directory outside the
repo, before the pooled R0 existed:
- every pooled fold was forced to the D fallback;
- the import placeholder (a never-warning R0 on FEMTO) stood in for R0.
This exposed D's fixed pooled outcome, which is deterministic and depends on no choice. It exposed nothing
about N or the real pooled R0. No code path or protocol choice was changed afterwards.

**V21. Execution note (no effect on results).** A process-listing error made me think the three shard
processes had died after their first fold, so the shards were launched a second time.
- For about 20 minutes, 6 single-threaded processes ran. The duplicates were then stopped.
- Every fold is deterministic (seed 42 + fold, 1 thread) and is checkpointed by fold index, so a duplicated
  fold can only rewrite an identical file.
- Fold wall times in the shard logs for folds 3-5 are inflated by this.

## Addendum (2026-10-07T12:10+05:30, after all confirmatory passes had finished; execution note only)

**V22. Report pass.** `rerun.py --part report --n-jobs 3` was run once, after pooled folds 0-31, the
all-32 modal run and both transfer selections were checkpointed. It makes no selection: it refits the
fixed D/D' configurations (TSO, no stochastic fit), replays the frozen dummies, evaluates the R0 OOF
files and assembles gates. Nothing in V1-V21 was changed after any result was seen. Log:
`results/rerun_report.log`.
