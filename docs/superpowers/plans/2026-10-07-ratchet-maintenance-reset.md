# Health-state ratchet with maintenance reset: implementation plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans or
> superpowers:subagent-driven-development to carry out this plan task by task. Every
> task is TDD: write the failing test, watch it fail, implement, watch it pass, then commit.
> Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Rev 2 (2026-10-07)** addresses two adversarial reviews (state-machine lens: 13
> findings; operator/alerts lens: 12 findings). The disposition of every finding is in
> the "Review resolution" section at the end. The main changes from rev 1:
> - prepare and predict now run atomically, under a predictor generation counter;
> - stale predictions are no longer persisted;
> - a reset happens only on a deliberate repair, never on any corrective record;
> - a false-alarm close re-arms the alert without a reset;
> - out-of-distribution rows do not latch;
> - the migration seeds the pre-deploy human closes;
> - the commissioning and held states are visible in the UI (Task 12 is now required).

## Drift corrections (2026-10-07)

The plan was written against an uncommitted working tree. That tree is now committed
(`f72c4d3`, plus research-only commits `ba41962`, `bdf5a65`). Every reference was
re-checked against `bdf5a65`. What still holds, and what changed (the affected task text
below has been fixed in place):

**Still true (no change needed).**
- Migration 008 is the next migration: `MIGRATIONS` ends at `(7, _migration_007_push)`.
  `tests/storage/test_migrations.py` and `tests/api/test_get_db_schema.py` derive
  `LATEST` from `MIGRATIONS[-1][0]`, so neither needs a version bump.
- `maintenance_records.type` has `CHECK(type IN ('preventive','corrective'))`
  (`src/storage/db.py:168`, re-added by the guarded ALTER in migration 001). D2 keeps it.
- `work_orders.service.complete(conn, wo_id, actor, *, notes, performed_at,
  maintenance_type, now)` holds `_LOCK`, calls `log_maintenance` (which commits on its
  own), then `_transition`; its docstring has the "Not atomic" paragraph D1 replaces.
  `_transition` commits and its conflict path already calls `conn.rollback()`.
  `create_from_alert` takes only `live._TRANSITION_LOCK`, so the lock order of D1 holds.
- `alerts/live.py`: `apply_reading` `:96-110`, `_apply_reading` `:113-177`,
  `_ALERT_COLUMNS` `:47`, `_TRANSITION_LOCK` `:93`, `_resolve_locked` `:216` (the only
  human-close writer, used by `feedback.service.close_alert`).
- `feedback.service`: `close_alert`, `record_feedback`, `_upsert(conn, alert_id, actor,
  values, work_order_id, now)`, `FUTURE_SKEW`, docstring `:13-14` all as described.
- `rul_store.persist_prediction` `:28-65`, `rehydrate(predictor, conn, machine_id)`
  `:147-173` (still has no `epoch` and is still never called in production),
  `PREDICTION_SOURCE = "xjtu_rul"`, `live.DEMO_SOURCE = "demo"`.
- `rul_realtime.py`: deques `:63-72`, two locked sections in `_predict_from_base`, the
  OOD rule `outside_fraction > 0.10`. The deployed artifact has `baseline_window` 20,
  smoothing 3, persistence 3, and no `health_ratchet` key (so the D13 default is on).
- SQLite is 3.49.1, Python 3.12.

**Drifted (fixed in the task text).**
1. **Section 0 is historical.** Every file in its table is committed now; there is no
   other session. Read its "State today" column as "was uncommitted when written".
2. **Persist happens in the callers, not in `pipeline.handle_prediction`.** The route
   (`predictions.py:67`), replay (`replay_service.py:163`) and ingest (`ingest.py:435`)
   each call `persist_prediction` themselves and then call `handle_prediction` /
   `_on_prediction` with the `prediction_id`. `handle_prediction` returns a dict
   `{"event","alert","emails_sent"}`, not a tuple. So on a persist `None` **each caller**
   skips the alert fan-out (`handle_prediction` / `_on_prediction`); `log_inference`
   still runs (the inference happened). `handle_prediction` itself does not change for
   this (D6, Task 6, Task 7 fixed).
3. **Line numbers moved.** `ingest.py` `predictor.predict` is at `:410` (not `:409`);
   the DELETE route `reset_rul_state` is at `predictions.py:105-109` (not `:90-94`);
   `kpi._abnormal_event_count` is `:104-109` and `_machine_health` `:112-138` (not
   `:101-106` / `:109-135`).
4. **Ingest's `try` catches every exception** and records it as an ERROR message. A
   `StaleEpoch` must be caught *before* that generic `except` and logged at INFO as a
   skip, not reported as a prediction failure. The route's `try` catches only
   `ValueError`, so it needs its own `except StaleEpoch` (409) (Task 6 fixed).
5. **`get_current_user` / `require_role` live in `src.auth.deps`**, not `src.api.deps`
   (Task 8, Task 10 fixed).
6. **`records.py` has no `_get_record` helper** and `log_maintenance` takes no `now`; it
   builds the returned dict inline. `_log_maintenance_locked` returns that inline dict
   with `resets_health` added. `get_history` must also select `resets_health` so
   `MaintenanceRecord.resets_health` serializes (Task 8 fixed).
7. **`readings.features_json` holds raw snapshot features, not trained columns.** The
   trained classifier/regressor columns are mostly derived by `add_past_context`
   (`*_baseline_ratio`, `*_baseline_delta`, `*_mean_N`, `*_std_N`, `*_trend_N`) or are
   snapshot meta (`speed_rpm`, `load_kn`, `elapsed_minutes`). Checking a stored row
   against `classifier_feature_columns` would reject every row. The per-row rehydrate
   check is against the **non-derived, non-meta** trained columns plus
   `ROLLING_SOURCE_COLUMNS` (Task 5 fixed).
8. **`tests/prediction/test_rul_store.py:203`** (`test_rehydrate_skips_readings_without_feature_vector`)
   also calls `rehydrate` without `epoch` and without prediction rows; it changes with
   the parity test (Task 5 fixed).
9. **`experiments/` must not be edited** (execution rule for this run). Task 11 Step 2
   runs its scratch checks from a temp copy outside the repo and commits nothing under
   `experiments/` (Task 11 fixed).
10. **`reports.generators.fleet_summary`** (`:214-232`) also reads "latest state per
    machine"; it gets the same `effective_state` treatment as `machine_prognostic`, only
    for open-ended reports (no `period_end`) (Task 11b fixed).
11. **The close flow is a shared component.** Alert close lives in
    `frontend/src/components/AlertCloseForm.tsx` / `AlertCloseDialog.tsx`, used by
    `AlertsPage`, `MachineDetail`, `WorkOrderDrawer`, `MachineDetailPage` and the mobile
    pages (`MobileAlertsPage`, `MobileAlertDetailPage`, `MobileMachineDetailPage`). The
    Task 12 close-outcome messages go there, and held/commissioning badges also go on
    `MobileMachineDetailPage` (Task 12 fixed).
12. **`tests/maintenance/` does not exist**; sibling test dirs have `__init__.py`, so
    create `tests/maintenance/__init__.py` with the new test file.
13. **Test commands.** Backend: `python -m pytest -q -p no:cacheprovider <paths>` from the
    repo root (full suite about 4 min, 1043 green at `bdf5a65`). Frontend, from
    `frontend/` only: `npx vitest run <paths>`, `npx tsc -b`, `npm run build` (451 green).

**Goal.** Stop the production predictor (`src/prediction/rul_realtime.py`) from demoting
`health_state` (for example critical -> healthy -> critical). A demotion resolves the
open alert, and the next reading re-opens and re-pages it. Offline this happens on
10 of the 15 XJTU bearings (31 demotions).

The fix holds the worst state reached (a ratchet) until one of two deliberate events
happens to that machine:
- a **repair reset**: the component was replaced or fixed, so a new baseline is learned;
- a **false-alarm re-arm**: a human says the held alert was false, so the held level is
  dropped but the baseline is kept.

Both events are authoritative in the DB, so they survive restarts and reach every
predictor caller.

**Evidence.**
- `experiments/xjtu/v2/results/final.json`
- `experiments/xjtu/v2/INTEGRATION_PLAN.md` Phase A / FD7
- Scratch check `experiments/xjtu/v2/verify/ratchet_warmup_check.py`, on R0 OOF through `harness2.evaluate`:

| Variant | C_w5 | Missed criticals | Early pages | Demotions |
|---|---|---|---|---|
| R0 (today) | 6.740 | 8 | 2 | 31 (10 bearings) |
| R0 + ratchet | 6.794 | 8 | 2 | 0 |
| **R0 + ratchet + commissioning rows (<=20) not latched** (this plan) | 6.747 | 8 | 2 | 0 |

The MC and EP bearing lists are the same in all three rows. Without the gate, bearing
3_5 latches a 4-snapshot faulty blip at rows 12-15, about 12 rows before its genuine
faulty onset at row 27.

The evidence is XJTU only. That is why out-of-distribution rows do not latch (decision
D8), and why Task 11 re-checks the OOD rule on XJTU before the plan is called done.

---

## Architecture

### Per-machine health state (new table `machine_health_state`)

| Column | Meaning |
|---|---|
| `epoch` | Baseline life. Bumped only by a **repair reset**, which restarts commissioning and rehydrates nothing. |
| `episode` | Alert episode. Bumped by **every** reset **and** every false-alarm re-arm. Monotonic. |
| `max_state` | Held (ratcheted) level for the current episode. |
| `model_version` | Artifact that derived `max_state`. A change triggers a re-derive (D10). |
| `epoch_started_at`, `episode_started_at`, `reset_reason`, `updated_at` | Audit fields. |

