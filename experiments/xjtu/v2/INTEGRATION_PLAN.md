# Integration plan: XJTU-SY two-stage health-state system (v2)

Source of truth for every number: `experiments/xjtu/v2/results/final.json` (script
`experiments/xjtu/v2/final.py`; per-snapshot timeline `results/final_timeline.csv`; per-bearing
metrics `results/final_per_bearing.csv`). Protocol: `PROTOCOL.frozen.md` (rev 2). Deviations:
`results/final.deviations.txt` (FD1-FD7) and `results/post-onset-rul.deviations.txt` (DV1-DV14).

## 0. Decision and what this plan authorises

The protocol's primary rule (section 4.4) was applied to the full two-stage system: stage 1 selected
from 24 onset rules, then 672 downstream combinations, by fully nested leave-one-bearing-out (15
outer folds, 14-fold inner LOBO, bearing-grouped conformal, paired 1-SE rule, refit, D fallback).
**Result: nothing ships.**

| condition | N (nested system) | D (fixed simplest pipeline) | D' |
|---|---|---|---|
| P1: beats production R0 (bootstrap CI < 0, sign-flip p < 0.05, robust signs) | fail: -0.113 [-1.61, +1.41], p 0.45 | fail: -0.209 [-2.36, +1.62], p 0.42 | fail |
| P2: early pages <= R0's 2 | fail: 7 | fail: 5 | fail: 5 |
| P3: missed criticals <= R0's 8 | pass: 6 | pass: 6 | pass: 6 |
| P4: E1 exchange + beats all 5 dummies at 3 rates | fail (E1; loses to `onset_eq_critical`) | fail | fail |
| P5 / P6 | pass / pass | pass / pass | pass / pass |
| G(N better than D) | fail: +0.096 [-1.90, +1.90], p 0.54 | - | - |

Seed robustness: two further complete nested runs with other learner seeds give the same
decision and the same failed gates (section 6).

Consequences:

- **The live model stays R0** (`models/xjtu_rul_model.joblib`, current `src/training/xjtu_rul.py`).
  No part of N or D may be switched on for operators.
- **Phase A** (section 1) is model-independent plumbing that *every* future pipeline needs and
  that fixes two defects in today's behaviour (state demotion inside an episode, and no
  maintenance-driven reset). It is recommended now, keeping R0 as the model, on exploratory
  evidence (FD7): applying the protocol ratchet to R0 leaves its event profile unchanged
  (MC 8, EP 2, same bearings) and costs +0.053 in C [+0.004, +0.124] (6.740 -> 6.794), from
  extra capped hours in faulty/critical, while removing all within-episode demotions (R0 demotes
  on 10 of 15 bearings, which today resolves and re-opens alerts and re-pages). This is a product
  decision, not a protocol ship; it is labelled as such in `final.json["exploratory_R0_semantics"]`.
- **Phase B** (sections 2-5) is the exact change-set for the two-stage system. It is written so it
  can be implemented behind an artifact switch, but it **must not be activated** until a pipeline
  passes P(.) on a new pre-registered evaluation with more run-to-failure data (section 7).

---

## 1. Phase A: ratchet, reset and alert-resolution wiring (model stays R0)

### 1.1 `src/prediction/rul_realtime.py`

1. New per-machine state, next to `_warning_history`:
   ```python
   self._max_state: dict[str, int] = defaultdict(int)      # 0 healthy .. 3 critical (ratchet level)
   self.ratchet = bool(artifact.get("health_ratchet", True)) if ratchet is None else ratchet
   ```
   Constructor gains `ratchet: bool | None = None` (tests can turn it off). Old artifacts have no
   `health_ratchet` key and get the ratchet: the ratchet is a state-semantics fix, not a model
   property.
