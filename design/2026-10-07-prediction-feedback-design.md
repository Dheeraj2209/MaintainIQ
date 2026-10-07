# Prediction Feedback + Field Accuracy — Design

Status: Approved (decisions below are final for implementation)
Date: 2026-10-07
Source: user requirement (verbatim) "Prediction feedback: when an alert is closed, record what actually happened, then use it to measure real-world model accuracy and for retraining."; `design/_integration_map.md` §4 (feature 2, migration 5) and §7 (cross-cutting checklist); builds on `design/2026-10-07-work-orders-escalation-design.md` (`live.acknowledge_alert` / `get_alert` / `API_ALERT_COLUMNS`, `pipeline.fan_out`, `ensure_current_schema`, work-order completion) and `design/2026-08-04-alert-acknowledge-design.md` (orthogonal-column-not-status rule).

## Problem

The model's only quality numbers are offline:

- **Nobody records what really happened.** An alert ends in one of two ways: a healthy reading auto-resolves it (`src/alerts/live.py:110-117`), or it stays open. There is no way to say "the bearing really failed at 14:20", "we replaced it in time", or "false alarm — loose sensor mount". `maintenance_records.alert_id` links a repair to an alert, but not its outcome.
- **"Accuracy" is a benchmark, not the field.** `kpi.prediction_kpis()` (`src/kpi/calculations.py:247-279`) reshapes `models/evaluation_report.json`, and it hard-codes `root_cause_accuracy` as `not_applicable` (`:272-277`). The `model_performance` report (`src/reports/generators.py:130-191`) has inference telemetry and the registry's offline metrics only.
- **An alert can't be traced back to its prediction.** `rul_store.persist_prediction` returns the new row id (`src/prediction/rul_store.py:28-65`), but every caller drops it: `src/api/routes/predictions.py:67`, `src/ingestion/replay_service.py:161`, `src/telemetry/ingest.py:431`. `alerts` has no `prediction_id`, `reading_id` or `model_version`. Matching on `predictions.timestamp <= alerts.created_at` is unreliable, because replay sets `opened_at = now` while MQTT sets it to the sensor time (map §4).
- **Field data never flows back into training.** `python -m src.training.xjtu_rul --features-csv` (`src/training/xjtu_rul.py:422-441`) only ever sees the XJTU-SY table.

## Goal

1. Any signed-in user can **close** an open alert and, in the same step, record what actually happened. They can also record the outcome of an alert that already auto-resolved. The record is editable afterwards.
2. Every new alert carries the `prediction_id`, `reading_id` and `model_version` that opened it, so feedback can be joined to the exact prediction.
3. `GET /api/model/feedback-accuracy` reports real-world accuracy from that feedback: precision, false-alarm rate, lead time against the 120-minute horizon, RUL error (point estimates only), root-cause accuracy, per model version, next to the offline benchmark.
4. The KPI `prediction` block and the `model_performance` report show the real-world numbers. Their response shapes don't change; keys are only added.
5. `GET /api/model/feedback/export` (and a CLI) produce a CSV in the `build_feature_table` format that `xjtu_rul --features-csv` accepts. Retraining stays a manual, documented CLI step.
6. The UI offers the close/outcome dialog from the Alerts page, Machine Detail and work-order completion. It shows outcomes on alert rows and a field-accuracy card with a CSV export on the Model page.

## Non-goals

- **A `closed` alert status.** `alerts.status` stays `open|resolved`, because KPIs and reports count those values (`kpi/calculations.py:97,158,166,757`, `reports/generators.py:228-233`). A close is a human-driven resolve plus `closed_by`.
- **Automatic retraining, scheduled retraining or model promotion.** The export is an input to the existing manual CLI.
- **Measuring missed failures.** A failure that never raised an alert has no alert to attach feedback to. It is reported as `not_applicable` (decision 11), not inferred from maintenance records.
- **Feedback on device incidents** (`device_incidents`). They are sensor-node health, not model predictions.
- **Cancelling or completing the alert's active work order when the alert is closed.** The order keeps its own lifecycle; the dialog shows a hint.
- **Changing `alert_escalated` semantics, the paging ladder, or `ALERT_EVENT_TYPES`.**
- **Editing anything under `src/prediction/rul_realtime.py`, `src/training/**`, `models/**`, `src/ingestion/xjtu_sy.py` or `src/features/**`.** They are read or imported only.
- **A feedback history table.** One editable row per alert, with `updated_by` / `updated_at`; earlier values are not kept.
- **Dashboard KPI card changes** (`KpiCards.tsx`). The real-world numbers live on the Model page; the KPI API gains them for API consumers and the report.

## Decisions