### Stamped columns
- `predictions.health_epoch`, `predictions.health_episode` and
  `predictions.instant_health_state` are written by `persist_prediction`.
- `alerts.health_episode` is written when an alert is inserted.
- `maintenance_records.resets_health` records whether that record reset the machine.

### Writers that change the health state
All go through `src/prediction/health_epoch.py`, under `live._TRANSITION_LOCK`, inside
the caller's transaction.

| Event | Function | Effect |
|---|---|---|
| Repair reset (maintenance with resolved `reset_health`, work-order completion, manual DELETE route, replay restart) | `_reset_locked` | epoch+1, episode+1, `max_state = 'healthy'`, system-resolves the open real alert with `resolved_at = now` |
| False-alarm re-arm (feedback close or record with outcome `false_alarm` or cause `sensor_or_data_quality_issue`, on an alert of the current episode) | `_rearm_locked` | episode+1, `max_state = 'healthy'`, epoch unchanged |
| Ratchet raise (synced prediction) | `record_level` | raises `max_state` only if the row is still at the result's episode; a single UPSERT statement |

### Reader sync: `health_epoch.predict_synced`
Every predictor caller (route, replay, MQTT) calls
`health_epoch.predict_synced(predictor, conn, machine_id, call)`. It holds
`_SYNC_LOCKS[machine_id]` for the whole sync-and-predict:

1. **sync** (`_prepare_locked`): read `(epoch, episode, max_state, model_version)`.
   - If the epoch differs from the predictor's, reset and rehydrate the current epoch
     (bounded, D5), then restore.
   - If only the episode differs, restore the held level only.
   - If the model differs, re-derive (D10).
2. **predict**: run `call()`.

The predictor carries a per-machine **generation** counter. `restore_health` and
`_reset_state` bump it. `_predict_from_base` captures `gen`, `epoch`, `episode` and
`snapshots_seen` together in its first locked section. If `gen` has changed in a later
section it raises `StaleEpoch` and leaves the deques and `_max_state` alone.
Callers log `StaleEpoch` and skip persist and fan-out.

### Persist and alerts are guarded by episode
- `persist_prediction` uses a single conditional `INSERT ... SELECT ... WHERE` on the
  current episode. A stale result (one whose episode was overtaken by a reset or re-arm)
  is **not persisted**: the function returns `None`, and the caller skips the alert and
  the fan-out. In the same transaction it calls `record_level`, which is itself
  conditional on the episode.
- `apply_reading` drops a stale episode. With the ratchet on, it also applies episode
  suppression: no re-open after a human close unless severity rises.

**Tech stack.** Python 3, SQLite 3.49 (guarded `ALTER TABLE` migrations), FastAPI,
pytest, React/Vitest (Task 12).

---

## 0. Coordination with the other live session (historical)

> **2026-10-07 drift note:** all of this work is now committed (`f72c4d3`). There is no
> other session; the table below only maps files to tasks. See "Drift corrections".

Almost every file this plan touches is **uncommitted work from the other session**
(`git status` 2026-10-07). Land that work first (commit it, or at least stop editing
these files), then run this plan on top of it.

| File | State today | What this plan does to it | Task |
|---|---|---|---|
| `src/prediction/rul_realtime.py` | M | ratchet, gate, OOD no-latch, generation counter, epoch API, `_seek_cycle` | 3, 4 |
| `src/prediction/rul_store.py` | M | conditional persist, bounded rehydrate by epoch | 5 |
| `src/storage/migrations.py` | M | migration 008 | 1 |
| `src/alerts/live.py` | M | `health_episode` kwarg, stale drop, episode suppression, docstring | 7 |
| `src/prediction/pipeline.py` | ?? | pass `health_episode` only when the ratchet is on; handle persist `None` | 7 |
| `src/feedback/service.py` | M | false-alarm re-arm in `close_alert` / `record_feedback`; docstring | 7b |
| `src/maintenance/records.py` | M | `_log_maintenance_locked`, reset rules, backdate/future guard | 8 |
| `src/work_orders/service.py` | ?? | atomic `complete`, default reset rule, default feedback | 8 |
| `src/api/schemas.py` | M | `reset_health` fields; held/commissioning fields on responses | 8, 12 |
| `src/api/routes/maintenance.py` | committed | `get_current_user`; 403 on a reset by a non-supervisor | 8 |
| `src/api/routes/work_orders.py` | ?? | pass `reset_health` | 8 |
| `src/api/routes/predictions.py` | M | `predict_synced`; 409 on stale; DELETE bumps the DB only | 6, 10 |
| `src/ingestion/replay_service.py` | M | `predict_synced`; reset outside `_lock`; stop on external reset | 6, 9 |
| `src/telemetry/ingest.py` | ?? | `predict_synced` | 6 |
| `src/kpi/calculations.py` | M | effective current state; event count on the instant state | 11b |
| `src/reports/generators.py` | M? | latest state via `effective_state` | 11b |
| `frontend/` (types, MaintenanceForm, WorkOrderDrawer, MachineGrid/Detail, AlertsPage) | M | **required** Task 12 | 12 |
| `tests/api/test_rul_persistence.py` | M | stub `baseline_window: 2` | 3 |
| `tests/prediction/test_rul_store.py` | committed | rehydrate parity test passes `epoch` | 5 |

**New files:**
- `src/prediction/health_epoch.py`
- `tests/prediction/test_health_ratchet.py`
- `tests/prediction/test_health_epoch.py`
- `tests/prediction/test_predict_synced_race.py`
- `tests/storage/test_migration_008_health_epoch.py`
- `tests/alerts/test_alert_episodes.py`
- `tests/feedback/test_false_alarm_rearm.py`
- `tests/maintenance/test_maintenance_reset.py` (there is no `tests/maintenance/` today; add an `__init__.py` only if sibling test dirs have one)
- `tests/api/test_health_reset_routes.py`
- `tests/ingestion/test_replay_health_epoch.py`
- `tests/telemetry/test_ingest_health_epoch.py`
- `tests/kpi/test_effective_state.py` (or the existing KPI test dir)
- ~~`experiments/xjtu/v2/verify/ratchet_prod_check.py`~~ (drift: scratch, kept outside the repo; `experiments/` is not edited, see Task 11)
- `tests/maintenance/__init__.py`

**Not touched:** `src/storage/db.py` (the DDL lives in `migrations.py`), `src/alerts/paging.py`, the demo routes, `src/training/xjtu_rul.py` and the other ML files.

**Baseline before starting:**
- [ ] `cd C:/projects/MaintainIQ && python -m pytest -q -p no:cacheprovider` and `cd frontend && npx vitest run`. Record both pass counts (1043 / 451 at `bdf5a65`). Every task must keep the rest of both suites green.

---

## Design decisions

### D1. Normal repair path resets (blocker 1), atomically (review R1-8)

- `src/maintenance/records.py` is split into:
  - `_log_maintenance_locked(conn, ..., reset_health, now)`: no lock, no commit. It
    validates, inserts the record and, if the reset resolves True, calls
    `health_epoch._reset_locked(conn, machine_id, reason=f"maintenance:{id}", now=now)`.
    It returns `(record, resolved_alerts)`.
  - `log_maintenance(...)`: the public wrapper. It takes `live._TRANSITION_LOCK`, calls
    the locked function, commits (or rolls back), then calls `announce_resolved`.
- `work_orders.service.complete` holds `_LOCK`, then `live._TRANSITION_LOCK`, and inside
  them:
  1. calls `_log_maintenance_locked`;
  2. writes the default feedback (D11);
  3. calls `_transition(..., commit=False)`. `_transition` gets a `commit: bool = True`
     parameter; its conflict path already rolls back, which now also undoes the record
     and the reset;
  4. commits **once**.

  `announce_resolved` runs after the commit and outside both locks. Completion is now
  atomic. A failed `_transition` leaves no orphan record and no reset, so a retry is
  clean.
- **Lock order:** `_LOCK -> _TRANSITION_LOCK`. Nothing takes them in reverse:
  work-order creation and feedback take only `_TRANSITION_LOCK`. Document this in the
  `work_orders/service.py` docstring and replace the "Not atomic" paragraph in
  `complete`.
- The predictor is never called from these services. It notices the new epoch at its
  next `predict_synced`.

### D2. When does a maintenance record reset? (blocker 5, reviews R1-2, R2-2, R2-3, R2-4)

**No new type, no table rebuild.** Migration 008 adds
`maintenance_records.resets_health INTEGER NOT NULL DEFAULT 0`. The CHECK
('preventive','corrective') stays; a reset is a property of the record, not of its type.

`reset_health: bool | None` is resolved in `_log_maintenance_locked`:

| Caller | `reset_health=None` (default) resolves to | Explicit value |
|---|---|---|
| POST `/api/maintenance` | **False**, always. A free-standing record never resets implicitly. | `True` requires admin/supervisor (403 otherwise); `False` is always allowed |
| `work_orders.complete` | **True** only if all hold: the type is `corrective`; the order has an `alert_id`; that alert is a real alert of the machine's **current episode** (`alerts.health_episode = current episode`, open or human-closed); and the date guard passes. Otherwise **False**. Free-standing orders, preventive work and stale-alert orders do not reset. | wins; the date guard still applies |

**Date guard** (applies whenever the resolved value is True):
- `performed_at` more than 5 minutes in the future: `MaintenanceError` (reuse
  `feedback.service.FUTURE_SKEW`).
- `performed_at` earlier than `epoch_started_at` or the linked alert's `opened_at`:
  - with an explicit `True`, `MaintenanceError("cannot reset health tracking with a
    record dated before the current fault")`;
  - with the default, it silently resolves to False.

  A backdated history entry never resets today's episode.

**Timestamps:** the system resolve uses wall-clock `now` for `alerts.resolved_at` and for
the broadcast `at`, never `performed_at`. That avoids a negative MTTR.