2. `STATE_RANK = {"healthy": 0, "degrading": 1, "faulty": 2, "critical": 3}` and its inverse.
3. In `_predict_from_base`, after the instantaneous state is computed (today:
   `self._health_state(predicted) if within_horizon else "healthy"`), inside the machine lock:
   ```python
   inst = STATE_RANK[instantaneous_state]
   mx = self._max_state[machine_id]
   if inst >= 2:
       mx = max(mx, inst)
       self._max_state[machine_id] = mx
   displayed = max(inst, mx) if (self.ratchet and mx >= 2) else inst
   condition_receded = self.ratchet and displayed > inst
   ```
   Same arithmetic as `causal.replay` hard-latch branch (faulty/critical never demoted; degrading
   may still clear because R0 has no onset latch). Result dict gains
   `"health_state": STATE_NAMES[displayed]`, `"instantaneous_health_state": instantaneous_state`,
   `"max_state_reached": STATE_NAMES[mx]`, `"condition_receded": condition_receded`, and the
   warning `"condition_receded: displayed state is held at the highest level reached; cleared only
   by a maintenance reset"` when `condition_receded`.
4. `reset_machine(machine_id, *, rebaseline_only=False)`: today clears all history. New semantics:
   - `rebaseline_only=False` (replacement / explicit reset): clear history, baseline, cycles,
     probability and warning deques **and** `_max_state`.
   - `rebaseline_only=True` (sensor remount): clear history, baseline, cycles, deques, but **keep**
     `_max_state` (protocol 2.4: re-baselining does not certify the bearing healthy).
5. Restart safety: `_max_state` is in memory like everything else today. Persist it (section 1.4)
   and re-hydrate it in a new `restore_machine_state(machine_id, max_state)` called by the
   predictor provider at startup; otherwise a restart silently un-ratchets a critical machine.

### 1.2 `src/maintenance/records.py`, `src/api/schemas.py`, `src/api/routes/maintenance.py`

- `MaintenanceCreate.type`: `Literal["preventive", "corrective", "replacement", "sensor_remount"]`;
  add `resets_baseline: bool = False`.
- `log_maintenance(...)` gains `type in {"replacement"}` or `resets_baseline=True` -> returns
  `reset="full"`; `type == "sensor_remount"` -> `reset="rebaseline_only"`; otherwise `None`.
  Persist `type` and `resets_baseline` (migration below).
- Route `POST /maintenance`: after the record is committed, if `reset` is set call
  `get_predictor().reset_machine(machine_id, rebaseline_only=(reset == "rebaseline_only"))` and,
  for a full reset only, `alerts.live.resolve_for_maintenance(conn, machine_id, performed_at,
  maintenance_id)`. Acknowledging an alert never resets anything (unchanged).
- `DELETE /predictions/rul/{machine_id}/state` (`src/api/routes/predictions.py:89`): keep, add query
  `rebaseline_only: bool = False`, and for a full reset also resolve the open alert as above. It
  stays an admin action; log who did it.

### 1.3 `src/alerts/live.py`

- Add `resolve_for_maintenance(conn, machine_id, timestamp, maintenance_id)` under
  `_TRANSITION_LOCK`: resolve the open alert (if any) with `status='resolved'`, `resolved_at`,
  `resolution='maintenance_reset'`, `maintenance_id`; return `("alert_resolved", alert)` for
  `pipeline.fan_out` (no page on resolution, as today).
- `resolve_for_maintenance` follows the existing `_resolve_locked` contract (caller holds
  `_TRANSITION_LOCK`, single conditional `UPDATE ... WHERE status = 'open'`), with `closed_by` =
  the user who logged the maintenance.
- With the ratchet, a faulty/critical machine never reports healthy again until reset, so the
  existing "healthy reading resolves the alert" branch only fires after a reset or for
  degrading-only episodes. That is the intended one-alert-per-episode behaviour (protocol 2.4).