1. **One `alert_feedback` row per alert, plus four columns on `alerts`.** This is migration 5.
   - `alert_feedback` (`UNIQUE(alert_id)`) holds the outcome, the actual cause, an optional failure time, notes, an optional work order, and who recorded or last edited it.
   - Guarded ALTERs add `alerts.prediction_id`, `alerts.reading_id`, `alerts.model_version` and `alerts.closed_by`. Feature 4 ("Why this alert?") needs the same links.
   - **No `model_version` copy on `alert_feedback`** (the map's DDL had one). `alerts.model_version` is written once on INSERT and never changes (decision 3), so a copy could only drift.
   - **`updated_by` / `updated_at` are added** (not in the map's DDL), because feedback is editable by someone other than its recorder.

2. **Outcomes and causes are closed vocabularies, enforced by CHECKs on the new table.**
   - `outcome` ∈ `confirmed_failure | maintenance_prevented | false_alarm | unknown`.
     - `confirmed_failure`: the machine really failed (or would have, and was found failed).
     - `maintenance_prevented`: a real developing fault, fixed before it failed. This counts as a true positive.
     - `false_alarm`: nothing was wrong with the machine.
     - `unknown`: closed without knowing. It is stored but excluded from every accuracy number.
   - `actual_cause` ∈ the `src/root_cause/rule_based.py:15-18` labels `bearing_wear | imbalance | sensor_or_data_quality_issue | unknown`, plus `other`, or NULL ("not specified").
   - `actual_failure_at` is allowed **only with `confirmed_failure`**. A CHECK and the service both enforce this, so lead time and the export never read a failure time that wasn't a failure.

3. **Alert→prediction links are threaded through the pipeline with keyword defaults, and they keep the opening prediction.**
   - `live.apply_reading(..., *, prediction_id=None, reading_id=None, model_version=None)`. The batch path (`db.insert_alerts`, `db.py:427-438`, explicit column list) and the demo routes (`demo.py:42,83`) keep working unchanged and store NULLs.
   - The links are written on INSERT only. **An escalation (`live.py:97-106`) does not overwrite them.**
     - **Why:** `opened_at` is the timestamp of the opening reading, so "opening prediction" and `opened_at` describe one instant. Lead time and RUL error then share one reference time (decision 9).
     - **What escalation still overwrites:** `probable_cause`/`severity`/`message`, as today. Root-cause accuracy therefore scores the latest cause.
     - Feature 4's explanation snapshot can keep the escalation evidence separately.
   - `pipeline.handle_prediction(..., prediction_id=None, reading_id=None)` reads `model_version` from `result.get("model_version")`, so callers don't pass it.
   - **Callers pass the ids `persist_prediction` already returns:**
     - `predictions.py:67,78`: `prediction_id` only, because this route stores no reading.
     - `replay_service.py:161,172`: `reading_id = row["id"]`.
     - `ingest.py:431,442`: the `reading_id` from `_insert_reading`.
   - **The injectable hooks change signature.** `ReplayService.on_prediction` becomes `(conn, result, features, *, prediction_id=None, reading_id=None)` and `TelemetryIngestor.on_prediction` becomes `(conn, result, features, timestamp, *, prediction_id=None, reading_id=None)`. Their call sites pass the ids as keywords. The test hooks that use fixed positional lambdas gain `**_` (listed in the test plan).
   - **Why not put the ids on `result`:** `result` (minus `input_features`) is broadcast as `prediction` (`pipeline.py:85`) and typed as `RULPrediction` in the UI. A storage id does not belong in that contract.

4. **Closing is a human resolve, done under `live._TRANSITION_LOCK`, and it is idempotent.**
   - `POST /api/alerts/{id}/close` takes the outcome as its body. In one transaction under the lock:
     1. Read the alert (404 if missing).
     2. If its status is `open`: `UPDATE alerts SET status='resolved', resolved_at=:now, closed_by=:user WHERE id=? AND status='open'`.
     3. Acknowledge the alert if nobody has (`live._acknowledge_locked`, `live.py:143-155`), as work-order creation does. A human who closed an alert has seen it, and this keeps "time to acknowledge" and the paging ladder consistent.
     4. Upsert the feedback (the decision 6 rules).
     5. Commit once.
   - **Already resolved** (an auto-resolve, a racing close, or a double click): no status change and no error. The feedback is upserted and the response says `closed: false`.
     - **Why not 409:** the auto-resolve and the human's click race on every recovering machine. A 409 would force the UI into a retry dance for an outcome the user simply wants recorded.
   - **Rollback:** if the upsert is refused (403, 400), the whole transaction rolls back and the alert stays open.
   - **Paging stops by construction.** The paging candidate query and its compare-and-set both require `status = 'open'` (`src/alerts/paging.py:171-172,208-209`).
   - **A later abnormal reading opens a fresh alert.** `_open_alert` (`live.py:41-46`) finds nothing open, so `apply_reading` takes the INSERT branch (`live.py:69-95`). A close while the machine still reads abnormal is therefore short-lived. The dialog says so. This is the intended semantics: closing records a human judgement about *this* episode.
   - **`resolved_at` is the close time** (wall clock), not a reading timestamp. `avg_alert_resolution_hours` (`kpi/calculations.py`, `_maintenance_for_machine`) then includes human closures. That is correct, but it is user-visible (Risks).

5. **Feedback without closing: `PUT /api/alerts/{id}/feedback`.** It creates or replaces the alert's feedback on an open or resolved alert, without touching `alerts.status`. It takes `_TRANSITION_LOCK` for its read-then-write, like every alert read-then-write. An operator can therefore label an alert that auto-resolved hours ago.

6. **Who may write feedback.**
   - **Creating** feedback (through close or PUT): any role (admin, supervisor, operator).
   - **Replacing existing feedback:** its recorder (`recorded_by`), or any admin or supervisor. Anyone else gets **403** `only the recorder, an admin or a supervisor may change this feedback`.
   - A replace sets `updated_by` / `updated_at`; `recorded_by` / `recorded_at` never change.
   - PUT is a **full replacement**: an omitted optional field becomes NULL. The dialog always sends the whole form.

7. **Validation** (service-level 400 unless noted):
   - `outcome` missing or not in the enum, or `actual_cause` not in the enum: **422** (Pydantic `Literal`).
   - `notes` is limited to 2000 characters (**422**).
   - `actual_failure_at`:
     - must parse with `src.telemetry.device_health.parse_iso` (`device_health.py:48-59`; `Z`/`+00:00` accepted, naive treated as UTC) and is stored as UTC `isoformat()`. Otherwise `actual_failure_at is not a valid ISO-8601 timestamp: '...'`;
     - is rejected when the outcome is not `confirmed_failure`: `actual_failure_at only applies to outcome confirmed_failure`;
     - is rejected more than `FUTURE_SKEW = 5 min` in the future: `actual_failure_at is in the future`.
     - It may be earlier than `opened_at`, which means the alert came late. That case is measured, not refused.
   - `work_order_id`, when given:
     - must exist: `unknown work order: {id}`;
     - must be for the alert's machine: `work order {id} is for machine {m}, not {m2}`;
     - if the order has an `alert_id`, it must be this alert: `work order {id} belongs to alert {a}`.

8. **Demo alerts are stored but never measured.** Feedback on `source = 'demo'` alerts is accepted (the demo flow should feel real) and excluded from every accuracy number and from the export (`COALESCE(a.source, '') != 'demo'`).
   - **Demo and real readings never share an episode** (fix, 2026-10-07). `live.apply_reading` keeps one open alert per machine *per kind*: demo (`source = 'demo'`) or real. Before, a real reading escalating a demo-opened alert left it labelled `demo`, so a genuine confirmed failure was dropped from field accuracy, the export and similar incidents; and a demo reading could escalate or resolve a real alert, rewriting the severity and cause this feature scores. Now `alerts.source` always says which kind of reading drove the whole episode, and `explain`'s `synthetic` flag (taken from it) is right too.

9. **Accuracy definitions** (`src/feedback/accuracy.py`):
   - **Labelled** means outcome ≠ `unknown`.
   - **Precision** = (`confirmed_failure` + `maintenance_prevented`) / labelled. **False-alarm rate** = `false_alarm` / labelled. Both are `None` when nothing is labelled.
   - **Lead time:**
     - For `confirmed_failure` rows with `actual_failure_at`: `lead = actual_failure_at − alerts.opened_at` in minutes.
     - Each alert's horizon is its opening prediction's `prognostic_horizon_minutes`, else `DEFAULT_HORIZON_MINUTES = 120.0`. A test pins that default to `src.training.xjtu_rul.PROGNOSTIC_HORIZON_MINUTES`, but the API path does not import that module, because it pulls in sklearn.
     - Buckets: `late` (lead < 0, the alert came after the failure), `within_horizon` (0 ≤ lead ≤ horizon) and `early` (lead > horizon).
     - Reported as count, mean, median, min and max.
   - **RUL error:**
     - Uses the opening prediction (`alerts.prediction_id → predictions`) of `confirmed_failure` rows with `actual_failure_at`.
     - The actual RUL at that prediction is the lead time, because the prediction and `opened_at` are the same instant (decision 3).
     - For `rul_estimate_kind = 'point_estimate'` with a non-NULL `predicted_rul_minutes`: `abs_error = |predicted − lead|`, reported as MAE, median absolute error and count.
     - **`lower_bound` rows are censored** (the model only claimed "RUL > horizon"), so they never enter the MAE. They are counted, together with how many were respected (`lead ≥ predicted_rul_minutes`).
   - **Root-cause accuracy:** over labelled-or-not feedback rows whose `actual_cause` is set and is not `unknown`, the share with `alerts.probable_cause == actual_cause`. `other` always counts as a miss, because the classifier cannot emit it.
   - **Per model version:** the same counts, precision, false-alarm rate, lead-time median, RUL MAE and root-cause accuracy, grouped by `alerts.model_version`. Alerts opened before migration 5 (NULL) form their own group, with `model_version: null`.
   - **Filters:** `model_version` (exact), and `period_start`/`period_end` on `alerts.created_at`. `created_at` is the wall clock (`live.py:64`); `opened_at` can carry 2003 dates for replayed data (the same reasoning as the work-orders design, decision 7).
   - **`status`** is `not_applicable`, with a reason, when the `alert_feedback` table is missing (an old file opened outside `get_db`) or the labelled count is 0. All keys are always present (nulls when not applicable), so the shape is stable for TS and the report.

10. **Offline comparison** comes from `model_registry.metrics_json["failure_detection"]` (written by `xjtu_rul.register_trained_model`, `src/training/xjtu_rul.py:313-321,417`). It uses the row for the `model_version` filter, else the active row (`is_active = 1 ORDER BY deployed_at DESC`, as in `generators.py:159-163`).
    - Returned as `offline: {model_version, precision, recall, false_alarm_count, missed_failure_window_count}`.
    - It is **`null` when absent or unparseable.** `POST /api/predictions/rul` re-registers the model with `metrics_json=None` (`predictions.py:42-47` → `rul_store.register_active_model`, `rul_store.py:118-144`, which overwrites `metrics_json`), so null is a normal state, not an error.
    - The UI labels it "Offline benchmark (per snapshot)": the offline precision scores 10-second snapshots inside a 120-minute window, while the field precision scores alerts. They are shown side by side, never subtracted.

11. **Missed failures** are reported as `{"status": "not_applicable", "reason": "a failure that raised no alert has no alert to label; recall needs failures recorded independently of alerts"}`. A free-standing "failure without alert" record would be a new entity with its own UI. That is out of scope (Non-goals).

12. **KPI wiring keeps shapes stable.**
    - `prediction_kpis()` becomes `prediction_kpis(conn=None)`. The KPI summary (`calculations.py:774`) and `/api/kpis/detail` (`src/api/routes/kpis.py:29`) pass `conn`.
    - **`root_cause_accuracy`:**
      - If the field root-cause sample is non-empty: `{"status": "available", "accuracy", "labelled_count", "correct_count", "source": "operator_feedback"}`.
      - Otherwise the existing `not_applicable` dict, with the reason extended by "; no operator feedback has labelled a root cause yet". The reason still names XJTU-SY, so `tests/test_kpi.py:50-58` keeps passing.
    - **New `real_world` key:** the compact `{status, reason, labelled_count, precision, false_alarm_rate, median_lead_minutes, rul_mae_minutes}`. It is present in both the "evaluation report found" and "not found" branches (`calculations.py:253-254`, the early return).
    - `KpiSummary.prediction` is `dict` (`schemas.py:101`) and the TS `PredictionKpi` has an index signature (`types.ts:91-100`), so no schema break.
    - Without `conn` (older callers), `real_world` is the `not_applicable` dict.

13. **Report:** `model_performance` (`generators.py:130-191`) gains `"real_world": real_world_accuracy(conn, period_start=..., period_end=...)`. `_md_model_performance` (`:370-…`) appends a "## Real-World Accuracy (operator feedback)" section: a metric/value table, a per-model-version table and the offline comparison line, or "_No labelled alerts in this period._". The report stays admin/supervisor-only (`src/api/routes/reports.py:19`).

14. **Export for retraining** (`src/feedback/export.py`):
    - **Episodes:** one per qualifying feedback row. A row qualifies when it is `confirmed_failure`, has `actual_failure_at`, and its alert is not a demo alert.
    - **Readings:**
      - Only `readings.dataset = 'live_mqtt'` (`ingest.LIVE_DATASET`, `src/telemetry/ingest.py:62`) are used. XJTU-SY replay readings are already in the training table with exact labels, and their 2003 timestamps can't be aligned with a wall-clock failure time.
      - **Window:** readings of the alert's machine with `episode_start < ts ≤ actual_failure_at`.
      - `episode_start` is the latest of: any `maintenance_records.performed_at` before the alert's `opened_at`, and any earlier `confirmed_failure` `actual_failure_at` on the same machine. A repair before the alert resets the trajectory; one between the alert and the failure (a failed fix) does not.
      - All comparisons use parsed datetimes (`parse_iso`), never string order.
    - **Columns, in `build_feature_table` order** (`src/ingestion/xjtu_sy.py:120-130`):
      - `bearing_id` = `"{machine_id}:fb{feedback_id}"`. It is unique per episode and can't collide with XJTU's `Bearing1_1` ids when the files are concatenated.
      - `condition` = `machines.operating_condition` (empty when NULL; it is a non-feature column, `xjtu_rul.NON_FEATURE_COLUMNS`, `xjtu_rul.py:53`).
      - `cycle`, re-based to 0..n−1 in time order.
      - `elapsed_minutes` since the episode's first reading.
      - `rul_minutes` = `actual_failure_at − ts`, in minutes.
      - `speed_rpm` and `load_kn`, from the reading.
      - `source_file` = `"reading:{id}"`.
      - Then the `features_json` keys, in the first row's insertion order (which `extract_snapshot_features` fixes), followed by any extra keys sorted.
    - **Skipped rows:** rows whose `features_json` is `{}` or unparseable, or that lack any `xjtu_rul.ROLLING_SOURCE_COLUMNS` (`xjtu_rul.py:33-40`, required by `add_past_context`, `:56-61`), are skipped and counted.
    - **Skipped episodes:** an episode left with 0 rows is skipped.
    - **Output:** written with the stdlib `csv` module. The route never imports pandas or sklearn.
    - **CLI:** `python -m src.feedback.export --out outputs/feedback_features.csv [--merge-with outputs/xjtu_features.csv --merged-out outputs/xjtu_plus_feedback.csv] [--model-version V] [--db PATH]`. `--merge-with` concatenates with pandas (column union, NaN for missing) so the merged file can go straight to `python -m src.training.xjtu_rul --features-csv outputs/xjtu_plus_feedback.csv`.
    - **Why a merge step:** training needs at least three bearing groups (`xjtu_rul.py:180-181`) and reads exactly one CSV, so field episodes are added to the XJTU table, not trained alone.
    - Retraining and promoting the model stay manual (README).

15. **Feedback is embedded in API alert rows as one JSON subquery.**
    - `live.API_ALERT_COLUMNS` (`live.py:35-38`) gains a correlated `json_object(...)` subquery `AS feedback_json`. It joins `users` for `recorded_by_name`.
    - A new `live.api_alert(row) -> dict` turns `feedback_json` into `feedback: dict | None`.
    - The three readers use it: `alerts.py:44` (list), `machines.py:43-48` (machine detail) and `work_orders.service.get_work_order` (`service.py:154-158`).
    - This is one statement and avoids N+1 queries.
    - Broadcast alerts (`_ALERT_COLUMNS`, the create dict) carry no `feedback`. The feedback events carry it beside the alert.

16. **Realtime events** are two new types. Both are `{type, machine_id, alert, feedback, at}`.
    - `alert_closed` is sent when a close changed the status.
    - `alert_feedback_recorded` is sent for a PUT, and for a close on an already-resolved alert.
    - **Neither is paged:** they are not in `pipeline._PAGING_EVENTS` (`pipeline.py:28`) and are emitted from the routes, not `fan_out`.
    - **Neither type is ever published to devices.** Neither is added to `protocol.ALERT_EVENT_TYPES` (`src/telemetry/protocol.py:65`).
    - **The device LED is still cleared.**
      - **Why:** after a human close no `alert_resolved` will ever follow for that alert. `apply_reading` returns `None` on a healthy reading with nothing open (`live.py:119`). The device's retained alert topic would then keep the LED on indefinitely.
      - **Mechanism:** a new `protocol.device_alert_event(event) -> dict | None` returns ALERT_EVENT_TYPES events unchanged. For `alert_closed` it returns a **copy typed `alert_resolved`**, the device protocol's existing vocabulary, carrying the now-resolved alert. Every other event returns `None`.
      - `MqttIngestService._on_realtime_event` (`src/telemetry/mqtt_service.py:390-404`) publishes that instead of testing `is_alert_event`.
      - `alert_feedback_recorded` maps to `None`, since the alert's status didn't change.
      - The `alert_closed` type itself never appears on MQTT. The device contract (`design/M6_LIVE_TELEMETRY.md` §5) is unchanged.

17. **Work-order completion offers the dialog. The two writes are not merged into one API call.**
    - After `POST /api/work-orders/{id}/complete` succeeds on an order with an `alert_id`, the drawer's action area is replaced by the embedded `AlertCloseForm`. It is pre-filled with `work_order_id` and the completion notes, and has a "Skip" button.
    - If the alert already has feedback, the form opens in edit mode only when the user may edit it (decision 6). Otherwise nothing is offered.
    - **Why separate calls:** completion is already non-atomic (`service.py:377-410`, work-orders Risks). Bundling feedback would add a third commit to that chain and couple the work-order service to feedback rules. Two explicit user steps are easier to reason about. Skipping is a legitimate choice.
    - **Why embedded, not a second modal:** the drawer is itself a dialog using `useDialogFocus`, and a nested modal would fight it for Escape and focus.

18. **No outcome is preselected.** The outcome radio group starts empty and submit stays disabled until one is chosen. `actual_cause` defaults to "Not specified", with the model's guess shown as a hint ("Model said: bearing wear"). Preselecting the model's own answer would bias the labels used to grade it.

19. **No new env vars, no scheduler job.** Everything is request-driven. `FUTURE_SKEW` and `DEFAULT_HORIZON_MINUTES` are module constants.

20. **Migration 5** (map §1 numbering) has the same mixed shape as migrations 3 and 4: `executescript(FEEDBACK_SCHEMA)`, then four guarded `ALTER TABLE alerts ADD COLUMN` calls. **No index on the new `alerts` columns**, for the same legacy-DB reason as migration 4 (`migrations.py:113-118`). The feedback table's `UNIQUE(alert_id)` provides its own index.

## Data model

### `src/storage/db.py`

The `alerts` CREATE (`db.py:134-151`) gains four nullable columns after `last_paged_at`:

```sql
    prediction_id INTEGER REFERENCES predictions(id),
    reading_id INTEGER REFERENCES readings(id),
    model_version TEXT,
    closed_by INTEGER
```

All four are nullable with no DEFAULT needed, so the conftest INSERT (`tests/conftest.py:104-115`) and `insert_alerts` (`db.py:427-438`) stay valid. Foreign keys are declared but not enforced; no connection sets `PRAGMA foreign_keys`. That matches `maintenance_records.alert_id`. `closed_by` is a `users.id` with no FK, like `acknowledged_by`.

New constant after `WORK_ORDER_SCHEMA`. `db.py:318` becomes `SCHEMA = SCHEMA + TELEMETRY_SCHEMA + DEVICE_HEALTH_SCHEMA + WORK_ORDER_SCHEMA + FEEDBACK_SCHEMA`:

```python
# Prediction feedback (design/2026-10-07-prediction-feedback-design.md): what
# actually happened after an alert, recorded by a person when the alert is
# closed (or later). One row per alert, editable; the source of real-world
# accuracy (src/feedback/accuracy.py) and of field episodes for retraining
# (src/feedback/export.py). Applied on its own by migration 5 and folded into
# SCHEMA for fresh installs.
FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_feedback (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id           INTEGER NOT NULL UNIQUE REFERENCES alerts(id),
    outcome            TEXT NOT NULL
                       CHECK(outcome IN ('confirmed_failure','maintenance_prevented','false_alarm','unknown')),
    actual_cause       TEXT
                       CHECK(actual_cause IS NULL OR actual_cause IN
                             ('bearing_wear','imbalance','sensor_or_data_quality_issue','unknown','other')),
    actual_failure_at  TEXT,
    notes              TEXT,
    work_order_id      INTEGER REFERENCES work_orders(id),
    recorded_by        INTEGER NOT NULL,
    recorded_at        TEXT NOT NULL,
    updated_by         INTEGER,
    updated_at         TEXT,
    -- A failure time only makes sense for a failure.
    CHECK(actual_failure_at IS NULL OR outcome = 'confirmed_failure')
);
CREATE INDEX IF NOT EXISTS idx_alert_feedback_outcome ON alert_feedback(outcome);
"""
```

Column semantics:
- **`recorded_by` / `updated_by`** are `users.id` with no FK (convention, `DATA_MODEL.md` diagram notes).
- **`recorded_at` / `updated_at`** are wall-clock UTC ISO-8601. `updated_*` stays NULL until the first edit.
- **`actual_failure_at`** is normalised UTC ISO-8601 (decision 7).
- **`work_order_id`** is the order that handled the episode, if any. It is validated against the alert's machine and alert.

### `src/storage/migrations.py`

```python
def _migration_005_feedback(conn: sqlite3.Connection) -> None:
    # Additive: the alert_feedback table plus four nullable alerts columns
    # linking an alert to the prediction/reading/model that opened it and the
    # user who closed it. executescript() COMMITs first; the guarded ALTERs
    # after it are idempotent, so a crash between them re-runs cleanly (same
    # shape as migrations 3 and 4). No index on the new alerts columns, for
    # the legacy-DB reason given in migration 4.
    conn.executescript(FEEDBACK_SCHEMA)
    for column in (
        "prediction_id INTEGER REFERENCES predictions(id)",
        "reading_id INTEGER REFERENCES readings(id)",
        "model_version TEXT",
        "closed_by INTEGER",
    ):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # fresh DB: SCHEMA already has the column

MIGRATIONS = [..., (4, _migration_004_work_orders), (5, _migration_005_feedback)]
```

- `MIGRATIONS` is at `migrations.py:127-133`.
- The import block (`:45-50`) gains `FEEDBACK_SCHEMA`.
- The docstring (`:1-40`) gains a "Migration 5" paragraph.
- `ensure_current_schema` (`:174-199`) needs no change. `LATEST_VERSION` (`:158`) becomes 5 automatically, so the first request through `get_db` upgrades a v4 file. MQTT ingest upgrades through `ensure_telemetry_schema`, and replay start resolves `get_db`.

## Services

### `src/alerts/live.py`

- `_ALERT_COLUMNS` (`:26-30`) gains `prediction_id, reading_id, model_version, closed_by`.
- The create dict (`:79-95`) gains the three link values and `closed_by: None`.
- `apply_reading` / `_apply_reading` (`:52-62`) gain `*, prediction_id=None, reading_id=None, model_version=None`. The INSERT (`:71-77`) writes them. The escalate UPDATE (`:99-103`) and the resolve (`:111-114`) do not touch them (decision 3).
- `API_ALERT_COLUMNS` (`:35-38`) gains:

  ```sql
  , (SELECT json_object('id', f.id, 'alert_id', f.alert_id, 'outcome', f.outcome,
        'actual_cause', f.actual_cause, 'actual_failure_at', f.actual_failure_at,
        'notes', f.notes, 'work_order_id', f.work_order_id,
        'recorded_by', f.recorded_by, 'recorded_by_name', u.name, 'recorded_at', f.recorded_at,
        'updated_by', f.updated_by, 'updated_at', f.updated_at)
     FROM alert_feedback f LEFT JOIN users u ON u.id = f.recorded_by
     WHERE f.alert_id = alerts.id) AS feedback_json
  ```
- New `api_alert(row) -> dict`: `dict(row)`, then pop `feedback_json` and `json.loads` it into `feedback` (None when NULL).
- New `_resolve_locked(conn, alert_id, user_id, now) -> bool`: `UPDATE alerts SET status='resolved', resolved_at=?, closed_by=? WHERE id=? AND status='open'`. Returns `rowcount == 1`. It does not commit, the same contract as `_acknowledge_locked`.
- The module docstring (`:15-17`) adds closing and feedback to "the same lock serialises…".

### `src/prediction/pipeline.py`

- `handle_prediction(..., broadcast=None, prediction_id=None, reading_id=None)` (`:49-57`) passes `prediction_id`, `reading_id` and `model_version=result.get("model_version")` to `apply_reading` (`:74-81`).
- `fan_out` is unchanged.
- The docstring notes that the ids come from `rul_store.persist_prediction`.

### Callers

- **`src/api/routes/predictions.py:67`:** `prediction_id = rul_store.persist_prediction(...)`; `:78-82` passes `prediction_id=prediction_id`.
- **`src/ingestion/replay_service.py`:**
  - `_default_fan_out(conn, result, features, *, prediction_id=None, reading_id=None)` (`:92-95`) forwards both ids.
  - `:161` keeps the returned id.
  - `:172` calls `self._on_prediction(conn, result, base, prediction_id=pid, reading_id=row["id"])`.
- **`src/telemetry/ingest.py`:**
  - `_default_fan_out(conn, result, features, timestamp, *, prediction_id=None, reading_id=None)` (`:156-161`).
  - The type hint at `:208` is widened to `Callable[..., object]`.
  - `:431` keeps the id.
  - `:442` passes `prediction_id=..., reading_id=reading_id`.

### `src/feedback/service.py` (new package `src/feedback/`)

```python
OUTCOMES = ("confirmed_failure", "maintenance_prevented", "false_alarm", "unknown")
TRUE_POSITIVE_OUTCOMES = ("confirmed_failure", "maintenance_prevented")
CAUSES = ("bearing_wear", "imbalance", "sensor_or_data_quality_issue", "unknown", "other")
EDITOR_ROLES = ("admin", "supervisor")
FUTURE_SKEW = timedelta(minutes=5)

class FeedbackError(ValueError): ...            # 400
class FeedbackNotFound(FeedbackError): ...      # 404 (unknown alert)
class FeedbackForbidden(FeedbackError): ...     # 403

def get_feedback(conn, alert_id) -> dict | None           # FeedbackNotFound for an unknown alert
def record_feedback(conn, alert_id, actor, *, outcome, actual_cause=None, actual_failure_at=None,
                    notes=None, work_order_id=None, now=None) -> tuple[dict, dict, bool]
    # -> (alert, feedback, created). PUT semantics (decisions 5-7).
def close_alert(conn, alert_id, actor, *, outcome, actual_cause=None, actual_failure_at=None,
                notes=None, work_order_id=None, now=None) -> tuple[dict, dict, bool]
    # -> (alert, feedback, closed). Decision 4.
```

- `actor` is the `get_current_user` dict (`id`, `role`, `name`).
- **Validation** (decision 7) runs before taking the lock, except for the `work_order_id` check, which needs the alert's `machine_id` and runs under it.
- **Both mutators** take `live._TRANSITION_LOCK`. They run every statement on `conn` and commit once at the end. On any exception they `conn.rollback()` and re-raise, so a 403 on the upsert leaves an open alert open. They never take `work_orders.service._LOCK`, so there is no lock-order issue (the work-orders design states the same invariant).
- **Upsert:**
  1. `SELECT recorded_by FROM alert_feedback WHERE alert_id = ?`.
  2. If none: INSERT with `recorded_by = actor.id`, `recorded_at = now`.
  3. Else: the decision 6 permission check, then `UPDATE ... SET outcome, actual_cause, actual_failure_at, notes, work_order_id, updated_by, updated_at`.
  - `sqlite3.IntegrityError` from a CHECK is a defensive backstop. It maps to `FeedbackError`, because validation should already have caught it.
- **Return values** come from `live.get_alert(conn, id)` (`live.py:122-125`, broadcast shape) and `get_feedback`.

### `src/feedback/accuracy.py` (new)

```python
DEFAULT_HORIZON_MINUTES = 120.0   # == src.training.xjtu_rul.PROGNOSTIC_HORIZON_MINUTES (pinned by a test)

def real_world_accuracy(conn, *, model_version=None, period_start=None, period_end=None) -> dict
def real_world_kpi(conn) -> dict                     # the compact KPI block (decision 12)
def root_cause_kpi(conn) -> dict | None              # None -> caller keeps the offline not_applicable dict
def offline_metrics(conn, model_version=None) -> dict | None
```

- **One SELECT:** `alert_feedback f JOIN alerts a ON a.id = f.alert_id LEFT JOIN predictions p ON p.id = a.prediction_id`, filtered with `COALESCE(a.source,'') != 'demo'` plus the decision 9 filters. Selects `f.outcome, f.actual_cause, f.actual_failure_at, a.opened_at, a.probable_cause, a.model_version, p.rul_estimate_kind, p.predicted_rul_minutes, p.prognostic_horizon_minutes`.
- **Aggregation** is in Python, with `parse_iso`; rows with an unparseable `opened_at` are left out of the lead and RUL figures.
- Rates are rounded to 4 places and minutes to 1.
- **Guard:** if `alert_feedback` is missing, return the not_applicable shape. Use `kpi._table_exists` (`calculations.py:704-707`), moved to a tiny shared helper `src.storage.db.table_exists` and re-exported by kpi so existing callers are untouched.

**Response shape** (all keys always present):

```json
{
  "status": "available",
  "reason": null,
  "model_version": null,
  "period": {"start": null, "end": null},
  "horizon_minutes": 120.0,
  "feedback_count": 7,
  "labelled_count": 6,
  "outcome_counts": {"confirmed_failure": 2, "maintenance_prevented": 2, "false_alarm": 2, "unknown": 1},
  "precision": 0.6667,
  "false_alarm_rate": 0.3333,
  "lead_time": {"count": 2, "mean_minutes": 95.0, "median_minutes": 95.0, "min_minutes": 70.0,
                "max_minutes": 120.0, "within_horizon_count": 2, "early_count": 0, "late_count": 0},
  "rul_error": {"point_estimate_count": 1, "mae_minutes": 12.5, "median_abs_error_minutes": 12.5,
                "lower_bound_count": 1, "lower_bound_respected_count": 1},
  "root_cause": {"labelled_count": 4, "correct_count": 3, "accuracy": 0.75},
  "missed_failures": {"status": "not_applicable", "reason": "..."},
  "by_model_version": [
    {"model_version": "xjtu_rul_20260930", "feedback_count": 6, "labelled_count": 5, "precision": 0.6,
     "false_alarm_rate": 0.4, "median_lead_minutes": 95.0, "rul_mae_minutes": 12.5, "root_cause_accuracy": 0.75},
    {"model_version": null, "...": "pre-link alerts"}
  ],
  "offline": {"model_version": "xjtu_rul_20260930", "precision": 0.71, "recall": 0.83,
              "false_alarm_count": 400, "missed_failure_window_count": 120},
  "exportable_episode_count": 2
}
```

- **When not applicable:** `status: "not_applicable"`, a `reason` (`"no labelled alerts yet"` or `"alert_feedback table not present"`), zero counts, null rates and null/zero sub-blocks with the same keys. `offline` and `exportable_episode_count` are still filled.
- `by_model_version` is sorted with the newest `MAX(a.created_at)` first, and the null group last.
- `exportable_episode_count` comes from `export.count_episodes(conn, model_version=...)`, a cheap COUNT that does not read readings.

### `src/feedback/export.py` (new)

```python
BASE_COLUMNS = ["bearing_id", "condition", "cycle", "elapsed_minutes", "rul_minutes",
                "speed_rpm", "load_kn", "source_file"]   # build_feature_table order

@dataclass
class ExportResult:
    rows: list[dict]; columns: list[str]; episode_count: int; skipped_rows: int; skipped_episodes: int

def count_episodes(conn, *, model_version=None) -> int
def build_export(conn, *, model_version=None) -> ExportResult     # decision 14
def to_csv(result: ExportResult) -> str                           # header always written
def main(argv=None) -> int                                         # the CLI (decision 14)
```

- `ROLLING_SOURCE_COLUMNS` is imported lazily from `src.training.xjtu_rul` inside `build_export`. The route pays the sklearn import cost only when someone exports, never at app import.
- `build_export` orders episodes by feedback id, and rows by timestamp within an episode.
- **Empty export** (no qualifying episodes): `to_csv` writes the `BASE_COLUMNS` header only.

## API

All routes need a session. `alerts.router` and `model.router` are already mounted behind `get_current_user` (`src/api/app.py:175-177`).

| Method & path | Roles | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/alerts/{id}/close` | any | `AlertCloseRequest` | `AlertCloseResponse` `{alert, feedback, closed}` | 400, 403, 404 `unknown alert: {id}`, 422 |
| `PUT /api/alerts/{id}/feedback` | any (replace: recorder, admin, supervisor) | `AlertFeedbackIn` | `AlertFeedback` | 400, 403, 404, 422 |
| `GET /api/alerts/{id}/feedback` | any | — | `AlertFeedback \| null` | 404 `unknown alert: {id}` |
| `GET /api/model/feedback-accuracy?model_version=&period_start=&period_end=` | any | — | `FieldAccuracy` (dict, shape above) | 400 for an unparseable period |
| `GET /api/model/feedback/export?model_version=` | admin, supervisor | — | `text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="maintainiq-feedback-features-YYYYMMDD.csv"`, headers `X-Episode-Count`, `X-Row-Count` | 403 |

- **Route style:**
  - Alert routes are `async def`, call the sync service, and `await broadcast_quietly(...)` after commit, reusing `src/api/routes/work_orders.broadcast_quietly` (`work_orders.py:62-66`), as `alerts.py` already does (`:9,88`).
  - Service exceptions map through one `_feedback_http_error(exc)`: NotFound → 404, Forbidden → 403, else 400.
- **Roles:** `user: dict = Depends(require_role("admin", "supervisor", "operator"))` on the two writes (to receive the identity; same as the ack route, `alerts.py:60`). The export uses `Depends(require_role("admin", "supervisor"))`.
- **Why the export is restricted:** it is bulk feature data meant for model maintenance, which matches the elevated `model_performance` report (`reports.py:19`).
- **The model routes are sync `def`** like `model.py:29-47`. The export builds the CSV in memory and returns `Response(content=..., media_type="text/csv", headers=...)`, the same pattern as `reports.py:89-105`.

`src/api/schemas.py`:

```python
FeedbackOutcome = Literal["confirmed_failure", "maintenance_prevented", "false_alarm", "unknown"]
FeedbackCause = Literal["bearing_wear", "imbalance", "sensor_or_data_quality_issue", "unknown", "other"]

class AlertFeedback(BaseModel):
    id: int; alert_id: int; outcome: FeedbackOutcome; actual_cause: Optional[FeedbackCause] = None
    actual_failure_at: Optional[str] = None; notes: Optional[str] = None; work_order_id: Optional[int] = None
    recorded_by: int; recorded_by_name: Optional[str] = None; recorded_at: str
    updated_by: Optional[int] = None; updated_at: Optional[str] = None

class AlertFeedbackIn(BaseModel):          # body of PUT /feedback and POST /close
    outcome: FeedbackOutcome
    actual_cause: Optional[FeedbackCause] = None
    actual_failure_at: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=2000)
    work_order_id: Optional[int] = None

class AlertCloseRequest(AlertFeedbackIn): ...

class Alert(BaseModel):                    # existing, schemas.py:36-53, gains:
    prediction_id: Optional[int] = None
    reading_id: Optional[int] = None
    model_version: Optional[str] = None
    closed_by: Optional[int] = None
    feedback: Optional[AlertFeedback] = None   # list/detail routes only; None in broadcasts

class AlertCloseResponse(BaseModel):
    alert: Alert; feedback: AlertFeedback; closed: bool
```

- `AlertFeedback` is declared before `Alert`.
- The alert list (`alerts.py:37-44`) and machine detail (`machines.py:43-48`) build `Alert(**live.api_alert(row))`. `service.get_work_order` stores `live.api_alert(row)`.
- **Response models:** `/close` returns `AlertCloseResponse` and `PUT` returns `AlertFeedback`. `GET /feedback` is declared with `response_model=Optional[AlertFeedback]`.

## Realtime events

```json
{"type": "alert_closed", "machine_id": "m1",
 "alert": {"id": 2, "status": "resolved", "resolved_at": "2026-10-07T12:00:00+00:00", "closed_by": 1,
           "acknowledged_at": "2026-10-07T12:00:00+00:00", "prediction_id": 41, "...": "Alert"},
 "feedback": {"id": 3, "alert_id": 2, "outcome": "maintenance_prevented", "...": "AlertFeedback"},
 "at": "2026-10-07T12:00:00+00:00"}
{"type": "alert_feedback_recorded", "machine_id": "m1", "alert": {"...": "Alert"},
 "feedback": {"...": "AlertFeedback"}, "at": "..."}
```

- `at` is the service's `now`.
- They are sent from the route handlers after commit. A broadcast failure is logged and does not fail the request.
- The closing user's own dashboard gets the toast too, as with work-order events.
- **Devices:** `alert_closed` reaches the MQTT alert topic only as a translated §5 `alert_resolved` payload (decision 16). `alert_feedback_recorded` never reaches it.

## Notification behaviour

| Trigger | Who is emailed | Notes |
|---|---|---|
| Close (`alert_closed`) | nobody | Not a paging event. Also stops the paging ladder for that alert (status leaves `open`). |
| Feedback recorded / edited | nobody | Realtime only. |
| A new abnormal reading after a close | admins + supervisors (level 0, unchanged) | It is a new alert (`alert_created`) through `fan_out`. |

- The NotificationBell is unchanged. Its `PAGING_EVENT_TYPES` (`frontend/src/layout/AppShell.tsx:80-85`) doesn't list the new events, so they never light the dot.

## Frontend UX

**Types (`frontend/src/api/types.ts`):**
- New `FeedbackOutcome`, `FeedbackCause`, `AlertFeedback`, `AlertFeedbackIn` and `AlertCloseResponse`.
- `FieldAccuracy`, mirroring the response shape, with `FieldAccuracyByVersion`, `LeadTimeStats`, `RulErrorStats` and `OfflineMetrics`.
- `RealWorldKpi`.
- **`Alert` (`:29-49`) gains optional fields**, to avoid churning the six files with alert literals: `prediction_id?: number | null`, `reading_id?: number | null`, `model_version?: string | null`, `closed_by?: number | null` and `feedback?: AlertFeedback | null`.
- **`PredictionKpi` (`:91-100`)** gains `real_world?: RealWorldKpi` and `root_cause_accuracy?: { status: string; accuracy?: number; labelled_count?: number; reason?: string }`.
- **`LiveEvent` (`:240`)** gains:

  ```ts
  export type AlertFeedbackEventType = 'alert_closed' | 'alert_feedback_recorded'
  export interface AlertFeedbackLiveEvent { type: AlertFeedbackEventType; machine_id: string; alert: Alert; feedback: AlertFeedback; at: string }
  export type LiveEvent = AlertLiveEvent | DeviceLiveEvent | WorkOrderLiveEvent | AlertPagedLiveEvent | AlertFeedbackLiveEvent
  export function isFeedbackEvent(e: LiveEvent): e is AlertFeedbackLiveEvent
  ```

  `KNOWN_EVENT_TYPES` (`:245-…`) gains both types.

**Client (`frontend/src/api/client.ts`, after `acknowledgeAlert` :110):**
- `closeAlert(id, payload: AlertFeedbackIn)` → `POST /alerts/{id}/close`
- `submitAlertFeedback(id, payload)` → `PUT /alerts/{id}/feedback`
- `getAlertFeedback(id)`
- `getFieldAccuracy(opts?: { modelVersion?: string })` (after `getModelTelemetry`, :168-170)
- `downloadFeedbackExport(): Promise<{ filename; content; episodeCount }>`, the raw-fetch pattern of `downloadReport` (:211-219), reading `X-Episode-Count`.

**Styles (`components/healthStyles.ts`), reusing the intensity tokens:**
- `feedbackOutcomeLabel(o)`: `Confirmed failure`, `Prevented by maintenance`, `False alarm`, `Unknown`.
- `feedbackOutcomeTone(o)`: `confirmed_failure → critical`, `maintenance_prevented → healthy`, `false_alarm → degrading`, `unknown → unknown`.
- `causeLabel(c)`: `Bearing wear`, `Imbalance`, `Sensor / data quality issue`, `Unknown`, `Other`; unknown values pass through.

**`components/AlertCloseForm.tsx` (new) and `components/AlertCloseDialog.tsx` (new):**
- **`AlertCloseForm`** props: `{ alert: Alert; workOrderId?: number | null; initialNotes?: string; onSaved(result: { alert: Alert; feedback: AlertFeedback; closed: boolean }); onCancel(); cancelLabel?: string }`.
- **Mode** comes from the alert:
  - `alert.status === 'open'` → "Close alert" (submit label "Close alert", calls `closeAlert`);
  - resolved with no feedback → "Record outcome" (`submitAlertFeedback`);
  - existing feedback → "Edit outcome", pre-filled from `alert.feedback`. If the user is an operator and not `feedback.recorded_by`, the form is read-only with "Recorded by {name} — only they, an admin or a supervisor can change it."
- **Fields:**
  - **Outcome:** a radio group (`role="radiogroup"`, legend "What actually happened?"), four options with a one-line description each, none preselected (decision 18).
  - **Actual cause:** a select with "Not specified" plus the five causes, and the hint "Model said: {causeLabel(alert.probable_cause)}".
  - **Failure time:** `datetime-local`, shown only for `confirmed_failure`, `max` = now, sent with `localInputToIso` (`lib/datetime.ts:6`).
  - **Notes:** `Textarea`, 2000 characters.
  - **Work order:** a read-only line "Linked to work order #n" when `workOrderId ?? alert.active_work_order_id` is set; that value is sent.
- **Info lines:**
  - for an open alert: "Closing resolves this alert now. If the machine still reads abnormal, the next reading opens a new alert.";
  - when `active_work_order_id` is set: "Work order #n stays open — complete or cancel it separately."
- **Submit:** disabled until an outcome is chosen. Server errors show inline (`role="alert"`). On success it calls `onSaved`.
- **`AlertCloseDialog`** props: `{ open; alert: Alert | null; workOrderId?; onClose; onSaved }`. A modal (`role="dialog"`, `aria-modal`, labelled by its heading) using `useDialogFocus` (`lib/useDialogFocus.ts:8`), wrapping `AlertCloseForm`.

**`pages/AlertsPage.tsx`:**
- **Actions cell (`:165-197`)**, after the work-order control:
  - **Open alert:** a "Close…" button.
  - **Resolved, no feedback:** a "Record outcome" button.
  - **Has feedback:** a `Badge` with `feedbackOutcomeTone` and the outcome label (title: "{cause} · recorded by {name}"). The badge is a button that opens the dialog in edit mode when the user may edit, otherwise a plain badge.
  - All of them call `e.stopPropagation()`, because the row navigates on click (`:139`).
- **The dialog** is held in page state next to `workOrderAlert` (`:24`) and rendered beside `CreateWorkOrderDialog` (`:205-210`).
- **After a save:** `toast.success` ("Alert #n closed" / "Outcome recorded"), then `load(status)`.

**`components/AlertsPanel.tsx`** (the compact list on Machine Detail): each row shows the outcome badge when `feedback` is set. No buttons, since selection drives the actions.

**`components/MachineDetail.tsx` and `pages/MachineDetailPage.tsx`:**
- **New optional prop** `onRecordOutcome?(alert: Alert)`. When passed, a button renders next to `WorkOrderButton` (`MachineDetail.tsx:210-216`), driven by `selectedAlert`:

  | Selection | Button |
  |---|---|
  | none | disabled "Close / record outcome", `title="Select an alert first"` |
  | open alert | "Close alert" |
  | resolved alert, no feedback | "Record outcome" |
  | alert with feedback | "Edit outcome" (disabled for an operator who isn't the recorder) |
- **`MachineDetailPage`** owns `AlertCloseDialog`, for the same remount reason as the work-order dialog (`MachineDetailPage.tsx:13-17,40-51`). After a save it toasts and bumps `reloadCount`.

**`components/WorkOrderDrawer.tsx`:**
- **After completion:** after a successful `completeWorkOrder` (`handleComplete`, `:151-163`), if `detail.alert` exists and either has no feedback, or has feedback the user may edit, the drawer sets `pending = 'outcome'`.
- **The outcome step:** the action area renders `<AlertCloseForm alert={detail.alert} workOrderId={detail.id} initialNotes={notes} cancelLabel="Skip" …/>` under the heading "Record what happened". `detail` is re-fetched first, so the alert is current.
- **Saving or skipping:**
  - **Save:** clears `pending`, toasts and re-fetches.
  - **Skip:** clears `pending`.
- **`run()` (`:113-126`)** gains an optional `after?: (updated) => void` so Complete can chain the step without duplicating the error handling.
- **Linked alert line:** the header's linked-alert line shows the outcome badge when feedback exists.

**`pages/ModelPage.tsx`:**
- **New card "Field accuracy (operator feedback)"** below Telemetry (`:63-75`), loaded by its own effect: `api.getFieldAccuracy()`.
  - It is not added to the existing `Promise.all` (`:22`), so a failure shows inline in this card rather than blanking the page.
  - It re-fetches when `lastEvent` is a feedback event (`isFeedbackEvent`).
- **When available:**
  - `MetricCard`s:
    - Labelled alerts;
    - Precision (`pct`);
    - False-alarm rate;
    - Median lead time (min), with sub-text "{within}/{count} within {horizon} min";
    - RUL MAE (min, point estimates), with sub-text "{n} censored lower bounds excluded";
    - Root-cause accuracy.
  - A line "Offline benchmark (per snapshot): precision {x} · recall {y} (model {v})", or "No offline benchmark recorded for the active model".
  - A compact table "By model version" (Version, Labelled, Precision, False-alarm rate, Median lead, RUL MAE).
  - A footnote: "Missed failures can't be measured from alert feedback."
- **When not applicable:** "No labelled alerts yet — close alerts with an outcome to start measuring field accuracy."
- **Export button** "Export retraining CSV", for admins and supervisors only (`useAuth`):
  - disabled with `title="No confirmed failures with a failure time yet"` when `exportable_episode_count === 0`;
  - on click, `downloadFeedbackExport()` → Blob → anchor download → `toast.success("Exported {n} episode(s)")`.
  - Help text below it: "Merge with the XJTU-SY feature table and retrain manually — see README."

**Realtime (`realtime/LiveEventsProvider.tsx`):**
- `describe()` (`:31-56`) adds:
  - `alert_closed` → `{machine_id}: alert closed — {feedbackOutcomeLabel(outcome)}`
  - `alert_feedback_recorded` → `{machine_id}: outcome recorded — {label}`
- `toastKind()` (`:73-77`): `alert_closed` → `success`, `alert_feedback_recorded` → `info`.

**Pages that re-fetch on any `lastEvent`** (AlertsPage `:40-42`) pick the events up unchanged. `MachineDetailPage` matches them by `machine_id` (`:23-25`), which both carry.

**Routes and nav:** none new. The Model page (`App.tsx:51`, nav `AppShell.tsx:55`) is already open to every role.

**MSW (`frontend/src/test/server.ts`, `fixtures.ts`):**
- Default handlers:
  - `POST /api/alerts/:id/close` → `{alert: {...clone, status:'resolved', resolved_at, closed_by: 1, feedback}, feedback, closed: alert.status === 'open'}`;
  - `PUT /api/alerts/:id/feedback` → a cloned `alertFeedback` with `alert_id` patched;
  - `GET /api/alerts/:id/feedback` → `null`;
  - `GET /api/model/feedback-accuracy` → `structuredClone(fieldAccuracy)`;
  - `GET /api/model/feedback/export` → a CSV body with `Content-Disposition` and `X-Episode-Count: 1`.
  - None mutates shared fixtures (unlike the ack handler at `server.ts:61-69`).
- Fixtures:
  - new `alertFeedback`;
  - `fieldAccuracy` (available, two versions, `offline` set);
  - `fieldAccuracyNotApplicable`;
  - `resolvedAlertWithFeedback`.
  - `openAlerts` (`:79-97`) is unchanged; the new fields are optional.

## Environment variables

None (decision 19). The README env table is unchanged.

## Test plan

### Backend (pytest)

**`tests/storage/test_migrations.py`:**
1. `_v1_db()` (`:167-184`), `_v2_db()` (`:278-296`) and `_v3_db()` (`:391-408`) also `DROP TABLE IF EXISTS alert_feedback`.
2. A new `_PRE_V5_ALERTS` (today's alerts DDL without the four columns) and `_v4_db(conn=None)`: run steps 1–4, drop `alert_feedback`, rebuild `alerts` in the pre-v5 shape, stamp versions 1–4.
3. **v4→v5:**
   - `alerts` rows are preserved (including `page_level`/`acknowledged_by`) and read NULL for the four new columns;
   - `maintenance_records`, `users`, `notifications`, `device_incidents`, `work_orders` and `work_order_events` rows are preserved;
   - `alert_feedback` and `idx_alert_feedback_outcome` exist;
   - versions are `[1..5]` (`5 in versions`);
   - a re-run is a no-op.
4. The v3→v4 test (`:411-446`) still passes, and its upgraded DB has the v5 columns too.
5. **Fresh DB constraints:**
   - the outcome CHECK rejects `'closed'`;
   - the actual_cause CHECK rejects `'gremlins'` and accepts NULL and `'other'`;
   - `actual_failure_at` with `outcome='false_alarm'` is rejected;
   - a second row for the same `alert_id` violates UNIQUE.
6. The legacy IMS upgrade (`:136-162`) reaches `LATEST`, and its preserved alerts gain `prediction_id` (NULL).
7. `ensure_current_schema` upgrades a v4 file to 5 (`prediction_id` in `alerts`).

**`tests/alerts/test_live_links.py` (new):**
1. `apply_reading(..., prediction_id=7, reading_id=3, model_version="v1")` on m2 inserts those values, and the create dict carries them plus `closed_by: None`.
2. An escalation on the same episode with different ids keeps the originals and still updates severity and cause.
3. A call without the keywords (the demo/batch style) stores NULLs.
4. `_ALERT_COLUMNS` round-trips the four columns.
5. `api_alert(row)` parses `feedback_json` into a dict, or `None` when there is no feedback.

**`tests/prediction/test_pipeline.py` (extend):**
1. `handle_prediction(conn, result, ..., prediction_id=11, reading_id=5)` opens an alert with those ids and `model_version == result["model_version"]`.
2. The existing tests pass unmodified, so the defaults keep today's behaviour.

**Caller threading:**
- `tests/api/test_rul_persistence.py`: `POST /api/predictions/rul` with an abnormal fake result → the opened alert's `prediction_id` equals the new `predictions.id`, and `reading_id` is NULL.
- `tests/ingestion/test_replay_service.py`: the hooks at `:145` and `:194` gain `**_`. A new test checks that the injected hook receives `prediction_id` (= the persisted row) and `reading_id = row["id"]`. `test_default_fan_out_opens_a_real_alert` (`:159-…`) also asserts `alert["reading_id"]` and `alert["prediction_id"]`.
- `tests/telemetry/test_ingest.py`: `default_fan_out` (`:67`) and the lambda at `:282` gain `**_`/`**k`. A new test checks that the hook gets the reading id of the stored reading and the prediction id. `tests/telemetry/test_mqtt_service.py:278`'s `lambda *a` becomes `lambda *a, **k`.

**`tests/feedback/test_service.py` (new; seeded conftest DB, explicit `now=`, actors built from the demo users):**
1. `close_alert` on open alert 2 (m1) by an operator:
   - status is `resolved`, `resolved_at = now`, `closed_by = operator`;
   - `acknowledged_at`/`acknowledged_by` are set;
   - one feedback row exists with `recorded_by = operator`;
   - it returns `closed = True`.
2. `close_alert` on alert 2 when it is already acknowledged keeps the original acknowledgement.
3. `close_alert` on resolved alert 1 returns `closed = False`. The status, `resolved_at` and `closed_by` (NULL) are unchanged, and the feedback is created.
4. After a close, `apply_reading(conn, "m1", "critical", ...)` opens a **new** alert id (status open). The closed alert stays resolved.
5. After a close, the paging candidate query returns nothing for that alert: `AlertPager.tick` at `created_at + 60 min` with a fake notify sends nothing.
6. `record_feedback` on open alert 2 leaves the status `open` and returns `created = True`. A second call by the same operator updates it (`updated_by`/`updated_at` set, `recorded_*` unchanged, `created = False`).
7. **Permissions:**
   - another operator replacing it gets `FeedbackForbidden`;
   - a supervisor and an admin can replace it;
   - `close_alert` by another operator on an open alert whose feedback exists by someone else raises `FeedbackForbidden`, and the alert **stays open** (rollback).
8. **Validation (FeedbackError):**
   - `actual_failure_at` with `false_alarm`;
   - an unparseable `actual_failure_at`;
   - an `actual_failure_at` 10 min in the future (5 min in the future is accepted);
   - an unknown `work_order_id`;
   - a work order on m2 for an m1 alert;
   - a work order whose `alert_id` is another alert.
9. `actual_failure_at` given as a `Z` value is stored as `+00:00` UTC. A naive value is treated as UTC.
10. An unknown alert raises `FeedbackNotFound` for `close_alert`, `record_feedback` and `get_feedback`.
11. **Lock:** while the test holds `live._TRANSITION_LOCK`, `close_alert` in a thread blocks until the lock is released.
12. **Race:** two threads closing alert 2 at once give exactly one `closed = True`, one feedback row and one `closed_by`.

**`tests/feedback/test_accuracy.py` (new; feedback rows, predictions and alerts inserted directly):**
1. With no feedback, or only `unknown` feedback, `status` is `not_applicable` with reason `no labelled alerts yet`, every key is present and `feedback_count` counts the unknown rows.
2. On a DB without `alert_feedback` (dropped), the result is `not_applicable` and nothing raises.
3. **Precision and false-alarm rate:** 2 confirmed, 1 prevented and 1 false alarm → 0.75 / 0.25.
4. **Demo excluded:** a `source='demo'` false alarm doesn't change the numbers.
5. **Lead time:**
   - failures 70 and 120 min after `opened_at` → mean and median 95, both within the horizon;
   - a failure 130 min after → `early`;
   - a failure before `opened_at` → `late`;
   - a prediction with `prognostic_horizon_minutes = 60` uses 60.
6. **RUL censoring:**
   - a `point_estimate` predicting 100 against a lead of 120 → MAE 20;
   - a `lower_bound` predicting 120 against a lead of 300 → excluded from the MAE, `lower_bound_count` 1, `respected` 1;
   - a `lower_bound` against a lead of 50 → not respected.
7. **Root cause:**
   - a match counts;
   - `other` counts as a miss;
   - `unknown` and NULL `actual_cause` are excluded;
   - a false alarm with `sensor_or_data_quality_issue` counts.
8. **`by_model_version`:** two versions plus NULL. The groups are correct, with the null group last. The `model_version=` filter restricts every number.
9. The period filter on `created_at` excludes older alerts, whatever their `opened_at`.
10. **`offline`:**
    - taken from the active registry row's `failure_detection`;
    - `null` for `metrics_json` NULL, `"{}"` or broken JSON;
    - the `model_version` filter picks that version's row.
11. `DEFAULT_HORIZON_MINUTES == src.training.xjtu_rul.PROGNOSTIC_HORIZON_MINUTES`.
12. `exportable_episode_count` matches `export.count_episodes`.

**`tests/feedback/test_export.py` (new; live readings with `dataset='live_mqtt'` and real feature dicts from `extract_snapshot_features` on random signals):**
1. **One `confirmed_failure` with 5 live readings before the failure and 2 after:**
   - 5 rows;
   - `bearing_id = "m2:fb{id}"`;
   - `cycle` is 0..4;
   - `elapsed_minutes` starts at 0;
   - `rul_minutes` decreases to the true minutes-to-failure;
   - `source_file = "reading:{id}"`;
   - the columns start with `BASE_COLUMNS`.
2. **Round trip:** `to_csv` → `pd.read_csv` → `xjtu_rul.add_past_context(table)` succeeds (column requirements met, no training). `xjtu_rul.feature_columns(...)` excludes the base non-feature columns.
3. **Concatenation:** the export concatenates with a small XJTU-shaped frame (built with `build_feature_table`'s column order) without dtype errors, and the `bearing_id` sets stay disjoint.
4. **Excluded:** `false_alarm`, `maintenance_prevented`, `confirmed_failure` without `actual_failure_at`, demo alerts, and `xjtu_sy` readings.
5. **Episode start:**
   - a maintenance record before `opened_at` cuts the earlier readings;
   - one between `opened_at` and the failure does not;
   - an earlier confirmed failure on the same machine starts the next episode after it.
6. **Skipped:** rows with `features_json='{}'`, or missing `h_rms`, are counted in `skipped_rows`. An episode with no rows left is counted in `skipped_episodes`.
7. **Timestamp formats:** mixed `Z`/`+00:00` timestamps order and compare correctly.
8. **Empty export:** header only.
9. **CLI:**
   - `main(["--out", p, "--db", db])` writes the file and returns 0;
   - `--merge-with xjtu.csv --merged-out m.csv` writes the union of the columns with both bearing sets.

**`tests/api/test_alert_feedback_route.py` (new):**
1. Anonymous gets 401 on all five routes.
2. `POST /api/alerts/2/close` as an operator returns 200, `closed: true`, `alert.status == "resolved"`, `alert.closed_by` = the operator, and `feedback.outcome`. A follow-up `GET /api/alerts?status=open` no longer lists alert 2, and `GET /api/alerts` shows alert 2 with an embedded `feedback`.
3. Close on resolved alert 1 returns 200 with `closed: false`.
4. **Errors:**
   - 404 for an unknown alert;
   - 422 for a missing or invalid `outcome` and an invalid `actual_cause`;
   - 400 for `actual_failure_at` with `false_alarm`;
   - 403 for an operator editing a supervisor's feedback (PUT).
5. `PUT` then `GET /api/alerts/1/feedback` round-trips it. `GET` on an alert without feedback returns `null`.
6. `GET /api/machines/m1` alerts carry `feedback`, `prediction_id`, `reading_id`, `model_version` and `closed_by`. `GET /api/work-orders/{id}` for an order on alert 2 has `alert.feedback` after a close.
7. **Broadcasts** (spy on `manager.broadcast`):
   - a close of an open alert sends exactly one `alert_closed` (no `alert_acknowledged`), with `alert` and `feedback`;
   - a close of a resolved alert sends `alert_feedback_recorded`;
   - PUT sends `alert_feedback_recorded`;
   - a raising broadcast still returns 200.
8. No email is sent for close or feedback (patch `send_email` and assert it isn't called).

**`tests/api/test_model_endpoints.py` (extend):**
1. `GET /api/model/feedback-accuracy` on the seeded DB returns `not_applicable` with the full key set.
2. After closing alert 2 as `confirmed_failure`, it returns `available`, `labelled_count` 1 and precision 1.0.
3. A bad `period_start` returns 400.
4. `GET /api/model/feedback/export`:
   - operators get 403;
   - supervisors get 200 with `text/csv`, `Content-Disposition` `maintainiq-feedback-features-`, and an `X-Episode-Count` header;
   - the body's first line starts with `bearing_id,condition,cycle`.

**`tests/test_kpi.py` / `tests/kpi/` (extend):**
1. `prediction_kpis()` with no argument is unchanged (`:50-58` passes), plus `real_world.status == "not_applicable"`.
2. `prediction_kpis(conn)` with a labelled root cause gives `root_cause_accuracy.status == "available"` and the right accuracy. `real_world.precision` is set.
3. `summary(conn)["prediction"]` contains `real_world`, and `GET /api/kpis` still validates against `KpiSummary`.
4. On a DB without `alert_feedback`, the KPIs don't raise.

**`tests/reports/test_generators.py` (extend):**
1. `model_performance` has a `real_world` key (`not_applicable` on the seed). With feedback it reports precision.
2. The period filter is applied to it.
3. The markdown contains "## Real-World Accuracy" with either the table or the empty-state line. The existing assertions (`:127-160`) are unchanged.

**`tests/telemetry/test_protocol.py` / `test_mqtt_service.py` (extend):**
1. `device_alert_event` passes the three ALERT_EVENT_TYPES through unchanged.
2. `alert_closed` → a copy with `type == "alert_resolved"`, and the original dict is not mutated.
3. `alert_feedback_recorded`, `alert_acknowledged` and `work_order_updated` → `None`.
4. `build_alert_payload(device_alert_event(closed_event))` has `status: "resolved"`.
5. `_on_realtime_event` with an `alert_closed` publishes one retained message whose payload type is `alert_resolved`. With `alert_feedback_recorded` it publishes nothing.

**`tests/docs/*`:** `alert_feedback` is documented (enforced automatically).

### Frontend (vitest + RTL + MSW)

- **`components/healthStyles.test.ts`:** `feedbackOutcomeLabel`/`Tone` for all four outcomes plus an unknown value; `causeLabel`.
- **`components/AlertCloseForm.test.tsx` / `AlertCloseDialog.test.tsx` (new):**
  1. An open alert shows "Close alert", with submit disabled until an outcome is picked.
  2. No outcome is preselected, and the cause defaults to "Not specified" with the "Model said" hint.
  3. The failure-time input appears only for "Confirmed failure".
  4. Submit on an open alert POSTs `/api/alerts/2/close` with the exact body (spy), including `actual_failure_at` as UTC ISO, and calls `onSaved`.
  5. A resolved alert with no feedback PUTs `/feedback`.
  6. Existing feedback pre-fills the form ("Edit outcome").
  7. An operator viewing another user's feedback sees a read-only form with the explanation.
  8. A server 400 shows inline.
  9. The "Work order #n stays open" hint shows when `active_work_order_id` is set.
  10. Escape closes the dialog.
- **`pages/AlertsPage.test.tsx` (extend):**
  1. "Close…" opens the dialog without navigating (stopPropagation), and on success the list re-fetches and toasts.
  2. A resolved row without feedback shows "Record outcome".
  3. A row with `feedback` shows the outcome badge.
  4. The alert is passed to the dialog.
- **`components/AlertsPanel.test.tsx` (extend):** the outcome badge renders when `feedback` is set.
- **`components/MachineDetail.test.tsx` / `pages/MachineDetailPage.test.tsx` (extend):**
  1. The outcome button is disabled with no selection.
  2. With an open alert selected it reads "Close alert" and calls `onRecordOutcome`.
  3. The dialog survives a live event that remounts `MachineDetail`.
- **`components/WorkOrderDrawer.test.tsx` (extend):**
  1. Completing an order with a linked alert that has no feedback shows "Record what happened". The form's submit sends `work_order_id` equal to the order id.
  2. "Skip" hides it with no request.
  3. An order without `alert_id` goes straight back to the actions.
  4. A linked alert whose feedback the operator can't edit → the step isn't offered.
- **`pages/ModelPage.test.tsx` (extend):**
  1. The field-accuracy card renders the precision, lead time, RUL MAE, offline line and by-version table from the fixture.
  2. `not_applicable` shows the empty state, and the rest of the page still renders.
  3. A failing `/api/model/feedback-accuracy` shows an inline error, and Health and Telemetry still render.
  4. The export button is visible to admins and supervisors only, and hidden for operators.
  5. It is disabled when `exportable_episode_count` is 0.
  6. A click calls the export endpoint and triggers a download (stub `URL.createObjectURL`).
  7. The card re-fetches on an `alert_closed` event.
- **`realtime/LiveEventsProvider.test.tsx`:**
  - `alert_closed` → a success toast "m1: alert closed — Prevented by maintenance";
  - `alert_feedback_recorded` → an info toast;
  - both update `lastEvent`.
- **`layout/AppShell.test.tsx`:** the bell dot does not light for `alert_closed` or `alert_feedback_recorded`.
- **`api/client.test.ts`:** `closeAlert`/`submitAlertFeedback` send the right method and path. `downloadFeedbackExport` parses the filename and `X-Episode-Count`.
- **Build and lint:** `npm run build` (type-checks the extended union and the exhaustive `describe()` switch) and `npm run lint`.

## Docs to update

- **`docs/DATA_MODEL.md`:**
  - a `### \`alert_feedback\`` section (columns, CHECKs, UNIQUE, the edit rules, and "Added by migration 5");
  - `alerts` (`:191-215`) gains `prediction_id`, `reading_id`, `model_version` and `closed_by`, with notes: written on INSERT only and kept through escalation; `closed_by` set by a human close;
  - the ER diagram (`:15-35`) gains `alerts ||--o| alert_feedback : "outcome recorded as"`, `predictions ||--o{ alerts : "opened"`, `readings ||--o{ alerts : "opened at"` and `work_orders |o--o{ alert_feedback : "handled"`;
  - the diagram notes gain `alert_feedback.recorded_by`/`updated_by` and `alerts.closed_by` under the users-id convention;
  - Storage layout (`:475-505`) gains a migration 5 sentence;
  - the "Model heartbeat & telemetry contract" section (`:514-…`) gains `### \`GET /api/model/feedback-accuracy\`` and `### \`GET /api/model/feedback/export\``, with the shape and column definitions.
- **`README.md`:**
  - a new "#### Prediction feedback and field accuracy" subsection after "Work orders and paging" (`:232-266`), before "### Docker compose" (`:268`). It covers closing semantics (a new alert opens if the machine still reads abnormal), who may edit, the five routes, the two events and the device-LED note.
  - The "Train the real run-to-failure RUL model" section (`:345-…`) gains "Retraining with field feedback": `python -m src.feedback.export --out outputs/feedback_features.csv --merge-with outputs/xjtu_features.csv --merged-out outputs/xjtu_plus_feedback.csv`, then `python -m src.training.xjtu_rul --features-csv outputs/xjtu_plus_feedback.csv`. Caveats: only live MQTT episodes; review before promoting; domain shift.
- **`TODO.md`:** a checked item "Prediction feedback + field accuracy (design/2026-10-07-prediction-feedback-design.md)" after the work-orders item (`:52-60`).
- **`IMPLEMENTATION_PLAN.md`:** `## M9 — Prediction feedback` after M8 (`:59-66`).
- **`src/storage/migrations.py`:** the docstring gains a migration 5 paragraph.
- **`src/alerts/live.py`:** the docstring covers close and feedback under the lock.
- **`src/telemetry/protocol.py` / `mqtt_service.py`:** comments on `device_alert_event` and the LED reasoning.
- **`design/_integration_map.md`:** not edited. This doc supersedes §4 where they differ:
  - no `model_version` copy on `alert_feedback`, and `updated_by`/`updated_at` added;
  - the links keep the opening prediction;
  - close is idempotent, not 409;
  - the export uses live readings only;
  - the LED translation.
- **`PROJECT_CONTEXT.md`:** not touched.

## Risks

- **Label quality.** Accuracy is only as good as what people record. Mitigations: no preselected outcome; `unknown` excluded; `actual_failure_at` only for confirmed failures; edits attributed (`updated_by`). Small samples are shown with their counts, never as bare percentages.
- **Selection bias.** Only closed or labelled alerts are measured. Auto-resolved alerts nobody labels drop out, and missed failures are invisible (decision 11). The UI footnote and the `missed_failures` block say so.
- **Human close of a still-abnormal machine** opens a new alert on the next reading, and a new level-0 email. This is intended and documented in the dialog, but it can surprise users who expect "close" to silence a machine. The answer to that is a work order, not a close.
- **`avg_alert_resolution_hours` now includes human closures**, measured to the close time. That is semantically correct but shifts the KPI. Noted in the README.
- **Hook signature change** for `on_prediction` (decision 3). Any out-of-tree hook with a fixed positional signature would raise TypeError, which the callers catch and log as a fan-out failure. In tree, only the five test hooks listed are affected.
- **Device LED translation** sends an `alert_resolved` payload that no `apply_reading` produced (decision 16). The payload is accurate (status resolved, alert id), and firmware treats it like any resolve. It is covered by protocol and service tests.
- **Domain shift in retraining.** Live MQTT features come from different sensors and sample rates than XJTU-SY, and `condition` is blank. Merging can hurt the leave-one-bearing-out metrics. Retraining stays manual, with the evaluation report reviewed before the model is registered (README).
- **Export cost.** `build_export` loads every live reading of each episode's machine. That is fine for a manual, admin-only export at current scale. If it grows, add an index on `readings(machine_id, dataset, timestamp)` in a later migration.
- **`offline` is often null** because `POST /api/predictions/rul` re-registers the model without metrics (`rul_store.py:118-144`). This is the known register_active_model clobbering issue (backlog), and the UI shows "No offline benchmark recorded".
- **JSON1 dependency.** `API_ALERT_COLUMNS` uses `json_object`, which is built into the SQLite bundled with every supported CPython (3.38+ includes JSON functions by default). A test exercises it on the CI interpreter.
- **Lazy migration on a live request** (inherited from the work-orders design): migration 5 is additive and fast, and runs once under `ensure_current_schema`'s lock.

## Implementation notes (backend, 2026-10-07)

Where the backend as built refines or departs from the text above:

- **Acknowledge only on an actual close.** Decision 4 step 3 runs only when step 2 resolved the alert. A close on an already-resolved alert records feedback and touches nothing else on the alert (test plan item 3 requires `resolved_at`/`closed_by` unchanged; the acknowledgement is left alone for the same reason).
- **The `/close` response embeds the feedback in `alert` too** (`AlertCloseResponse.alert.feedback`), matching the MSW handler in "Frontend UX". Broadcast alerts still carry no `feedback`; the event's sibling `feedback` key does.
- **Accuracy helpers.** Besides `real_world_kpi` / `root_cause_kpi`, `accuracy.py` exposes `compact_kpi(result)` and `root_cause_block(result)` so `prediction_kpis(conn)` computes the field numbers once, `not_applicable_kpi()` for callers without a connection, and `unfiltered_not_applicable(...)`. Every `model_performance` report period is free text (`ReportCreateRequest`), so an unparseable bound gives a `real_world` block that is `not_applicable` with the parse error as `reason` instead of failing the report; the API route answers 400 as specified.
- **Validation is duplicated in the service.** Outcome, cause and the 2000-character notes limit are also checked by the service (`FeedbackError`, 400) so non-HTTP callers get the same rules; over HTTP Pydantic answers 422 first, as specified.
- **Episode start, earlier failure.** "Any earlier `confirmed_failure` `actual_failure_at` on the same machine" is read as *earlier than this episode's own failure time* (any other feedback row, demo or not, since the trajectory reset is physical).
- **CLI.** `--merge-with` without `--merged-out` is a usage error. The CLI runs `ensure_current_schema` on `--db` first, so a v4 file is upgraded rather than reported empty.
- **README retraining command** trains a *candidate* (`--no-register --artifact outputs/xjtu_rul_candidate.joblib --report outputs/xjtu_rul_candidate_evaluation.json`). The bare `python -m src.training.xjtu_rul --features-csv …` in decision 14 would overwrite the live `models/xjtu_rul_model.joblib` and register it as active, which is the promotion the Non-goals keep manual.
- **`table_exists`** now lives in `src.storage.db`; `kpi._table_exists` is an alias of it.
- **Test hooks.** Besides the hooks listed in the test plan, `tests/telemetry/test_ingest.py`'s `fake_handle` (which replaces `pipeline.handle_prediction`) and its `broken` / `fan_out` helpers gained the new keywords.
- **`.env.example` / README env table:** unchanged (decision 19).