**Roles:**
- An operator can reset only by completing a work order they are the assignee of. The
  existing `_require_assignee_or_supervising` covers this.
- POST `/maintenance` with `reset_health=True` and the DELETE state route require
  admin/supervisor. This is one policy for both.
- POST `/maintenance` now takes `user=Depends(get_current_user)`. `app.py` already
  requires authentication, so this adds no new login requirement.

The existing `MaintenanceForm` default (corrective when an alert is linked) no longer
resets anything by itself. Resets come from the explicit checkbox (Task 12) or from
work-order completion of the episode's alert.

### D3. Commissioning warm-up must not latch (blocker 2, review R2-5)

- The gate uses the artifact's `baseline_window` (20, `BASELINE_WINDOW`), the same
  COMMISSION=20 as the offline protocol.
- For rows with `snapshots_seen <= baseline_window` and the ratchet on:
  - reported `health_state` = the held level;
  - `_max_state` is **not** updated;
  - result `commissioning = {"seen": k, "of": 20}`;
  - a `commissioning:` warning is added;
  - `instant_health_state` carries the raw value.
- Every repair reset restarts the gate: `_reset_state` pops `_cycles`, and a new epoch
  rehydrates nothing. A false-alarm re-arm does **not** restart it.
- The UI must not show a just-repaired machine as plain green. It shows
  "Commissioning k/20" and, if the instant state is not healthy, the instant state as a
  secondary badge (Task 12). Commissioning rows open no alert (see the review
  resolution, R2-2, for why there is no "commissioning anomaly" alert).

### D4. Concurrency: sync and predict are one critical section (review R1-1, R1-13)

- `predict_synced` holds `_SYNC_LOCKS[machine_id]` across sync and predict. The only
  in-memory resets and restores happen inside `_prepare_locked`, under that lock. So an
  in-process reset can no longer land between a prediction's locked sections.
- Defence in depth: the predictor's generation counter. Every `_reset_state` and
  `restore_health` bumps `_gen[m]`. `_predict_from_base` captures `gen`, `epoch`,
  `episode` and `snapshots_seen` in section 1. Before mutating the probability and
  warning deques (section 2) and before the ratchet update (section 3), it checks
  `self._gen[m] == gen`. If not, it raises `StaleEpoch`, a `RuntimeError` subclass
  exported from `rul_realtime`.
- Resets from other connections or processes only bump the DB. An in-flight result then
  carries the old episode and is rejected by the conditional persist (D6).
- `reset_machine` (public) stays for the offline CLI and the tests. Its docstring says
  "epoch-unaware; production callers must use health_epoch, and a bare reset is undone
  by the next predict_synced". The DELETE route no longer calls it (D9). Internally,
  `_reset_state` is what bumps `gen`, and `reset_machine` delegates to it.
- Replay restart races, where the old worker is still mid-predict after the 5 s join:
  the old worker holds `_SYNC_LOCKS[m]`, so the new worker's sync waits. The old result
  carries the old episode, and the DB was bumped by `start`, so persist drops it.

### D5. Rehydrate after a restart, after a replacement, and its bounds (blocker 3, reviews R1-4, R1-5)

- **F1:** `rehydrate` is never called in production today, so every restart cold-starts
  and resolves and re-pages the open alert. After this plan, every predictor caller goes
  through `predict_synced`, which rehydrates on the first use.
- `rul_store.rehydrate(predictor, conn, machine_id, *, epoch)` replays only readings
  joined to **predictions of that epoch**. Old-bearing rows (older epoch), pre-ratchet
  rows (`health_epoch` NULL) and backfilled rows that were never predicted are never
  replayed. `readings` and MQTT `_insert_reading` are not altered.
- **Bounded:**
  - State depends only on the first `baseline_window` rows (`_baseline_history`) and
    the last `max_history` rows. The smoothing and persistence deques are no longer than
    `max_history` (`rul_realtime.py:63-72`).
  - `rehydrate` selects, with `ROW_NUMBER() OVER (ORDER BY p.id)` as the epoch ordinal
    `n` and `COUNT(*)` as `N`, the rows with `n <= baseline_window OR n > N - max_history`.
  - It replays the head, calls `predictor._seek_cycle(m, max(baseline_window, N - max_history))`,
    and replays the tail. The cycle numbers and `history_snapshots` then equal a warm
    predictor's.
  - Cost is at most `baseline_window + max_history` classifier calls, whatever the
    epoch length.
  - Migration 008 adds `CREATE INDEX IF NOT EXISTS idx_predictions_machine_epoch ON
    predictions(machine_id, health_epoch, id)`.
- **Model change** (D10) is the one case that replays the whole epoch, once per machine
  per deploy.
- **Robust:**
  - Rows whose `features_json` is empty, unparsable or missing a trained feature are
    skipped individually. The check is done **before** calling `_predict_from_base`, so
    a bad row never half-mutates the deques. Each skipped row is logged.
  - Any other exception inside `_prepare_locked` is logged. The machine is then
    `_reset_state`'d again and restored with
    `restore_health(epoch, episode, max_state)` on empty history.
  - The machine therefore always ends synced to the DB epoch and episode, and the next
    reading does not retry the failing rehydrate.
- After rehydrate, `restore_health` sets the held level from the DB, which is
  authoritative. Route-only machines (POST `/rul` stores no `reading_id`) rehydrate
  nothing, restart commissioning and report the held level, so they never demote.

### D6. Stale results are never persisted (reviews R1-12, R2-8)

- `persist_prediction` writes with a single statement:

  ```sql
  INSERT INTO predictions (..., health_epoch, health_episode, instant_health_state)
  SELECT ?, ..., ?, ?, ?
  WHERE COALESCE((SELECT episode FROM machine_health_state WHERE machine_id = ?), 0) = ?
  ```

  If `rowcount == 0` it returns `None`.
- It runs only when `result["health_episode"]` is not None. Results with no episode
  (fakes, the demo, an old DB) use the legacy INSERT.
- In the same transaction, when the row was inserted and `result["health_ratchet"]` is
  true, it calls `record_level`.
- Callers on `None` (drift fix: persist is done by the callers, which then call
  `handle_prediction` / `_on_prediction`; none of them may call it on `None`):
  - the route (`predictions.py:67`) returns **409** "machine health was reset during
    this prediction; retry", with no `handle_prediction` call;
  - replay (`replay_service.py:163`) and MQTT ingest (`ingest.py:435`) log at INFO,
    skip `_on_prediction`, and continue (ingest still records the message as accepted).
- **Current-state readers** use `health_epoch.effective_state(conn, machine_id, latest_pred)`:
  - If `machine_health_state` exists and the latest prediction's `health_episode` is
    older than the current episode, or is NULL while the machine has a row, the current
    state is `max_state` and is flagged `reset_pending_reading`.
  - Otherwise it is the prediction's `health_state`.

  After a reset or re-arm with no new reading, the tile therefore shows the post-reset
  state immediately. Used by `kpi._machine_health` and `reports.generators` (latest
  state). No marker prediction rows are inserted.

### D7. Alert episodes (blocker 4, reviews R1-6, R2-9)

Alert episode = (machine, real kind, `health_episode`). Each real alert stores
`alerts.health_episode` at INSERT.

`pipeline.handle_prediction` passes `health_episode` to `apply_reading` **only when
`result.get("health_ratchet")` is true**. With the ratchet off (kill switch) the alert
path is exactly legacy, which makes it a real rollback. The persist-level stale skip (D6)
stays on in both modes: it is mechanical correctness, not a behaviour policy.

When `health_episode` is given, `_apply_reading` does the following:

1. **Stale drop.** If `health_episode` is not the DB episode, return `None`.
2. **Abnormal with no open alert.** Select the **newest resolved real alert of this
   episode**:
   - If it is human-closed (`closed_by IS NOT NULL`) and its severity rank is **>=** the
     new severity, return `None`. Nothing is inserted or paged.
   - Otherwise (no prior alert, a system-resolved one, or a lower severity) insert and
     page.
3. **Healthy with an open alert.** Resolve, as today. With the ratchet this only
   happens at an episode start, which the reset or re-arm already handled.

### D8. Out-of-distribution rows do not latch (review R2-6)

The evidence covers XJTU only, and the live MQTT fan is flagged `out_of_distribution`.
For a row with `outside_fraction > 0.10` and the ratchet on:
- reported `health_state` = `max(instant, held)`, so it is shown and can alert as today;
- `_max_state` is not raised;
- warning `ood_not_latched:` is added.

A run of OOD rows therefore behaves like legacy **above the held floor**. It can demote
back to the held level, and it can re-page as today on OOD flapping. An OOD blip never
holds a machine red until maintenance. In-distribution rows latch as designed.
Task 11 verifies on XJTU that this rule leaves MC 8 / EP 2 / demotions 0 (the stop
condition is in Task 11).

### D9. Replay (blocker 6, review R1-11)

- `ReplayService.start`:
  1. Under `self._lock`, check for an alive thread and return if one is found
     (idempotent, as today). Then release the lock.
  2. **Outside the lock**, run the live guard and `_load_worklist` (both DB-only), then
     `health_epoch.reset_machine_health(conn, m, reason="replay_restart")` and
     `announce_resolved`. A duplicate bump from two racing starts is harmless.
  3. Re-take `self._lock`. If a thread became alive meanwhile, return. Otherwise store
     `self._start_epoch[m] = new_epoch`, the worklist and the state, and spawn the
     thread.

  `_TRANSITION_LOCK` and the SQLite busy waits are never held under `self._lock`, so
  they cannot stall the other workers or `status()`.