- **Interaction with human close (`_resolve_locked`, `src/feedback/service.py`, added concurrently
  on this branch).** Today a human close is followed, on the next abnormal reading, by a fresh
  alert. Under the ratchet the machine keeps reporting faulty/critical after the close, so every
  close would immediately re-open and re-page. Fix in `_apply_reading`: carry an `episode_id`
  (predictor reset counter, from `machine_health_state`) on each reading and on each alert
  (`alerts.episode_id`, nullable); open a new alert only if there is no open alert **and** no
  alert of the same `episode_id` that was human-closed, or if the new reading's severity is higher
  than the closed alert's (escalation still pages). Readings without an `episode_id` (demo route,
  old callers) keep today's behaviour.

### 1.4 Storage (`src/storage/db.py`, `src/storage/migrations.py`)

- New table `machine_health_state(machine_id PK, max_state_reached TEXT NOT NULL,
  onset_latched INTEGER NOT NULL DEFAULT 0, fpt_cycle INTEGER, state_json TEXT, updated_at TEXT)`;
  `state_json` holds the Phase B fields (section 3.2) so Phase B needs no second migration.
- `maintenance_records`: add `type` values above (CHECK constraint if present), `resets_baseline
  INTEGER NOT NULL DEFAULT 0`.
- `alerts`: add nullable `resolution TEXT`, `maintenance_id INTEGER REFERENCES maintenance_records(id)`,
  `episode_id INTEGER`. `machine_health_state` gains `episode_id INTEGER NOT NULL DEFAULT 0`,
  incremented by every full reset.
- `predictions`: add nullable `instantaneous_health_state TEXT`, `reason_code TEXT`,
  `condition_receded INTEGER NOT NULL DEFAULT 0`. `src/prediction/rul_store.persist_prediction`
  maps them with `.get()` so old result dicts still persist.
- Write-through: `rul_store.persist_prediction` upserts `machine_health_state` in the same
  transaction as the prediction row.

### 1.5 Phase A tests

- `tests/test_rul_realtime.py`: (a) a stub artifact whose regressor returns 20 then 100 min with
  the warning on: state goes critical then stays critical with `condition_receded=True`;
  (b) `ratchet=False` reproduces today's demotion; (c) `reset_machine()` clears the ratchet,
  `reset_machine(rebaseline_only=True)` keeps it; (d) old artifact without `health_ratchet` loads
  and ratchets.
- `tests/test_maintenance.py`: replacement record resets the predictor and resolves the open alert
  with `resolution='maintenance_reset'`; preventive record does neither; sensor_remount re-baselines
  but keeps `max_state_reached`.
- `tests/test_alerts_live_concurrency.py`: `resolve_for_maintenance` racing `apply_reading` keeps
  one open alert at most.
- `tests/alerts/`: human-closed alert + further ratcheted critical readings of the same
  `episode_id` open nothing and page nothing; a higher severity in the same episode, or any
  abnormal reading after a reset (new `episode_id`), opens a new alert.
- `tests/storage/test_migrations.py`: new columns/table exist after migrating an old database;
  old rows read back with defaults.
- `tests/api/test_rul_persistence.py`: restart (new predictor instance) re-hydrates `max_state`.

---

## 2. Phase B system definition (the configuration that would ship if gates passed)

Modal configuration from the nested procedure run on all 15 bearings (`final.json["system"]`):

| part | setting |
|---|---|
| warm-up | snapshots 0-19 = commissioning; state healthy, `rul_estimate_kind="warming_up"` |
| stage 1 (onset latch O) | `r5 >= 1.3` for k = 3 consecutive snapshots counted from snapshot 20; `self + min60 fallback` reference; hard latch (no clear) |
| stage 2 | ExtraTreesRegressor(200 trees, min_samples_leaf 10, max_features 0.3) on `log1p(min(RUL, 600))`, trained on post-FPT rows, bearing-equal sample weights, 9 features (2.2) |
| intervals | global bearing-grouped conformal on log1p residuals; quantiles at 0.05/0.10/0.20/0.80/0.90/0.95 |
| faulty | O = 1 and 3 consecutive snapshots of (`q50 <= 60` or `r5 >= 3.0` or `p5 >= 4.0`) |
| critical | O = 1 and 3 consecutive snapshots of (`q50 <= 30` or (`p5 >= 4.0` and `q50 <= 60`)) |
| ratchet | faulty/critical never demoted; reset only by maintenance (section 1) |
| healthy output | `predicted_rul_minutes = null`, `rul_estimate_kind="unknown_no_onset"`, probability `p0` (fleet rate, ~0.40 in training) |