- `replay_once` runs through `predict_synced`. Before predicting, if the DB epoch is not
  `self._start_epoch[m]`, someone else reset the machine (for example a work order
  completed mid-replay). It returns `False`, which ends the run, and sets
  `state["stopped_reason"] = "health_reset"`. The demo cannot keep feeding a failing
  trajectory into a fresh baseline. A re-arm (episode only) does not stop the replay.
- The worklist SQL stays unfiltered: replay re-reads dataset rows by design.

### D10. Model change (review R1-7)

`machine_health_state.model_version` records the artifact that derived `max_state`.
In `_prepare_locked`:
- DB `model_version` NULL (a migration seed, or written by an older build): adopt the
  current version and keep `max_state`. Seeds are never discarded.
- DB `model_version` differs from `predictor.artifact["model_version"]`:
  1. Rehydrate the **whole** current epoch, unbounded, once.
  2. Take the latched level over the rows of the current **episode** (rows with
     `health_episode` = current episode).
  3. Floor it at the `health_state` of the machine's **open** real alert, if any. A model
     change never silently resolves an alert.
  4. Write it to the DB with the new `model_version`, conditional on the episode.

  A held level from a noisier old artifact is dropped unless the new model reproduces
  it, or an alert is still open.

### D11. Feedback on maintenance-resolved alerts (review R2-11)

When `complete` resets and resolves the order's alert, and the alert has no
`alert_feedback` row, it upserts one in the same transaction:
- `outcome = 'maintenance_prevented'`
- `work_order_id` = the order
- `recorded_by` = the actor

It uses `feedback.service._upsert`. The value is editable afterwards. POST `/maintenance`
resets (supervisor) do not invent feedback, because they have no work-order context.
The Alerts page keeps its "Record outcome" prompt for those.

### D12. False-alarm re-arm (review R2-1)

`feedback.service.close_alert` and `record_feedback`, inside their existing
`_TRANSITION_LOCK` transaction, call `health_epoch._rearm_locked(conn, machine_id, now=now)`
when all of the following hold:
- the outcome is `false_alarm`, or `actual_cause` is `sensor_or_data_quality_issue`;
- the alert is real;
- `alert.health_episode` equals the machine's current episode.

`_rearm_locked` bumps the episode and sets `max_state = 'healthy'`. It does not touch
the epoch, the baseline or commissioning. Effects:
- The tile turns green at once, through `effective_state`.
- The next in-distribution non-healthy reading latches again into the new episode and
  opens one new, paged alert. A recurring false signal therefore pages **once per
  re-arm**, not once per reading.
- `confirmed_failure`, `maintenance_prevented` and `unknown` keep the held level and
  the suppression. The UI then prompts for a repair (Task 12).

Re-arm has no role restriction beyond who may already close or record feedback.

### D13. Ratchet default and kill switch (blocker 7, review R2-9)

The ratchet is **on by default**, resolved in this order:
1. the constructor kwarg `ratchet=`;
2. env `MAINTAINIQ_HEALTH_RATCHET` (`0|false|off|no` / `1|true|on|yes`);
3. `artifact.get("health_ratchet", True)`.

Every result carries `health_ratchet: bool`.

**Ratchet off** gives:
- exact legacy `health_state` (no gate, no latch, no OOD rule);
- no `record_level`;
- no episode suppression, because the pipeline passes no episode.

Epoch tagging, resets, stale-persist skip, re-arm bookkeeping and `effective_state`
still run. They are neutral when `max_state` stays `healthy`, because with the ratchet
off `restore_health` forces `healthy` in memory and `effective_state` only differs after
a reset with no new reading.

**Tests that change:**
- `tests/api/test_rul_persistence.py:175-235`: the stub artifact goes from
  `baseline_window: 20` to `2`. The tests keep exercising the production default.
- `tests/prediction/test_rul_store.py:163-200`: the parity test passes `epoch=`.
- `tests/test_rul_realtime.py`: re-run. Any `health_state` assertion that fails gets a
  stub `baseline_window: 2`. Do not turn the ratchet off.
- `tests/test_maintenance.py` and the work-order tests: a dict-equality assertion may
  need `resets_health`. A work-order test that completes an order **linked to an open
  alert** now resets and resolves that alert. Update only such assertions, and list
  them in the commit message.
- Fakes in `tests/api/test_ingestion_control.py`, `tests/ingestion/test_replay_service.py`
  and `tests/telemetry/test_ingest.py` need no change. `predict_synced` with a predictor
  lacking `restore_health` just calls `call()`.
- `tests/feedback/test_service.py:93-99` (re-open after close, with no episode) stays
  as-is. It documents the epoch-less contract.

### D14. Deploy seed (reviews R1-3, R1-10, R2-10)

For each machine, migration 008 picks at most one **seed alert**, restricted to
`source = 'xjtu_rul'`, so other detectors' alerts never seed:
1. the worst **open** `xjtu_rul` alert; otherwise
2. the **newest resolved** `xjtu_rul` alert whose `resolved_at` is later than the
   machine's latest `maintenance_records.performed_at` (or any time if there is none),
   **only if** that newest alert is human-closed (`closed_by IS NOT NULL`) and its
   `alert_feedback` (if the table exists) is not `false_alarm` or
   `sensor_or_data_quality_issue`.

For the seed alert:
- `alerts.health_episode = 0`;
- insert `machine_health_state(machine_id, epoch 0, episode 0, max_state = alert.health_state, model_version NULL)`.

Machines without a seed get no row, which means `(0, 0, 'healthy')`.

Effects:
- A pre-deploy open alert is not resolved by a cold first reading.
- A pre-deploy human-closed alert is suppressed by D7 instead of re-paging about 21
  snapshots after deploy.

---

## Task 1: Migration 008, health schema and seed

**Files:**
- Modify: `src/storage/migrations.py` (append `_migration_008_health_epoch`, add it to `MIGRATIONS`)
- Test: `tests/storage/test_migration_008_health_epoch.py`

- [ ] **Step 1: failing tests**
  - `test_fresh_db_has_health_schema`:
    - `LATEST_VERSION == 8`;
    - `machine_health_state` has `machine_id, epoch, episode, max_state, model_version, epoch_started_at, episode_started_at, reset_reason, updated_at`;
    - `predictions` has `health_epoch, health_episode, instant_health_state`;
    - `alerts` has `health_episode`;
    - `maintenance_records` has `resets_health`;
    - index `idx_predictions_machine_epoch` exists.
  - `test_v7_upgrade_seeds_open_real_alert`: an open `xjtu_rul` critical alert gives a seed `(0, 0, 'critical', NULL)` and `alerts.health_episode = 0`. An open demo alert gives no seed. An open alert with another `source` gives no seed.
  - `test_v7_upgrade_seeds_human_closed_after_maintenance`: an `xjtu_rul` faulty alert human-closed after the machine's last maintenance record gives a seed `faulty` and episode 0 on that alert. If the latest maintenance is later than `resolved_at`, there is no seed.
  - `test_no_seed_when_newest_resolved_is_system_resolved` (human-closed A, then system-resolved B, gives no seed).
  - `test_no_seed_for_false_alarm_feedback`.
  - `test_migration_008_is_idempotent_on_rerun`.
  - `test_legacy_rows_get_null_epoch_and_episode`.

  To build a v7 DB, copy the pattern in `tests/storage/test_migrations.py`. Read it but do not edit it.
- [ ] **Step 2:** the tests fail (`LATEST_VERSION == 7`).
- [ ] **Step 3: implement.**
  - `CREATE TABLE IF NOT EXISTS machine_health_state (...)` with
    `CHECK(max_state IN ('healthy','degrading','faulty','critical'))`, `epoch` and
    `episode INTEGER NOT NULL DEFAULT 0`, and `model_version TEXT`.
  - Guarded `ALTER TABLE ... ADD COLUMN` for the four tables. `health_epoch`,
    `health_episode` and `instant_health_state` are nullable with no default.
  - `CREATE INDEX IF NOT EXISTS`.
  - The seed is implemented in Python inside the migration: per machine, run the two
    queries of D14, guard with `table_exists(conn, "alert_feedback")`, and
    `INSERT OR IGNORE`. Use the literals `'demo'` (`live.DEMO_SOURCE`) and `'xjtu_rul'`
    (`rul_store.PREDICTION_SOURCE`); do not import the modules into migrations.
- [ ] **Step 4:** `python -m pytest tests/storage -q -p no:cacheprovider`, then the full suite. (Drift check: `tests/storage/test_migrations.py` derives `LATEST` from `MIGRATIONS`, so it needs no edit.)
- [ ] **Step 5:** commit "Add migration 008: health epoch/episode, ratchet level and deploy seed".

## Task 2: `src/prediction/health_epoch.py`, the DB authority

**Files:** create `src/prediction/health_epoch.py`; test `tests/prediction/test_health_epoch.py`.

**API:**

```python
HEALTH_RANK = {"healthy": 0, "degrading": 1, "faulty": 2, "critical": 3}
REARM_OUTCOMES = {"false_alarm"}; REARM_CAUSES = {"sensor_or_data_quality_issue"}

@dataclass(frozen=True)
class HealthRow: epoch: int; episode: int; max_state: str; model_version: str | None
                 epoch_started_at: str | None

def current(conn, machine_id) -> HealthRow | None
    # HealthRow(0, 0, 'healthy', None, None) when no row; None when the table is missing.
def record_level(conn, machine_id, episode, state, model_version) -> None
    # One UPSERT: INSERT ... ON CONFLICT(machine_id) DO UPDATE SET max_state = CASE WHEN
    # machine_health_state.episode = :episode AND rank(excluded) > rank(max_state)
    # THEN excluded.max_state ELSE max_state END, model_version = COALESCE(...).
    # An insert happens only when :episode == 0 (no row means episode 0). No commit.
def _reset_locked(conn, machine_id, *, reason, now) -> tuple[HealthRow, list[dict]]
    # One UPSERT bumping epoch+1, episode+1, max_state 'healthy', stamps; then SELECT
    # (the same txn holds the write lock, so it is consistent; RETURNING is also OK on
    # 3.49). Then resolve the open real alert(s): status 'resolved', resolved_at = now,
    # closed_by NULL. Return them in the _ALERT_COLUMNS shape. Caller holds _TRANSITION_LOCK.
def _rearm_locked(conn, machine_id, *, now) -> HealthRow   # episode+1, max_state healthy
def reset_machine_health(conn, machine_id, *, reason, now=None) -> tuple[HealthRow, list[dict]]
    # Takes _TRANSITION_LOCK, calls _reset_locked, commits or rolls back.
def announce_resolved(conn, alerts, *, at) -> None   # after commit; pipeline.fan_out, swallow+log
def effective_state(conn, machine_id, latest_pred: dict | None) -> dict
    # {"health_state", "reset_pending_reading": bool, "health_episode"}  (D6)
```

- [ ] **Step 1: failing tests:**
  - `current` defaults, and `None` when the table is dropped.
  - `record_level`: raise only (faulty, then degrading, stays faulty); a stale episode is a no-op; on a machine with no row, episode 0 inserts and episode 1 does nothing.
  - `_reset_locked` via `reset_machine_health`:
    - epoch+1 and episode+1;
    - `max_state` healthy;
    - the real alert resolved with `closed_by` NULL and `resolved_at == now`, not `performed_at`;
    - a demo alert untouched;
    - a second call resolves `[]`.
  - `_rearm_locked`: episode+1, epoch unchanged.
  - `effective_state`:
    - a latest prediction at the current episode returns its own state;
    - a latest prediction at an older episode returns `max_state` with `reset_pending_reading=True`;
    - with no table, returns the prediction's state.
  - Concurrency: 2 threads x 50 `record_level` calls interleaved with 1 reset. The final level is consistent with the episode, and there is no IntegrityError. Follow `tests/test_alerts_live_concurrency.py`.
- [ ] **Step 2:** the tests fail. **Step 3:** implement. **Step 4:** the suite is green.
- [ ] **Step 5:** commit "Add health_epoch: DB-authoritative epoch, episode and ratchet level".

## Task 3: predictor ratchet, commissioning gate, OOD rule, generation counter

**Files:** modify `src/prediction/rul_realtime.py` (constructor, `reset_machine`, `_predict_from_base` sections and result); modify `tests/api/test_rul_persistence.py` (stub `baseline_window: 2`); test `tests/prediction/test_health_ratchet.py`.

- [ ] **Step 1: failing tests.** Use a stub artifact in the style of `tests/prediction/test_rul_store.py::_fake_artifact`, a **scripted** classifier/regressor and explicit `base` dicts.
  1. `test_ratchet_holds_worst_state`: instant faulty, healthy, critical, degrading reports faulty, faulty, critical, critical. Check `instant_health_state`, `health_state_held` and the `condition_receded:` warnings.
  2. `test_commissioning_rows_never_latch` (`baseline_window=5`): a result `commissioning` field on rows 1-5 only.
  3. `test_reset_restarts_commissioning`.
  4. `test_ood_rows_do_not_latch`: script `feature_bounds_99pct` so rows 7-9 are OOD and critical, then row 10 is in-distribution and healthy. Rows 7-9 report critical with `ood_not_latched:`, row 10 reports healthy (held level healthy), and `_max_state` is unchanged.
  5. `test_ood_row_does_not_lower_held`: held faulty, then an OOD healthy row reports faulty.
  6. `test_ratchet_off_is_legacy`: kwarg, env and artifact paths. Legacy row by row, `health_ratchet` False.
  7. `test_kwarg_beats_env_beats_artifact`, `test_default_is_on_without_artifact_key`.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: implement.**
  - `_resolve_ratchet` as in D13.
  - New dicts `_max_state`, `_epochs`, `_episodes`, `_gen` (`defaultdict(int)`).
  - `_reset_state(m)` pops all the per-machine deques/dicts **and** `_epochs`, `_episodes`
    and `_max_state`, and bumps `_gen[m]`. `reset_machine` delegates to it.
  - Section 1 also captures `gen = self._gen[m]`, `epoch` and `episode`.
  - Sections 2 and 3 begin with
    `if self._gen[m] != gen: raise StaleEpoch(machine_id)`.
  - Ratchet block (section 3), after `warnings` and `outside_fraction` are known:

```python
instant = self._health_state(predicted) if within_horizon else "healthy"
commissioning = self.ratchet and snapshots_seen <= baseline_window
ood = outside_fraction > 0.10
with self._locks[machine_id]:
    if self._gen[machine_id] != gen:
        raise StaleEpoch(machine_id)
    held = self._max_state.get(machine_id, "healthy")
    if not self.ratchet:
        state = instant
    elif commissioning:
        state = held
    elif ood:
        state = instant if HEALTH_RANK[instant] > HEALTH_RANK[held] else held  # shown, not latched
    else:
        state = instant if HEALTH_RANK[instant] > HEALTH_RANK[held] else held
        self._max_state[machine_id] = state
```

  - Warnings: `commissioning:`, `ood_not_latched:` (only if the instant is above held),
    `condition_receded:`.
  - Result adds `health_state`, `instant_health_state`, `health_state_held`
    (`state != instant`), `health_ratchet`, `commissioning`
    (`{"seen","of"}` or `None`), `health_epoch` and `health_episode`.
- [ ] **Step 4:** in `tests/api/test_rul_persistence.py::_make_predictor`, change `baseline_window: 20` to `2` with the comment `# ratchet commissioning gate: snapshot 3 is the first that may latch (plan D13)`.
- [ ] **Step 5:** run `python -m pytest tests/prediction tests/api/test_rul_persistence.py tests/test_rul_realtime.py -q`, then the full suite. Fix any other expectation only by lowering a stub `baseline_window`.
- [ ] **Step 6:** commit "Ratchet RUL health_state; commissioning and OOD rows never latch".

## Task 4: predictor epoch API

- [ ] **Step 1: failing tests (append to `test_health_ratchet.py`):**
  - `epoch_of`/`episode_of` are None when fresh.
  - `restore_health(m, epoch=3, episode=5, max_state="faulty")` gives a result with `health_epoch` 3 and `health_episode` 5. A post-commissioning `degrading` reports faulty, and during commissioning it reports faulty, not healthy (F1).
  - `restore_health` bumps `_gen`.
  - `_seek_cycle(m, 40)` makes the next result's `history_snapshots == 41`.
  - With the ratchet off, `restore_health` keeps the in-memory held level at healthy.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: implement** `epoch_of`, `episode_of`, `restore_health(m, *, epoch, episode, max_state)` (validates `max_state`, bumps `_gen`) and `_seek_cycle(m, n)` (sets `_cycles[m] = n` under the lock; used only by `rehydrate`).
- [ ] **Step 4:** green. Commit "Predictor epoch/episode tags, generation counter, held-state restore".

## Task 5: `rul_store`, conditional persist and bounded rehydrate

**Files:** modify `src/prediction/rul_store.py` (`persist_prediction` `:28-65`, `rehydrate` `:147-173`); modify `tests/prediction/test_rul_store.py` (parity test); tests in `tests/prediction/test_health_epoch.py`.

- [ ] **Step 1: failing tests:**
  - Persist with `health_episode` equal to the DB episode writes all the new columns and `record_level` raises `max_state`. With a stale episode it returns `None`, inserts no row and leaves the level unchanged.
  - With `health_ratchet` False, the row is inserted but there is no `record_level`.
  - With `health_episode` None, the legacy INSERT runs with NULL columns.
  - `rehydrate(..., epoch=1)` replays only epoch-1 rows.
  - **Bounded:** with 300 epoch rows, `baseline_window=5` and `max_history=30`, it calls `_predict_from_base` 35 times (spy), and the next prediction's `history_snapshots` and `health_state` equal those of a warm predictor fed all 300.
  - **Corrupt row:** one row with `features_json='{'` and one missing a trained feature are skipped and logged, and the rest replay.
  - Rewrite the parity test (`:163-200`) to persist with `reading_id` and episode 0 and to rehydrate with `epoch=0`, plus `restore_health`. Also update `test_rehydrate_skips_readings_without_feature_vector` (`:203`): pass `epoch=0` and give the `'{}'` reading a prediction row of epoch 0, so it still proves the skip.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: implement.**
  - D6 conditional INSERT. Fall back to the legacy INSERT if the columns are missing:
    catch `sqlite3.OperationalError` once and remember the result per connection.
  - D5 SQL:

    ```sql
    WITH e AS (SELECT r.speed_rpm, r.load_kn, r.sample_rate_hz, r.features_json,
                      ROW_NUMBER() OVER (ORDER BY p.id) AS n, COUNT(*) OVER () AS total
               FROM predictions p JOIN readings r ON r.id = p.reading_id
               WHERE p.machine_id = ? AND p.health_epoch = ?)
    SELECT * FROM e WHERE n <= ? OR n > total - ? ORDER BY n
    ```

    Call `_seek_cycle` at the head/tail boundary. Add a `full: bool = False` keyword
    for D10.
  - Per-row validation runs before predicting. Drift fix: `features_json` holds raw
    snapshot features, so do **not** check it against the trained column lists as-is
    (most trained columns are derived by `add_past_context`). Required keys =
    `ROLLING_SOURCE_COLUMNS` plus every column of `classifier_feature_columns` and
    `regressor_feature_columns` that is neither derived (suffix `_baseline_ratio`,
    `_baseline_delta`, `_mean_<w>`, `_std_<w>`, `_trend_<w>` for `w` in
    `ROLLING_WINDOWS`) nor snapshot meta (`speed_rpm`, `load_kn`, `elapsed_minutes`,
    `cycle`, `bearing_id`). Compute this set once per predictor.