Per-fold choices differed (8/15 folds chose exactly this; others chose ET leaf 20, HGB-q, a q10 rule,
or TSO; fold 1_1 fell back to D), and with learner seed base 2042 the modal choice is ET leaf 20
instead of leaf 10, so the configuration is not stable on 15 bearings.

### 2.1 Indicators (exact; identical to `causal.indicators`)

- `med20_c`, `MAD20_c` over snapshots 0-19 for c in {h_rms, v_rms, h_peak, v_peak}.
- Fallback reference (rms only): at snapshot 59 compute `m60_c = min over rows 9..59 of the trailing
  10-row median of rms_c`; from snapshot 60 on, `ref_c = min(med20_c, m60_c)` if
  `m60_c < 0.9 * med20_c`, else `med20_c`. Before 60: `ref_c = med20_c`.
- `r = max_c rms_c / ref_c`; `z = max_c (rms_c - ref_c) / max(1.4826 MAD20_c, 0.02 ref_c)`;
  `p = max_c peak_c / med20(peak_c)`; `r5, z5, p5` = trailing 5-row medians.

### 2.2 Stage-2 features (exact order; `build_post-onset-rul.seg_X`)

`[tso, log r5, log r5 - log r5[fpt], log max(r5[fpt..t]), OLS slope of log r5 over last 10 rows,
OLS slope over last 30 rows, kurt_z5, log p5, log env_ratio]` where `tso = t - fpt`; slopes use
fewer rows early and 0 with < 2 rows; `kurt_z5` = trailing-5 median of `max_c robust_z(kurtosis_c)`
with commissioning median/MAD and floor `max(1.4826 MAD, 0.02 |med|)`; `env_ratio = max_c
envelope_rms_c / med20(envelope_rms_c)` (unsmoothed). While O = 0, features are evaluated at a
provisional onset `fpt = t` (tso 0, delta 0, peak = current r5) for the conditions only.

---

## 3. Phase B file-by-file changes

### 3.1 New module `src/prediction/two_stage.py` (shared by training and live; pure functions)

Move, do not import from `experiments/`: `indicator_step(state, snapshot) -> (r5, z5, p5)`,
`stage2_features(state, snapshot)`, `conformal_quantiles(pred, residual_quantiles)`,
`prob_within(120, levels, values)` (copy of `harness2.prob_within`), and
`TwoStageMachineState` (dataclass, section 3.2) with `to_json()/from_json()`. A unit test replays
every XJTU bearing snapshot by snapshot and must reproduce `final_timeline.csv` states for the
modal model exactly (golden test, section 5).

### 3.2 Minimal new per-machine state (protocol 2.5; all scalars except two short windows)

| field | type | note |
|---|---|---|
| `med20` (h_rms, v_rms, h_peak, v_peak), `mad20` (h_rms, v_rms) | 6 floats | frozen at snapshot 19 |
| `kurt_med20`, `kurt_mad20` (h, v), `env_med20` (h, v) | 6 floats | stage-2 features |
| `m60_h`, `m60_v` | 2 floats | fallback reference, set at snapshot 59 |
| `onset_latched`, `fpt_cycle` | bool, int | latch O |
| `log_r5_at_fpt`, `peak_r5_since_fpt` | 2 floats | |
| `trigger_run`, `faulty_run`, `critical_run` | 3 ints | concurrent counters from snapshot 20 |
| `max_state_reached` | int | ratchet (shared with Phase A) |
| last 30 `log r5`, last 5 raw `r`, `z`, `p`, `kurt_z` | windows | already derivable from the 60-row history the predictor keeps; stored only so a restart does not need history |