- [ ] **Step 4:** run `python -m pytest tests/prediction -q`, then the full suite. Commit "Conditional, episode-guarded persist; bounded, epoch-scoped rehydrate".

## Task 6: `predict_synced` at every predictor call site

**Files:** `src/prediction/health_epoch.py`; `src/api/routes/predictions.py` (`predict_rul`); `src/ingestion/replay_service.py` (`replay_once`; move `conn = self._connection_factory()` above the predict call, keeping the `finally: conn.close()`); `src/telemetry/ingest.py` (around `predictor.predict` `:410`, inside the existing `try`; add an `except StaleEpoch` **before** its generic `except Exception`, see drift item 4). Tests: `tests/prediction/test_health_epoch.py`, `tests/prediction/test_predict_synced_race.py`, `tests/ingestion/test_replay_health_epoch.py`, `tests/telemetry/test_ingest_health_epoch.py`, `tests/api/test_health_reset_routes.py`.

```python
_SYNC_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)

def predict_synced(predictor, conn, machine_id, call):
    if not hasattr(predictor, "restore_health"):
        return call()                                   # fakes: legacy
    with _SYNC_LOCKS[machine_id]:
        _prepare_locked(predictor, conn, machine_id)
        return call()

def _prepare_locked(predictor, conn, machine_id):
    row = current(conn, machine_id)
    if row is None:
        return
    model = predictor.artifact["model_version"]
    if row.model_version not in (None, model):
        _rederive_for_model(predictor, conn, machine_id, row, model)    # D10
        return
    if row.model_version is None and row != HealthRow(0, 0, "healthy", None, None):
        _adopt_model(conn, machine_id, row.episode, model)              # D10 seeds
    if predictor.epoch_of(machine_id) == row.epoch:
        if predictor.episode_of(machine_id) != row.episode:
            predictor.restore_health(machine_id, epoch=row.epoch, episode=row.episode,
                                     max_state=row.max_state)           # re-arm only
        return
    predictor._reset_state(machine_id)
    try:
        rul_store.rehydrate(predictor, conn, machine_id, epoch=row.epoch)
    except Exception:
        log.exception("rehydrate failed for %s; starting the epoch from empty history", machine_id)
        predictor._reset_state(machine_id)
    predictor.restore_health(machine_id, epoch=row.epoch, episode=row.episode,
                             max_state=row.max_state)
```

`_adopt_model` and `_rederive_for_model` take `_TRANSITION_LOCK` and commit their own
small write. Both are conditional on the episode.

- [ ] **Step 1: failing tests:**
  - **Restart restore (F1):** A latches critical over 4 persisted snapshots. A fresh predictor B, through `predict_synced`, gives `critical` with `history_snapshots == 5`.
  - **Reset elsewhere:** after `reset_machine_health`, A's next synced result has `history_snapshots == 1` and is `healthy` with `commissioning`.
  - **Re-arm elsewhere:** after `_rearm_locked`, A keeps its history (snapshots continue) and reports `healthy`, then latches on the next critical.
  - **Rehydrate failure:** monkeypatch `rul_store.rehydrate` to raise. `predict_synced` still returns a result with `health_epoch` equal to the DB epoch and `history_snapshots == 1`, and a second call does not re-run rehydrate (spy count 1).
  - **Model change:** the DB holds `critical` with `model_version='old'` and no open alert. A predictor with model `new` whose scripted classifier never fires gives `max_state` re-derived to `healthy` and DB `model_version='new'`. The same with an open critical alert keeps `critical` and leaves the alert open.
  - **NULL model_version seed:** the seed `critical` is kept and the version adopted.
  - **Race** (`test_predict_synced_race.py`), with an event-gated scripted classifier that blocks inside section 2:
    1. Thread A runs a synced predict and blocks.
    2. Thread B calls `reset_machine_health(conn2, m)` and then a synced predict on the same machine. B blocks on `_SYNC_LOCKS` until A finishes.
    3. A's result carries the old episode. `persist_prediction` returns `None`, there is no alert, and `max_state` is still `healthy`.
    4. B's result is `history_snapshots == 1`.

    In a second variant, call `predictor._reset_state(m)` directly from B while A is blocked. A raises `StaleEpoch`, and `_max_state` and the deques are untouched by A.
  - **Call sites:** spy that replay, ingest and the route go through `predict_synced` with the machine id. The route returns 409 when persist returns `None`.
  - **Timing:** `predict_synced` on a 3,000-row epoch with the stub artifact performs at most `baseline_window + max_history` classifier calls (spy), not 3,000.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3:** implement, and wire the three call sites.
  - Route: `result = health_epoch.predict_synced(predictor, db, m, lambda: predictor.predict(...))`.
  - Each site catches `StaleEpoch` (route: 409, in its own `except` because the existing one catches only `ValueError`; replay and ingest: log at INFO and skip, and ingest must catch it before its generic `except Exception` so it is not recorded as an ERROR) and handles a persist `None` (D6: the caller skips `handle_prediction` / `_on_prediction`).
- [ ] **Step 4:** the full suite. The fakes in `tests/api/test_ingestion_control.py`, `tests/ingestion/test_replay_service.py` and `tests/telemetry/test_ingest.py` must pass unmodified.
- [ ] **Step 5:** commit "Sync predictor to DB health state atomically with every prediction".

## Task 7: alert episodes and stale drop

**Files:** `src/alerts/live.py` (`apply_reading` `:96-110`, `_apply_reading` `:113-177`, INSERT, returned dict, `_ALERT_COLUMNS`, `_resolve_locked` docstring); `src/prediction/pipeline.py` `handle_prediction` `:89-99`. Test: `tests/alerts/test_alert_episodes.py`.

- [ ] **Step 1: failing tests:**
  1. Stale episode dropped.
  2. A new alert stores `health_episode`.
  3. A human close at critical suppresses critical and faulty in the same episode (no rows, no notifications).
  4. A higher severity after a close re-opens and pages.
  5. A reset gives a new episode, which creates and pages.
  6. **R1-6 sequence:** human close at critical, then a same-episode system-resolve (a healthy reading with an open alert, e.g. a model-change edge), then critical **creates**, because the newest resolved alert is system-resolved.
  7. Epoch-less is legacy (`tests/feedback/test_service.py:93-99` unchanged).
  8. **Pipeline with ratchet off:** a result with `health_ratchet: False`, after a human close, re-opens and pages (legacy); `apply_reading` received `health_episode=None`.
  9. **Pipeline with ratchet on:** after a close, `event None` and no `send_email` call.
  10. **Persist `None` (drift fix):** `handle_prediction` never sees it, because the callers persist. This case is tested at the call sites in Task 6 (route 409, replay/ingest skip `_on_prediction`), not here.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: implement.**

```python
real = source != DEMO_SOURCE
if real and health_episode is not None:
    row = conn.execute("SELECT episode FROM machine_health_state WHERE machine_id = ?",
                       (machine_id,)).fetchone()
    if health_episode != (row[0] if row is not None else 0):
        return None
# abnormal and no open alert, before the INSERT:
if real and health_episode is not None:
    last = conn.execute(
        f"""SELECT severity, closed_by FROM alerts
            WHERE machine_id = ? AND status = 'resolved' AND health_episode = ?
              AND COALESCE(source, '') != '{DEMO_SOURCE}'
            ORDER BY id DESC LIMIT 1""", (machine_id, health_episode)).fetchone()
    if last is not None and last["closed_by"] is not None \
            and SEVERITY_RANK[severity] <= SEVERITY_RANK[last["severity"]]:
        return None
```

  - `pipeline.handle_prediction`:
    - `health_episode = result.get("health_episode") if result.get("health_ratchet") else None`,
      passed to `apply_reading`. (The persist-`None` early return lives in the callers,
      Task 6; `handle_prediction` does not call `persist_prediction`.)
  - Add `health_episode` to the INSERT, the returned dict and `_ALERT_COLUMNS`. Grep
    `tests/` for exact-dict equality on alert payloads.
  - `_resolve_locked` docstring: "with the ratchet on, a later abnormal reading in the
    same episode opens a fresh alert only if it is more severe; a repair reset or a
    false-alarm re-arm starts a new episode".
- [ ] **Step 4:** run `python -m pytest tests/alerts tests/feedback tests/prediction tests/test_demo.py tests/test_alerts_live_concurrency.py -q`, then the full suite. Commit "Alert episodes per health episode; drop stale readings".

## Task 7b: false-alarm re-arm (D12)

**Files:** `src/feedback/service.py` (`close_alert`, `record_feedback`, module docstring `:13-14`). Test: `tests/feedback/test_false_alarm_rearm.py`.

- [ ] **Step 1: failing tests:**
  - A held critical with an open alert, `close_alert(outcome="false_alarm")`, gives episode+1, `max_state` healthy, the epoch unchanged, and `effective_state` healthy. The next in-distribution critical reading (pipeline, ratchet on) creates **one** paged alert. Further critical readings do not page again.
  - `actual_cause="sensor_or_data_quality_issue"` with `outcome="unknown"` re-arms.
  - `confirmed_failure` does not re-arm, and the next critical is suppressed.
  - `record_feedback(false_alarm)` on an already human-closed alert of the current episode re-arms. On an alert of an older episode it does nothing.
  - A demo alert never re-arms.
  - Re-arm and close are in one transaction: monkeypatch `_rearm_locked` to raise, and the alert stays open with no feedback row.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: implement** the call inside both functions' existing `try` (lazy-import `health_epoch` to avoid a cycle). Update the docstring at `:13-14`, which today says "the next abnormal reading opens a fresh alert": describe episodes and re-arm.