`calm_run` is not needed (hard latch selected). The existing commissioning list (20 snapshots) and
60-row history already contain every input, so no new raw history is required.

### 3.3 `src/training/xjtu_rul.py`

- Keep `train_and_export` (R0) untouched. Add `train_two_stage(table, artifact_path, report_path,
  *, seed=42)`:
  1. per bearing: indicators with the fallback reference; FPT = first snapshot where the r5 >= 1.3
     run reaches 3 (from snapshot 20);
  2. ExtraTrees fit on post-FPT rows of all bearings (`sample_weight = 1 / rows_of_bearing`);
  3. conformal residuals from 15-fold LOBO OOF predictions (`e = log1p(y) - log1p(pred)`),
     bearing-equal weighted quantiles at 0.05/0.1/0.2/0.8/0.9/0.95;
  4. `p0` = bearing-equal mean over bearings of P(RUL <= 120 | O = 0, snapshot >= 20) under the
     final state machine;
  5. the evaluation report embeds `final.json` metrics (nested estimate, not the in-sample fit).
- Artifact (schema 3):
  ```python
  {"artifact_schema_version": 3, "pipeline": "two_stage_v2", "model_version": ...,
   "stage1": {"family": "ratio", "G": 1.3, "k": 3, "ref": "fallback", "commission": 20,
              "median_window": 5, "fallback_rows": 60, "fallback_window": 10, "fallback_factor": 0.9},
   "stage2": {"family": "ET", "model": et, "features": [...9 names...], "target": "log1p_min600"},
   "conformal": {"binning": "global", "levels": [...], "residual_quantiles": [...]},
   "states": {"m": 3, "model_crit": "q50<=30", "S1": 3.0, "S2": 4.0, "hysteresis": "latch",
              "faulty_rul": 60.0, "critical_rul": 30.0},
   "p0": float, "tso_L": float, "prognostic_horizon_minutes": 120.0,
   "sample_rate_hz": 25600.0, "feature_bounds_99pct": {...}}
  ```
  No `classifier`/`regressor` keys, so an old predictor rejects it loudly ("invalid RUL artifact;
  missing ...") instead of mis-predicting.
- CLI: `--pipeline {r0,two_stage_v2}` (default `r0`); `register_trained_model` reads
  `artifact["stage2"]["model"]` for the algorithm name when `pipeline == "two_stage_v2"`.

### 3.4 `src/prediction/rul_realtime.py`

- `__init__`: `self.pipeline = artifact.get("pipeline", "r0")`. `r0` -> today's code path (plus
  Phase A). `two_stage_v2` -> validate the schema-3 keys and use `TwoStageMachineState` per machine.
  Unknown pipeline -> `ValueError`.
- `_predict_from_base` for `two_stage_v2`, per snapshot under the machine lock:
  1. update commissioning stats (snapshots 0-19) / `m60` (snapshot 59); compute r, z, p and r5/z5/p5;
  2. `trigger_run` (from snapshot 20) on `r5 >= G`; latch O when `trigger_run >= k`, set
     `fpt_cycle`, `log_r5_at_fpt`, `peak_r5_since_fpt`;
  3. stage-2 prediction at `fpt_cycle` (or provisional `t` while O = 0): `q50`, conformal bounds;
  4. `faulty_run` on (`q50 <= 60` or `r5 >= S1` or `p5 >= S2`); `critical_run` on (`q50 <= 30` or
     (`p5 >= S2` and `q50 <= 60`)), both counted from snapshot 20 independently of O;
  5. level = 0 if not O else 3 if `critical_run >= 3` else 2 if `faulty_run >= 3` else 1; ratchet;
     `reason_code` = "onset" | "rul" | "severity" at entry;
  6. outputs: O = 0 -> `predicted_rul_minutes=None`, `predicted_rul_hours=None`,
     `rul_estimate_kind="unknown_no_onset"` (or `"warming_up"` before 20),
     `prediction_interval_90_minutes=[None, None]`, `failure_within_horizon_probability=p0`,
     warning `no_onset`; O = 1 -> `q50`, `[q10, q90]`, `rul_estimate_kind="post_onset"`, probability
     from `prob_within`. `raw_failure_within_horizon_probability` = same value;
     `warning_persistence_snapshots` = 3.
- `reset_machine` resets `TwoStageMachineState` per Phase A semantics (`rebaseline_only` keeps
  `max_state_reached` and nothing else).
- Backward compatibility: R0 artifacts behave exactly as after Phase A; existing fixtures in
  `tests/test_rul_realtime.py`, `tests/api/test_rul_persistence.py`,
  `tests/prediction/test_rul_store.py`, `tests/ingestion/test_replay_service.py` keep passing. The
  speed/load arguments stay in the signature (validated, unused by `two_stage_v2`).

### 3.5 API schema (`src/api/schemas.py`) and frontend types

- `RULPredictionResponse`: `predicted_rul_minutes: Optional[float]`, `predicted_rul_hours:
  Optional[float]`; new optional fields `reason_code: Optional[Literal["onset","rul","severity"]]`,
  `onset_detected: Optional[bool]`, `onset_snapshot: Optional[int]`, `max_state_reached:
  Optional[str]`, `instantaneous_health_state: Optional[str]`, `condition_receded: bool = False`,
  `pipeline: str = "r0"`. `rul_estimate_kind` values documented:
  `point_estimate | lower_bound` (R0), `warming_up | unknown_no_onset | post_onset` (v2).
- `MachineSummary` (already `Optional` RUL): add `reason_code`, `condition_receded`.
- `frontend/src/api/types.ts`: `RULPrediction.predicted_rul_minutes: number | null`,
  `predicted_rul_hours: number | null`, the new optional fields.

### 3.6 KPI and alerts

- `src/kpi/calculations.py`: `predicted_rul_minutes` may be null with a non-healthy state never
  (O = 1 always publishes q50) but is null for every healthy machine; fleet summaries that average
  or sort RUL must skip nulls (`RiskHorizon.tsx` already holds risk flat for null RUL). Add a count of
  `unknown_no_onset` machines to the fleet summary so "no RUL" is not read as "fine for ever".
- `src/alerts/generation.py::format_alert_message`: append the reason (`"onset vs own baseline"`,
  `"predicted RUL <= 30 min"`, `"peak severity with RUL <= 60 min"`). Severity mapping is unchanged
  (degrading low, faulty medium, critical high + page).

### 3.7 Frontend meaning changes (`MachineDetail.tsx`, `healthStyles.ts` legend, `MachinesAtRisk.tsx`)

State meanings change, so labels must change with them:

| state | R0 meaning today | v2 meaning |
|---|---|---|
| healthy | no late-life signature (RUL shown as "> 120 min") | no departure from this machine's own baseline; **no RUL promised** ("RUL unknown - no onset") |
| degrading | predicted RUL <= 120 | damage has started (onset latched); create a work order |
| faulty | predicted RUL <= 60 | advanced damage; replace at next planned stop |
| critical | predicted RUL <= 30 | failure possible within 30 min; stop or replace now |

Show `condition_receded` as a note ("held at highest level until maintenance"), and the
replacement / sensor-remount maintenance types in `MaintenanceForm.tsx`.

---

## 4. Backward compatibility summary

- Old artifact + new code: R0 path, Phase A ratchet on by default (can be disabled per artifact
  with `health_ratchet: False`).
- New artifact + old code: rejected at load (missing `classifier`/`regressor`).
- DB: all new columns nullable or defaulted; old rows read unchanged.
- API: only widening (nullable fields, optional new fields); old clients that assume a numeric RUL
  must handle null before a v2 artifact is deployed.

## 5. Tests to add for Phase B

- Golden replay: `two_stage.py` + modal artifact refit on all 15 bearings reproduces the
  `final_timeline.csv` state/RUL columns for the fold models when fed the fold artifacts (pickle the
  15 fold artifacts as test fixtures, or regenerate them with seeds 42 + fold).