- [ ] **Step 4:** run `python -m pytest tests/feedback tests/alerts -q`, then the full suite. Commit "False-alarm feedback re-arms the held health level".

## Task 8: maintenance and work-order reset (D1, D2, D11)

**Files:** `src/maintenance/records.py`; `src/work_orders/service.py` (`complete`, `_transition` `commit=` parameter, docstrings); `src/api/schemas.py` (`MaintenanceCreate.reset_health: Optional[bool] = None`, `WorkOrderComplete.reset_health: Optional[bool] = None`, `MaintenanceRecord.resets_health: bool = False`); `src/api/routes/maintenance.py` (`user=Depends(get_current_user)` imported from `src.auth.deps`, 403); `src/api/routes/work_orders.py`. Test: `tests/maintenance/test_maintenance_reset.py` (create `tests/maintenance/__init__.py` too).

- [ ] **Step 1: failing tests:**
  1. POST-style `log_maintenance(type="corrective")` without `reset_health` does not reset (D2: the API default is False).
  2. `log_maintenance(reset_health=True)` resets:
     - `resets_health == 1`;
     - epoch+1 and episode+1;
     - the alert resolved with `resolved_at == now` while `performed_at` stays the record's value;
     - visible from a second connection together with the record.
  3. **Date guard:** an explicit True with `performed_at` before `epoch_started_at` or before the alert's `opened_at` raises `MaintenanceError`, writing no row and no reset. An explicit True 10 minutes in the future raises.
  4. **Atomicity:** `_reset_locked` raises, so there is no row and the epoch is unchanged.
  5. **Work order, linked to the current-episode alert, default:** a reset, the order `done`, `maintenance_record_id` linked, and feedback `maintenance_prevented` with `work_order_id`.
  6. **Work order, unrelated or free-standing (no `alert_id`) or preventive:** no reset; the open critical alert stays open.
  7. **Work order linked to an older-episode alert:** no reset.
  8. **Work order with a backdated `performed_at` and the default:** no reset, completion succeeds.
  9. **Completion atomicity:** monkeypatch `_transition` to raise `WorkOrderConflict`. No maintenance row, no reset, no feedback, the order is still `in_progress`. A retry succeeds once.
  10. **Existing feedback is not overwritten** by D11.
  11. **Broadcast:** exactly one `alert_resolved` after the commit, and none on rollback.
  12. **HTTP:**
      - POST `/api/maintenance {"type":"corrective"}` as an operator gives 201 with `resets_health: false`;
      - `{"reset_health": true}` as an operator gives 403 and writes no row;
      - as a supervisor, 201 and a reset;
      - POST `/api/work-orders/{id}/complete {"reset_health": false}` gives no reset.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: implement** D1/D2.

```python
def _log_maintenance_locked(conn, machine_id, performed_at, description, technician, *,
                            alert_id, type, reset_health, now, default_reset=False):
    # validation as today, then:
    resets = default_reset if reset_health is None else bool(reset_health)
    if resets:
        resets = _date_guard(conn, machine_id, performed_at, alert_id, now,
                             explicit=reset_health is not None)  # raises or returns False
    cur = conn.execute(INSERT ... resets_health ..., (..., int(resets)))
    resolved = []
    if resets:
        _, resolved = health_epoch._reset_locked(conn, machine_id,
                                                 reason=f"maintenance:{cur.lastrowid}", now=now)
    return {..., "resets_health": bool(resets)}, resolved   # the inline dict log_maintenance builds today
```

  - Drift fix: there is no `_get_record` helper; keep building the returned dict inline
    as `log_maintenance` does today, plus `resets_health`. `log_maintenance` gains a
    `now=None` keyword and a `reset_health=None` keyword. Add `resets_health` to the
    `get_history` SELECT so history rows serialize it.
  - D11 default feedback: call `feedback.service._upsert(conn, alert_id, actor,
    {"outcome": "maintenance_prevented", "actual_cause": None, "actual_failure_at": None,
    "notes": None}, order["id"], now)` only when `alert_feedback` has no row for the alert.

  - `work_orders.complete` computes `default_reset` per D2 (corrective, alert of the
    current episode via `alerts.health_episode`, real) **inside** the locks.
  - Old-DB fallback: if `maintenance_records` lacks `resets_health`, use the legacy
    INSERT and never reset.
- [ ] **Step 4:** run `python -m pytest tests/maintenance tests/test_maintenance.py tests/work_orders tests/api tests/feedback -q`, then the full suite. Update only the assertions listed in D13.
- [ ] **Step 5:** commit "Deliberate repairs reset the health epoch; atomic work-order completion".

## Task 9: replay restart and external reset (D9)

**Files:** `src/ingestion/replay_service.py` (`start` `:188-218`, `replay_once`). Test: `tests/ingestion/test_replay_health_epoch.py`.

- [ ] **Step 1: failing tests:**
  - `start`, `stop`, `start`: the epoch is 2, and the first run's replay alert is resolved.
  - After a restart, the first result has `history_snapshots == 1`.
  - A `LiveMachineError` or an empty worklist does not bump.
  - A `start` while a thread is alive does not bump (idempotent).
  - While `start` performs the reset (monkeypatched to block), `status()` and another machine's `replay_once` return promptly (they do not wait on `self._lock`).
  - **External reset:** a mid-run `reset_machine_health(conn2, m)` makes the next `replay_once` return `False` with `stopped_reason == "health_reset"` and no persisted row. A re-arm does not stop it.
- [ ] **Step 2:** the tests fail. **Step 3:** implement D9. **Step 4:** run `python -m pytest tests/ingestion tests/api/test_ingestion_control.py -q`, then the full suite. If a fake connection factory lacks the table, `health_epoch` must tolerate it (`current` returns None, and `reset_machine_health` is a no-op returning `(None, [])`). Fix this in `health_epoch`, not in the test.
- [ ] **Step 5:** commit "Replay restart begins a new health epoch; replay stops on an external reset".

## Task 10: manual DELETE state route

**Files:** `src/api/routes/predictions.py` `reset_rul_state` `:105-109` (drift: was `:90-94`; `require_role` comes from `src.auth.deps`). Test: `tests/api/test_health_reset_routes.py`.

- [ ] **Step 1: failing tests:**
  - As a supervisor, DELETE `/api/predictions/rul/m2/state` gives 200 `{"machine_id","status":"reset","health_epoch":1,"health_episode":1}` and resolves the open alert. The next POST `/rul` has `history_snapshots == 1`.
  - An operator gets 403.
  - The route calls neither `reset_machine` nor `restore_health` (spy).
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3:** add `db=Depends(get_db)` and `require_role("admin", "supervisor")`; call `reset_machine_health` and then `announce_resolved`. The predictor syncs on its next `predict_synced`.
- [ ] **Step 4:** green. Commit "Manual RUL state reset bumps the DB health epoch (supervisor only)".

## Task 11: docs and offline OOD/parity check

- [ ] **Step 1:** in `docs/DATA_MODEL.md`, document:
  - `machine_health_state` (epoch vs episode);
  - the new columns;
  - held vs instant state;
  - `effective_state`;
  - the reset rules (D2);
  - re-arm (D12);
  - episodes (D7);
  - OOD (D8);
  - the model-change rule (D10);
  - the kill switch (D13).
- [ ] **Step 2: scratch** `ratchet_prod_check.py` (drift fix: `experiments/` must not be edited in this run, so write it, and the D8-modified copy of `ratchet_warmup_check.py`, to a temp directory outside the repo, import from the repo by path, and commit nothing under `experiments/`; record the numbers in the Task 11 commit message) (read-only DB, `file:<db>?mode=ro`). For each XJTU machine, feed `readings.features_json` in cycle order through `_predict_from_base` with the ratchet on and off. Report per bearing:
  - demotions;
  - the first non-healthy index;
  - the count of OOD rows;
  - whether any latch was blocked by D8.

  Also re-run a temp copy of `ratchet_warmup_check.py` (the original stays untouched) with the D8 rule added to its ratchet function: OOD per row from the artifact's `feature_bounds_99pct`.

  **Stop condition:** if D8 changes MC (8), EP (2) or the bearing lists on the OOF evaluation, stop and report to the user before Task 12. Do not tune.
- [ ] **Step 3:** commit the docs only: "Document health epoch, episodes, ratchet and resets".

## Task 11b: current-state and KPI readers

**Files:** `src/kpi/calculations.py` (`_machine_health` `:112-138`, `_abnormal_event_count` `:104-109`); `src/reports/generators.py` (latest state `:87` in `machine_prognostic`, and drift fix: `fleet_summary` latest rows `:214-232`; apply `effective_state` in both only when `period_end` is None, since a bounded report describes the past). Test: `tests/kpi/test_effective_state.py`.

- [ ] **Step 1: failing tests:**
  - After a reset with no new reading, `_machine_health` reports `healthy` with `reset_pending_reading` True and `health_state_held` False.
  - A held row reports `health_state_held` True.
  - `_abnormal_event_count` counts `COALESCE(instant_health_state, health_state) != 'healthy'`, so 1 instant-critical row followed by 10 held rows counts 1.
  - The report's latest state uses `effective_state`.
- [ ] **Step 2-4:** implement. `_latest_prediction` also selects `instant_health_state` and `health_episode`. Add `health_state_held`, `instant_health_state`, `commissioning` and `reset_pending_reading` to the `_machine_health` dict. Run the full suite. Commit "Current-state readers honour health resets; event counts use the instant state".

## Task 12 (required): operator-visible held, commissioning and reset

(The other session's frontend work has landed in `f72c4d3`.) Without it, operators cannot
tell held from current, or commissioning from healthy (review R2-5).

**Backend:**
- `RULPredictionResponse` gets optional `instant_health_state`, `health_state_held`,
  `health_ratchet`, `commissioning`, `health_epoch` and `health_episode`.
- `MachineSummary` (or the KPI machine schema) gets `health_state_held`,
  `instant_health_state`, `commissioning`, `reset_pending_reading` and
  `held_since` (`episode_started_at`).

**Frontend:**
- `frontend/src/api/types.ts`: the matching optional fields, plus `resets_health` on `MaintenanceRecord`.
- Machine grid/tile and detail:
  - a "Held since `{time}`" badge when `health_state_held`;
  - "Commissioning k/20" (neutral colour, not green) when `commissioning`, with the
    instant state as a secondary badge if it is not healthy;
  - when held, replace the lower-bound RUL text ("RUL > horizon") with "Held: `{state}`
    (current signal: `{instant}`)".
- `MaintenanceForm.tsx`:
  - a checkbox **"Component replaced / fault fixed — restart health tracking (closes the
    open alert, relearns baseline over the next 20 readings)"**, shown only to
    admin/supervisor;
  - default unchecked;
  - sends `reset_health`;
  - relabel the type options so "Corrective" no longer implies a reset.
- `WorkOrderDrawer.tsx` complete dialog:
  - the same checkbox;
  - pre-checked when the order is linked to the machine's current alert and the type
    is corrective (mirrors the D2 default);
  - the text explains the effect;
  - always sends an explicit `reset_health`.
- `MobileMachineDetailPage.tsx`: the same held / commissioning badges (drift fix).
- `AlertCloseForm.tsx` / `AlertCloseDialog.tsx` (drift fix: the close flow is this shared
  component, used by `AlertsPage`, `MachineDetail`, `WorkOrderDrawer`,
  `MachineDetailPage` and the mobile alert pages; put the messages here once):
  - when an operator closes as `confirmed_failure` or `maintenance_prevented`, show
    "The machine stays held at `{state}` until a repair is recorded" with a link to
    complete or create a work order;
  - when closing as `false_alarm`, show "Health tracking re-armed".
- Model warnings `commissioning:`, `condition_receded:` and `ood_not_latched:` appear in
  the detail view, not only in `AlertExplanationView`.

**Tests:** the matching `*.test.tsx`. Run `cd frontend && npx vitest run <paths>`, then `npx vitest run`, `npx tsc -b` and `npm run build`. Also add
backend schema tests that the new fields serialize.

---

## Verification checklist (before claiming done)

- [ ] `python -m pytest -q -p no:cacheprovider` and (from `frontend/`) `npx vitest run`, `npx tsc -b`, `npm run build` pass. The counts are the baselines plus the new tests.
- [ ] `grep -n "_predict_from_base\|predictor.predict(" src/` shows every production call site inside `predict_synced`. The offline CLI `replay_xjtu.py` and `rul_store.rehydrate` are the intended exceptions.
- [ ] `grep -n "reset_machine(" src/` finds no production caller.
- [ ] `log_maintenance` / `_log_maintenance_locked` are the only writers of `maintenance_records`.
- [ ] The Task 11 stop condition was checked and recorded.
- [ ] Manual check (optional, `python -m uvicorn src.api.app:app`):
  1. Start a replay and let it reach critical.
  2. Close the alert as `confirmed_failure`: no new page, and the tile shows held.
  3. Restart uvicorn mid-replay: the held state survives, with no `alert_resolved` and no page.
  4. Complete a work order linked to the alert: the epoch bumps, the replay stops with `health_reset`, the tile shows "Commissioning", and feedback is `maintenance_prevented`.
  5. On another machine, close a held alert as `false_alarm`: the tile is green at once and the next critical pages once.

## Risks and open questions

- **OOD rule (D8)** is a policy choice made without live-fan validation. Task 11 checks
  only that it is neutral on XJTU.
- **Model-change re-derive (D10)** replays a full epoch once per machine per deploy. On
  months-long MQTT epochs this is one slow first reading per machine after a deploy.
  Acceptable; logged with its duration.
- **Multi-process deployments:** the DB is authoritative and the conditional persist
  guards across processes. `_SYNC_LOCKS` is per process. Two processes predicting the
  same machine at once is not a supported topology today, and a second process would
  only produce a stale-dropped row.
- **Commissioning blind spot:** a repaired machine whose fault persists is reported
  "Commissioning", not alerted, for 20 readings. That is mitigated by visibility
  (Task 12) and by resets happening only on deliberate repairs (D2).

---

## Review resolution

R1 is the state-machine lens and R2 is the operator/alerts lens. "Accepted" means the
plan now implements the reviewer's fix or an equivalent.

| # | Finding | Disposition |
|---|---|---|
| R1-1 | In-flight prediction latches the old bearing into the new epoch | **Accepted.** D4: `predict_synced` makes sync and predict one critical section, the generation counter raises `StaleEpoch`, Task 10 bumps the DB only, and there is a race test (Task 6). The `epoch_of is None` window inside `prepare` no longer exists, because nothing predicts on that machine while `_prepare_locked` runs. |
| R1-2 | Any corrective entry resets; baseline built on a failing bearing | **Accepted.** D2: the POST default is False; a work order resets by default only for the current-episode alert; date guard; `resolved_at = now`. Tests 1, 3, 6-8 in Task 8. |
| R1-3 | Deploy re-pages pre-deploy human-closed alerts | **Accepted.** D14 seeds from the newest human-closed `xjtu_rul` alert after the last maintenance and stamps it episode 0. |
| R1-4 | A failing `prepare` leaves the machine broken | **Accepted.** D5 robustness, plus Task 5 and Task 6 tests. |
| R1-5 | Unbounded rehydrate cost | **Accepted.** D5 head+tail replay with `_seek_cycle`, and an index. |
| R1-6 | Ratchet off plus suppression hides a re-failure | **Accepted.** D7 uses the newest resolved alert of the episode, which must be human-closed, and suppression applies only with the ratchet on. Task 7 tests 6 and 8. |
| R1-7 | Held level survives a model change | **Accepted.** D10, with an open-alert floor and NULL-version adoption for seeds. |
| R1-8 | Work-order completion is not atomic | **Accepted (option 1).** D1: `_log_maintenance_locked` plus `_transition(commit=False)` gives one commit under `_LOCK -> _TRANSITION_LOCK`. |
| R1-9 | Epoch bump must be one statement | **Accepted.** UPSERT for both reset and `record_level` (Task 2). |
| R1-10 | Seed reads other detectors' alerts | **Accepted.** D14 restricts to `source = 'xjtu_rul'`. |
| R1-11 | Replay reset under `_lock`; reset mid-replay | **Accepted.** D9: reset outside `_lock`; replay stops on an external epoch change. |
| R1-12 | Latest prediction row still says critical after reset | **Accepted (option 1).** D6: stale results are not persisted, and current-state readers use `effective_state`, so no marker rows are needed. |
| R1-13 | A bare `reset_machine` no longer resets | **Accepted (document).** D4: it is documented as epoch-unaware, no production caller remains (verification grep), and `_reset_state` is internal to `health_epoch`. Making it raise was rejected: the offline CLI and the existing tests use it legitimately without a DB. |
| R2-1 | False-alarm close leaves the machine stuck red | **Accepted.** D12 re-arm (episode without epoch) and Task 7b. `feedback/service.py` is no longer "not touched". |
| R2-2 | Every corrective resets and hides a still-failing machine | **Accepted**, as for R1-2, plus D3/Task 12 visibility (Commissioning k/20, not green; instant state shown) and an explicit checkbox. **Rejected in part:** the separate non-latching "commissioning anomaly" alert. During commissioning, `_baseline_history` is still being formed from those very rows, so the baseline-relative features (`add_past_context`) that drive the classifier are not yet defined. An alert on them would page on an unvalidated signal, which is the 3_5 blip the gate exists to suppress. The instant state is shown instead, and the reset itself now requires a deliberate repair. |
| R2-3 | Backdated or future `performed_at` | **Accepted.** D2 date guard; `resolved_at = now`. |
| R2-4 | POST `/maintenance` reset has no role check | **Accepted.** D2: one policy. An explicit reset via POST needs admin/supervisor (403 test); operators reset via their own work order. |
| R2-5 | Operators cannot read held or commissioning state | **Accepted.** Task 12 is required, with the expanded scope (fields, badges, RUL text, warnings). The "no frontend change" claim is removed. |
| R2-6 | Ratchet makes OOD false positives permanent | **Accepted (option 1).** D8: OOD rows report but do not latch. A per-machine scope flag (option 2) was not added: D8 already prevents permanent OOD latching on every machine, and a second switch would double the configuration surface. Task 11 verifies the rule is neutral on XJTU. |
| R2-7 | KPI abnormal-event count changes meaning | **Accepted.** Task 11b counts on the instant state. |
| R2-8 | Stale prediction can become the latest state | **Accepted.** D6 conditional persist (single statement, so race-free) plus `effective_state`. |
| R2-9 | Kill switch is not a real rollback | **Accepted.** D7/D13: with the ratchet off the pipeline passes no episode, so alerting is legacy. The stale-persist skip stays on, because it only drops rows the DB has already superseded. |
| R2-10 | Episode identity lost across deploy | **Accepted**, as for R1-3 (D14). |
| R2-11 | System resolve skips the feedback loop | **Accepted for work-order completion** (D11, `maintenance_prevented`, editable). **Not applied** to supervisor POST `/maintenance` resets: there is no work order, and inventing an outcome would bias field accuracy. The Alerts page "Record outcome" prompt covers that case. |
| R2-12 | Documentation drift | **Accepted.** Task 7b updates the feedback docstring; Task 12 relabels "Corrective" and adds the explicit reset checkbox. |