- Causality: features and state at snapshot t are identical when computed from (first 20 + last 60
  snapshots + `TwoStageMachineState`) and from the full prefix.
- Counter arithmetic: an abrupt synthetic trajectory goes healthy -> critical at snapshot
  `20 + max(k, m) - 1 = 22`; onset at 22 for k = 3.
- Restart: serialise state mid-episode, new predictor instance, identical next outputs.
- Null RUL end-to-end: healthy prediction persists `predicted_rul_minutes NULL`, KPI and
  `/machines` serialise it, alert path unaffected.
- Schema: R0 artifact still loads; schema-3 artifact with the old loader raises.

## 6. Seed robustness (two further complete nested runs; FD2)

| learner seed base | C (w_EP 5) [95% CI] | w3 / w10 | MC | EP | N - R0 [CI], p | N - D [CI], p | decision |
|---|---|---|---|---|---|---|---|
| 42 (primary) | 6.628 [5.40, 7.84] | 5.69 / 8.96 | 6 | 7 | -0.113 [-1.61, +1.41], 0.45 | +0.096 [-1.90, +1.90], 0.54 | nothing ships |
| 1042 | 6.568 [5.39, 7.74] | 5.50 / 9.23 | 5 | 8 | -0.173 [-1.72, +1.35], 0.39 | +0.036 [-1.92, +1.80], 0.52 | nothing ships |
| 2042 | 6.614 [5.43, 7.79] | 5.55 / 9.28 | 5 | 8 | -0.127 [-1.68, +1.41], 0.41 | +0.082 [-1.87, +1.85], 0.53 | nothing ships |

- The decision and the failed gates (P1, P2, P4; G(N better than D)) are identical under all three
  seeds. Seed-to-seed differences in C are small (-0.060 [-0.25, +0.10] and -0.014 [-0.20, +0.13]
  vs the primary) and come from 5 bearings (3_2, 1_5, 2_4, 1_2, 2_5).
- Under the other two seeds 3_2 becomes an early page (at RUL ~2,300) instead of a miss, so the
  counts move from MC 6 / EP 7 to MC 5 / EP 8. That trade is the same failure mode as the primary.
- **Selection is not stable**: only 8 of 15 folds choose the same configuration under all three
  seeds, and the all-15 modal downstream changes with the seed (ET leaf 10 for 42 and 1042, ET leaf
  20 for 2042). Stage 1 (ratio 1.3 / k3 / fallback, 1.2 on the 3_2 and 3_5 folds) and the latch,
  global binning, S1 = 3.0 are stable; fold 1_1 falls back to D under every seed.

## 7. What is irreducible and what the real fix needs

- **Irreducible from vibration on these bearings**: onset is structurally undetectable at RUL >= 10
  on 1_4, 2_4, 1_5; a missed critical is unavoidable on 1_4 and 2_4 (latency floor), and partly on
  3_3. The latency-floor LC alone is 2.57 bearing-units (10 x 2.57 / 15 = 1.71 of every pipeline's C).
- **The binding limit is stage 2, not onset detection**: with the true onset handed to the
  learners (exploratory oracle in `post-onset-rul.json`), the nested oracle pipeline still has EP 9
  and fails P1/P2/P4, and in the descriptive outer grid (42 latch / S1 2.0 / S2 off configurations)
  the fewest early pages is 5 with oracle onset and 4 with the causal onset, against R0's 2; the q10
  rule collapses to "critical at onset". Separating
  "RUL 60-120" from "RUL < 30" after onset is not supported by the current features on 15 bearings.
- **Real fix is data, not tuning**: more run-to-failure bearings (the 5-bearing Mondrian bins and the
  tie-set sizes of 20-200 show the selection is under-determined), target-machine histories, and
  extra channels (temperature, acoustic, motor current) that carry imminent-failure information.
  Re-run this frozen protocol unchanged on the enlarged set; ship Phase B only if P(.) passes.
