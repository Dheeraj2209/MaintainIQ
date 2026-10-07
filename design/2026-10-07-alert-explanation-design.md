# "Why this alert?" (Alert Explanations) — Design

Status: Approved (decisions below are final for implementation)
Date: 2026-10-07
Source: user requirement (verbatim) ""Why this alert?": explain each alert with the triggering readings, key factors, probable cause and similar past incidents."; `design/_integration_map.md` §5 (feature 4, migration 6) and §7 (cross-cutting checklist); builds on `design/2026-10-07-prediction-feedback-design.md` (`alerts.prediction_id` / `reading_id` / `model_version`, `alert_feedback`), `design/2026-10-07-work-orders-escalation-design.md` (`pipeline.fan_out`, `work_orders`, `useDialogFocus`, the `work-orders/:id?` drawer route) and `design/2026-08-04-alert-acknowledge-design.md` (template).

## Problem

An alert says *that* a machine is unhealthy, not *why*:

- **The evidence is not kept.** An alert row has a severity, a health state, a `probable_cause` label and a one-line message (`src/storage/db.py:134-157`). Since migration 5 it also points at the opening reading and prediction (`alerts.reading_id` / `prediction_id`). There is no record of which readings, which features or which rule produced it.
- **Escalation destroys the evidence that is there.** The severity-escalate UPDATE overwrites `probable_cause`, `health_state` and `message` (`src/alerts/live.py:139-149`), and the links stay on the opening prediction by design (feedback design, decision 3). The escalating reading is never linked anywhere.
- **The model's context is in memory only.** `RealTimeRULPredictor` keeps each machine's rolling and commissioning-baseline history in process dicts (`src/prediction/rul_realtime.py:62-74`). After a restart, nothing can reproduce the feature vector the model scored.
- **The cause is a label, not an argument.** `classify_probable_cause` returns one string (`src/root_cause/rule_based.py:24-45`). The thresholds it used and the values it saw are lost.
- **The past is invisible from the alert.** `alert_feedback` (what really happened), `work_orders` and `maintenance_records.alert_id` (what was done) exist, but nothing brings up the earlier alerts that look like this one.

## Goal

1. Every alert has a **"Why?"** view, available to any signed-in user, with five sections:
   - **Triggering readings:** a window of the machine's vibration readings, with the trigger marked.
   - **Key factors:** which vibration features moved furthest from the machine's own baseline, weighted by how much the model relies on them. Each shows its direction and whether it is outside the training range.
   - **Probable cause:** the label plus the rule trace that produced it, always worded as *probable*.
   - **Model prediction:** failure probability, RUL with its interval, and the out-of-distribution (OOD) flag and warnings. OOD is always surfaced.
   - **Similar past incidents:** earlier alerts with the same probable cause or on the same machine, ranked by similarity, with their recorded outcome, actual cause, work orders and maintenance.
2. The evidence is **snapshotted at alert creation and at every severity escalation**, so it survives restarts and the escalation overwrite.
3. `GET /api/alerts/{id}/explanation` returns the latest snapshot plus similar incidents computed at request time. For alerts with no snapshot (legacy, batch, pre-migration-6) it computes everything on the fly and flags `source: "reconstructed"`.
4. Demo alerts are labelled **synthetic**. Everything degrades gracefully when there is no predictor loaded, no feature vector, no history or no stored prediction.
5. The UI is a drawer (`AlertExplanationPanel`) opened from the Alerts page, the dashboard `AlertsPanel`, and Machine Detail, plus a deep-linkable route `/alerts/:id`. Feature 5 (mobile) reuses the route and the panel's content component.

## Non-goals

- **Model-level explainability** such as SHAP, LIME, permutation importance per prediction, or counterfactuals. Key factors use global `feature_importances_` multiplied by per-machine deviation. That is a documented heuristic, not a causal attribution (decision 6).
- **A new root-cause model, or new rules.** `classify_probable_cause` and its threshold stay exactly as they are; the new function only explains them (decision 8).
- **Editing the forbidden ML modules.** `src/prediction/rul_realtime.py`, `src/training/**`, `models/**`, `src/ingestion/xjtu_sy.py` and `src/features/**` are read or imported only.
- **Temperature.** No temperature column exists or is shown (`tests/docs/test_data_model_doc.py` forbids `temperature_c`).
- **Persisting reconstructed explanations.** `GET` is side-effect free (decision 4).
- **New realtime event types or emails.** The explanation is pulled on demand (see "Realtime events" and "Notification behaviour").
- **A "Why?" action on toasts or in emails.** Feature 5's push notifications deep-link to `/m/alerts/:id`. Toast actions can be added later without backend change.
- **Fixing `kpi._vibration_severity`.** It reads the non-existent `vibration_h_high_band_energy_ratio` key (`src/kpi/calculations.py:86`), and so does the Machine Detail metric list (`frontend/src/components/MachineDetail.tsx:31`). Neither is on this feature's path: key factors read `features_json` short keys directly. Logged in TODO.md as a follow-up instead (see "Docs to update").
- **Changing `alert_escalated` semantics, `ALERT_EVENT_TYPES`, the paging ladder or the feedback vocabulary.**

## Decisions

1. **A separate `alert_explanations` table, many rows per alert. This is migration 6.**
   - The map offered two options (§5): ALTER columns onto `alerts`, or a table. We use the table because an alert can be explained more than once: one `created` row plus up to two `escalated` rows (low→medium→high, `SEVERITY_RANK` in `src/alerts/generation.py`). Columns on `alerts` could hold only the latest, and would widen every alert SELECT (`live._ALERT_COLUMNS`, `API_ALERT_COLUMNS`) with a large blob.
   - `kind ∈ created | escalated`. A partial UNIQUE index allows **at most one `created` row per alert**, so a retried fan-out cannot double it. `escalated` rows are not unique: each escalation is a distinct event.
   - `reading_id` / `prediction_id` on the row are the reading and prediction behind **that snapshot**. For an `escalated` row they are the escalating reading, which `alerts.reading_id` deliberately doesn't hold (feedback design, decision 3).
   - The body is one `explanation_json` TEXT blob, versioned inside by `explanation_version: 1`. The sections are nested lists of varying length; normalising them would add four tables for data that is only ever read whole.
   - **Similar incidents are not stored in the snapshot.** They change as feedback and work orders are recorded, so they are always computed at request time.

2. **The snapshot is written in `pipeline.fan_out`, right after `apply_reading` and before paging and broadcast.**
   - **Why `fan_out`, not `handle_prediction`:** `fan_out` (`src/prediction/pipeline.py:99-149`) is the one function both `handle_prediction` (`:96`) and the demo routes (`src/api/routes/demo.py:55,93`) call, so demo alerts get snapshots without a second hook.
   - `fan_out` gains keyword-only `features=None`, `reading_id=None` and `prediction_id=None`. `handle_prediction` forwards its `features`, `reading_id` and `prediction_id` (`:81-96`). `demo.simulate_fault` passes `reading_id=prediction["reading_id"]` (`src/prediction/live.py:98,105`). Demo still passes nothing new to `apply_reading`, so feature 2's "demo stores NULL links" rule holds.
   - **Only for `alert_created` and `alert_escalated`.** No snapshot for a resolve or a no-op.
   - **Before paging and broadcast**, so a user who clicks "Why?" from the toast already finds the snapshot.
   - **Wrapped in `try/except Exception` + `logger.exception`**, like the email and broadcast (`:121-147`). A failure never undoes the committed alert, and never stops the page or the broadcast.
   - **Never under `live._TRANSITION_LOCK`.** `apply_reading` has released it by the time `fan_out` runs. The snapshot only inserts into its own table and never reads-then-writes an alert.
   - `prediction` is the result dict `handle_prediction` already builds (`:95`), which still carries `outside_training_features` and `warnings`. The `predictions` table doesn't store `outside_training_features`, so the snapshot is the only place it is kept.

3. **The predictor is never loaded by this feature.**
   - `explain.loaded_predictor()` lazily imports `src.api.routes.predictions._cached_predictor` (the same lazy-import pattern as `src/telemetry/mqtt_service.py:448-454` and `src/api/routes/ingestion.py:21-27`). It returns the cached instance only if `_cached_predictor.cache_info().currsize > 0`, else `None`.
   - **Why:** loading the artifact (`joblib.load`, three 350-tree ExtraTrees) costs seconds and raises `FileNotFoundError` when no model is trained (`rul_realtime.py:30-34`). The explanation route must stay fast, must never 503, and must be deterministic in tests.
   - In practice, every snapshot written by the replay, MQTT or prediction route paths has the predictor loaded, because it just predicted. Only demo snapshots and reconstructions after a restart run without it, and they fall back to unweighted factors (decision 6).
   - Callers (tests, feature 5) may pass `predictor=` explicitly to every builder.

4. **`GET /api/alerts/{id}/explanation` serves the latest snapshot, else reconstructs, and never writes.**
   - "Latest" means the row with the highest `id` for the alert. An escalated alert therefore shows the escalation evidence, matching its current `probable_cause` and severity.
   - The response also lists every snapshot's `{kind, created_at}` (`snapshots`), so the UI can say "escalated at 14:20 (opened 13:05)".
   - **No snapshot**, or one whose `explanation_json` fails to parse or has an unknown `explanation_version`, means the explanation is computed live from the DB with `source: "reconstructed"` and a note.
   - Reconstruction uses `alerts.reading_id`, else the prediction's reading, else the nearest reading (decision 5). For an escalated legacy alert, that is the *opening* evidence, and a note says the escalation evidence wasn't captured.
   - **Reconstructions are not persisted.** A GET must not write. Also, a "nearest timestamp" guess frozen into a table would then pose as captured evidence.

5. **Triggering readings: up to 30 readings ending at the trigger, vibration channels only.**
   - **Locating the trigger, in order:**
     1. The snapshot's `reading_id`. `locate: "reading_id"`.
     2. A snapshot with `features` but no `reading_id` (an escalation via the predictions route): the unsaved trigger below, `locate: "snapshot_features"`. This comes before `alerts.reading_id`, which still holds the *opening* reading after an escalation.
     3. `alerts.reading_id`. `locate: "reading_id"`.
     4. The `reading_id` of the alert's prediction: `prediction_id`, else `alerts.prediction_id`, else the machine's latest prediction with `timestamp <= opened_at` (the row the prediction block shows). `locate: "prediction_reading"`. Replay predictions carry the exact reading even for pre-v5 alerts, and their wall-clock timestamps line up with `opened_at` where backfilled readings (stamped from `BACKFILL_EPOCH`) do not.
     5. The latest reading of the machine with `timestamp <= opened_at`, else the earliest after it. `locate: "nearest_timestamp"`, with a note. This fallback is needed for `POST /api/predictions/rul`, which stores no reading (`predictions.py:67`), and for every legacy alert.
     6. No readings at all: `locate: "none"`, empty list, and a note.
   - **Reading-less snapshots get an unsaved trigger.** When a snapshot has `features` but no `reading_id` (the predictions route), the in-memory feature vector becomes the trigger point (`reading_id: null`, `timestamp` = the alert's `at`). Its promoted channels are mapped with the same table as ingest (`src/telemetry/ingest.py:81-88`), and it is appended after the stored readings with `timestamp <= at`. The chart then shows what the model saw, not an unrelated stored row.
   - **Window size:** `TRIGGER_WINDOW_READINGS = 30` readings ordered by `(timestamp, id)`, ending at and including the trigger. Readings after the trigger are not shown: a snapshot can't have them, and including them only in reconstructions would make the two sources look different.
   - **Channels:** the six promoted vibration columns `vibration_h_rms`, `vibration_h_kurtosis`, `vibration_v_rms`, `vibration_v_kurtosis`, `cross_axis_rms_ratio` and `cross_axis_correlation`. These are present for every dataset including demo, whose `features_json` is `'{}'` (`src/prediction/live.py:94`). No temperature, and no `speed_rpm`/`load_kn` in the chart (they appear as operating-condition context under key factors).

6. **Key factors: deviation from the machine's own baseline × model importance; operating conditions excluded.**
   - **Candidates** are the 20 `ROLLING_SOURCE_COLUMNS` (`src/training/xjtu_rul.py:33-40`): `{h,v,m}_{rms,peak,kurtosis,envelope_rms,envelope_kurtosis,spectral_entropy}` plus `{h,v}_envelope_energy_100_200_hz_ratio`.
     - `speed_rpm` and `load_kn` are never ranked. They are operating conditions that the classifier uses (`classifier_feature_columns`, `:101-109`), not symptoms.
     - The cross-axis pair has no baseline ratio in `add_past_context`, so it is not ranked either; it is shown in the chart.
   - **Method `model_context`.** This is the preferred method. It needs the trigger row and at least `BASELINE_WINDOW` (20, `:42`) earlier readings with every `ROLLING_SOURCE_COLUMNS` key in `features_json`.
     - Build a frame from the machine's first `BASELINE_WINDOW` readings by cycle (its commissioning baseline, mirroring the predictor's retained baseline at `rul_realtime.py:137-146`) plus the last `max(ROLLING_WINDOWS)` = 60 readings up to the trigger. De-duplicate by cycle.
     - The frame has `bearing_id = machine_id`, `cycle`, `speed_rpm`, `load_kn` and the `features_json` keys.
     - Run the imported `xjtu_rul.add_past_context` (`:56-91`) and take the trigger row. Its `{c}_baseline_ratio` is exactly the deviation the model saw, and every derived feature can be checked against the training bounds.
   - **Method `promoted_columns` (fallback).** It is used when `features_json` is empty or incomplete (demo, partial rows) and at least 5 earlier readings exist.
     - Candidates are `h_rms`, `h_kurtosis`, `v_rms` and `v_kurtosis`, read from the promoted columns.
     - The baseline is the median of the machine's first `min(20, n)` readings, the same definition as `add_past_context`'s expanding median frozen at `BASELINE_WINDOW`.
     - No bounds check is possible, so `outside_training_bounds: null`.
   - **Not enough history:** fewer than 5 earlier readings gives `status: "insufficient_history"` and `factors: []`. No readings gives `status: "no_readings"`.
   - **Deviation** is `|ln(ratio)|` with the ratio clamped to `[1e-6, 1e6]`.
     - The map suggested `|ratio − 1|`. That measure is asymmetric: a drop to 0.5× scores 0.5, while a rise to 2× scores 1.0. The log treats both the same, and a collapsing channel (a loose sensor) is as noteworthy as a rising one.
   - **Direction:** `up` if ratio ≥ 1.05, `down` if ratio ≤ 0.95, else `steady`.
   - **Weighting:**
     - `importance(c)` is the sum over the predictor's `classifier_feature_columns` whose source is `c` of the mean `feature_importances_` across `predictor.classifiers`. Each element is unwrapped with `getattr(clf, "named_steps", {}).get("classifier", clf)`, matching `make_classifier`'s `("classifier", ExtraTreesClassifier)` step (`xjtu_rul.py:143-154`).
     - A feature's source is the longest `ROLLING_SOURCE_COLUMNS` entry `s` with `f == s` or `f.startswith(s + "_")`. Longest match keeps `h_rms` from claiming `h_envelope_rms_*`. Classifier features with no candidate source are not attributed: `speed_rpm`, `load_kn`, `cross_axis_*`, and raw shape factors such as `h_crest_factor`.
     - Importances are renormalised over the candidates, so they sum to 1.
     - `score = deviation × importance`, with `weighting: "model_importance"`.
     - With no loaded predictor, a legacy single `classifier` without `feature_importances_`, or a length mismatch, the result is `importance: null`, `score = deviation` and `weighting: "unweighted"`.
     - Importances are cached per `model_version` behind a `threading.Lock`, because each `feature_importances_` access averages 350 trees.
   - **Training bounds:**
     - For `model_context`, a factor's `outside_training_bounds` is true when any derived feature of that source that appears in `artifact["feature_bounds_99pct"]` (`xjtu_rul.py:230-245`; the same check as `rul_realtime.py:190-195`) is outside its `[low, high]`. Those features are listed in `out_of_bounds_features`.
     - With no predictor this is `null` (unknown, not "inside").
   - **Output:** the top `KEY_FACTOR_LIMIT = 6` by score, then deviation, then name.
   - Each factor has a human-readable `label` built from axis × metric tables. The axes are `h` "Horizontal", `v` "Vertical" and `m` "Combined (h+v magnitude)". The metric/unit pairs are:

     | Metric | Label | Unit |
     |---|---|---|
     | `rms` | "RMS vibration" | `g` |
     | `peak` | "Peak vibration" | `g` |
     | `kurtosis` | "Kurtosis" | — |
     | `envelope_rms` | "Envelope RMS" | `g` |
     | `envelope_kurtosis` | "Envelope kurtosis" | — |
     | `spectral_entropy` | "Spectral entropy" | — |
     | `envelope_energy_100_200_hz_ratio` | "Envelope energy share, 100–200 Hz" | `fraction` |

     XJTU-SY accelerations are in g.
   - **Operating conditions** are reported beside the factors, not ranked: `speed_rpm` (rpm) and `load_kn` (kN) at the trigger, plus `outside_training_bounds` from the same bounds table. A machine running far outside XJTU-SY's three conditions is a large part of why a prediction may be OOD.

7. **Prediction block: the stored prediction, else the snapshot's result dict, else the nearest prediction. OOD is never dropped.**
   - **Where the values come from, in order:**
     1. The snapshot's `prediction` dict (has `outside_training_features`).
     2. The `predictions` row by the snapshot's `prediction_id` or `alerts.prediction_id`.
     3. The machine's latest prediction with `timestamp <= opened_at`. `locate: "nearest_timestamp"`.
     4. Otherwise `null`, plus a note.
   - **Fields:**
     - `prediction_id`, `timestamp`, `model_version`, `health_state`
     - `failure_within_horizon_probability`, `predicted_rul_minutes`, `rul_estimate_kind`
     - `prediction_interval_low` / `_high`, from the row's columns or `prediction_interval_90_minutes`
     - `prognostic_horizon_minutes`
     - `out_of_distribution` (bool), `outside_training_features` (list; `[]` when unknown)
     - `warnings` (`warnings_json` decoded)
     - `locate`
   - **Demo predictions** (`model_name: "demo_simulator"`, `src/prediction/live.py:104-114`) have no probability or RUL. Those fields are null, and the UI says "Synthetic demo reading — no model prediction".

8. **Probable cause: a new `rule_based.explain_probable_cause(row)`; `classify_probable_cause` unchanged.**
   - **Signature:** `explain_probable_cause(row) -> dict` returns `{"label", "rule", "summary", "checks": [{"feature", "value", "operator", "threshold", "passed"}]}`.
   - **The rules** are the same four branches in the same order as `classify_probable_cause` (`rule_based.py:33-45`). The threshold is read from the module constant `HIGH_KURTOSIS` (`:21`), never copied.

     | Rule | Condition | Label |
     |---|---|---|
     | `missing_kurtosis` | kurtosis None or NaN | `sensor_or_data_quality_issue` |
     | `high_kurtosis` | kurtosis ≥ 5.0 | `bearing_wear` |
     | `nonzero_rms` | rms is present and > 0 | `imbalance` |
     | `no_rule_matched` | none of the above | `unknown` |

     Each failed earlier branch appears in `checks` with `passed: false`, so the trace reads as "kurtosis 3.1 < 5.0, so not bearing wear; RMS 0.42 > 0, so imbalance".
   - **One source of truth.** `classify_probable_cause` is **not** refactored to call it: the instruction is to keep its behaviour unchanged, and a refactor is a behaviour risk for the batch path. A parity test pins `explain_probable_cause(row)["label"] == classify_probable_cause(row)` over a grid of rows instead.
   - **Inputs:**
     - The snapshot's `features` short keys mapped through `pipeline._FEATURE_ALIASES` (`pipeline.py:34-37`). That is exactly what `_probable_cause` classified.
     - Else the trigger reading's promoted `vibration_h_rms` / `vibration_h_kurtosis`.
     - `inputs_source` records which.
   - **Agreement check.** The block also carries the alert's stored `probable_cause` (the label) and `matches_alert`. If they differ (a batch-pipeline alert, or a reconstruction from a different reading), a note says so. The stored label is still the one presented.
   - **Wording** (SRS FR-25/NFR-13, `rule_based.py:10-11`):
     - `display` is always "Probable cause: {human label}".
     - `disclaimer` is "Heuristic rule on vibration features — a probable cause, not a diagnosis. Confirm on inspection."
     - Neither the API nor the UI ever says "cause is", "diagnosed" or "root cause:" without "probable". A test greps for this.

9. **Similar past incidents: earlier alerts with the same probable cause or the same machine, scored, joined to what actually happened.**
   - **Candidates:** alerts other than this one with `opened_at < this.opened_at` (or equal and `id < this.id`) and at least one of these:
     - `probable_cause = this.probable_cause` (when not null), or
     - `machine_id = this.machine_id`, or
     - `alert_feedback.actual_cause = this.probable_cause`.

     That is one indexed query, capped at the 500 most recent.
   - **Score (0–1)** is the sum of:
     - 0.5 for a matching cause: the same `probable_cause`, or a recorded `actual_cause` equal to this alert's probable cause. These are not additive.
     - 0.3 for the same machine.
     - 0.1 for the same severity.
     - `0.1 × exp(−age_days / 90)` for recency, where `age_days = this.opened_at − that.opened_at`.

     `match_reasons` lists `same_probable_cause`, `actual_cause_matches`, `same_machine` and `same_severity`. Results are ordered by score, then `opened_at` descending, and limited to `similar_limit` (default 5, 0–20).
   - **Each incident** carries:
     - the alert facts: `alert_id`, `machine_id`, `opened_at`, `resolved_at`, `status`, `severity`, `health_state`, `probable_cause`, `synthetic`
     - `similarity`, `match_reasons`
     - `feedback`: `{outcome, actual_cause, actual_failure_at, notes}` or null. Notes are cut to 280 characters.
     - `cause_confirmed`: true/false when `actual_cause` is set and not `unknown`/`other`; null otherwise.
     - `work_orders`: `[{id, status, title, completed_at}]`
     - `maintenance`: `[{id, performed_at, type, description, technician}]`, from `maintenance_records.alert_id` united with `work_orders.maintenance_record_id`, de-duplicated by id.
   - **Synthetic isolation.** A real alert's list never includes demo (`source = 'demo'`) incidents. Demo history is not evidence about real machines, and `src/feedback/accuracy.py:55` excludes it for the same reason. A demo alert's list includes both, each with its `synthetic` flag.
   - **Guards.** `alert_feedback` and `work_orders` are guarded with `db.table_exists` (`src/storage/db.py:356-362`). An old file opened outside `get_db` then just loses those joins.

10. **Synthetic labelling.** `synthetic = (alert.source == "demo")`.
    - It appears at the top level, on every similar incident, and in the UI as a "Synthetic (demo)" badge with the line "Generated by the admin demo trigger; readings are scaled copies of a real reading."
    - The demo reading is stored with `dataset = 'xjtu_sy'` (`src/prediction/live.py:95`), so `source` is the only reliable marker.

11. **Every section degrades independently.**
    - `build_explanation` builds each section in its own `try/except Exception`. A failing section becomes `null`, or the `{status: "error"}` variant for key factors, plus a note, and is logged with `logger.exception`. The route returns 200 with the other sections; only an unknown alert is an error (404).
    - **Why:** the explanation is advisory. A malformed `features_json` on one row must not hide the probable cause or the past incidents.

12. **Routing: `alerts/:id?` on the existing Alerts page, mirroring `work-orders/:id?`.**
    - `frontend/src/App.tsx:54-57` already uses this pattern so a drawer keeps its list page and filters. The id is in the path because `LoginPage` restores only `from.pathname` (map §0).
    - Feature 5 builds `/m/alerts/:id` on the same API and on the panel's chrome-less `AlertExplanationView`.

13. **No environment variables.** The window size (30), the history minimum (5), the factor limit (6), the similar-incident default and maximum (5/20) and the similarity weights are module constants in `src/root_cause/explain.py`, as `HIGH_KURTOSIS` is in `rule_based.py`. They are tuning knobs for developers, not deployment settings, and `.env.example` stays unchanged.

## Data model

### `src/storage/db.py`

New constant after `FEEDBACK_SCHEMA`. `db.py:353` becomes `SCHEMA = SCHEMA + TELEMETRY_SCHEMA + DEVICE_HEALTH_SCHEMA + WORK_ORDER_SCHEMA + FEEDBACK_SCHEMA + EXPLANATION_SCHEMA`:

```python
# Alert explanations (design/2026-10-07-alert-explanation-design.md): the
# evidence behind an alert — triggering readings, key factors, the model
# prediction and the probable-cause rule trace — captured when the alert is
# created and again at each severity escalation, because the predictor's
# rolling context is in memory only and escalation overwrites the alert's
# probable_cause. Similar incidents are not stored; they are computed when
# read. Applied on its own by migration 6 and folded into SCHEMA for fresh
# installs.
EXPLANATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_explanations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id          INTEGER NOT NULL REFERENCES alerts(id),
    kind              TEXT NOT NULL CHECK(kind IN ('created','escalated')),
    reading_id        INTEGER REFERENCES readings(id),
    prediction_id     INTEGER REFERENCES predictions(id),
    created_at        TEXT NOT NULL,
    explanation_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_explanations_alert ON alert_explanations(alert_id, id);
-- At most one creation snapshot per alert; escalations may add more rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_explanations_one_created
    ON alert_explanations(alert_id) WHERE kind = 'created';
"""
```

Column semantics:
- **`reading_id` / `prediction_id`** are the reading and prediction behind *this snapshot*. They are NULL when there is none (the predictions route stores no reading; the demo stores no prediction id). They are declared foreign keys but not enforced, like `alerts.reading_id`.
- **`created_at`** is wall-clock UTC ISO-8601 at write time.
- **`explanation_json`** is the snapshot body: `explanation_version`, `triggering_readings`, `key_factors`, `prediction`, `probable_cause`, `synthetic` and `notes`. It never contains `similar_incidents`.

No change to `alerts`. Feature 2's columns are what this feature reads.

### `src/storage/migrations.py`

```python
def _migration_006_alert_explanations(conn: sqlite3.Connection) -> None:
    # Purely additive: one new table and its indexes, no ALTERs, so it is a
    # single executescript (same shape as migration 2). A v5 DB keeps every
    # row; a fresh DB, where migration 1 already ran SCHEMA, just gets the
    # version stamp.
    conn.executescript(EXPLANATION_SCHEMA)

MIGRATIONS = [..., (5, _migration_005_feedback), (6, _migration_006_alert_explanations)]
```

- `MIGRATIONS` is at `migrations.py:156-162`.
- The import block (`:52-58`) gains `EXPLANATION_SCHEMA`.
- The docstring (`:1-46`) gains a "Migration 6" paragraph.
- `LATEST_VERSION` (`:187`) becomes 6 automatically, so `ensure_current_schema` (`:203`) upgrades a v5 file on the first `get_db` request or the first MQTT message.
- The replay worker's connections open a file that `get_db` already upgraded when the replay was started.

## Services

### `src/root_cause/rule_based.py`

Add `explain_probable_cause(row) -> dict` after `classify_probable_cause` (`:24-45`), which is untouched. Add the rule-id constants `RULE_MISSING_KURTOSIS`, `RULE_HIGH_KURTOSIS`, `RULE_NONZERO_RMS` and `RULE_NO_MATCH`, and a `CAUSE_LABELS` dict mapping each label to its human text: "bearing wear", "imbalance", "sensor or data-quality issue" and "unknown". The module docstring gains one sentence pointing at the parity test.

### `src/root_cause/explain.py` (new)

Imports `ROLLING_SOURCE_COLUMNS`, `ROLLING_WINDOWS`, `BASELINE_WINDOW` and `add_past_context` from `src.training.xjtu_rul` (read-only import), `rule_based`, `src.storage.db.table_exists` and `src.alerts.live` (`API_ALERT_COLUMNS`, `api_alert`). Public surface:

```python
EXPLANATION_VERSION = 1
TRIGGER_WINDOW_READINGS = 30
MIN_BASELINE_READINGS = 5
KEY_FACTOR_LIMIT = 6
SIMILAR_DEFAULT_LIMIT, SIMILAR_MAX_LIMIT = 5, 20

def loaded_predictor():                       # decision 3; never loads
def build_snapshot(conn, alert: dict, *, reading_id=None, prediction_id=None,
                   prediction: dict | None = None, features: dict | None = None,
                   at: str | None = None, predictor=None) -> dict
def record_snapshot(conn, alert: dict, kind: str, **snapshot_kwargs) -> int | None
def similar_incidents(conn, alert: dict, *, limit=SIMILAR_DEFAULT_LIMIT) -> list[dict]
def get_explanation(conn, alert_id: int, *, similar_limit=SIMILAR_DEFAULT_LIMIT,
                    predictor=None) -> dict | None   # None => unknown alert
```

- **`build_snapshot`** returns the snapshot body. Its private section builders are `_triggering_readings`, `_key_factors`, `_prediction_block` and `_probable_cause_block`. Each is guarded per decision 11.
- **`record_snapshot`** does three things:
  - Returns `None` when `alert_explanations` doesn't exist. It logs at debug level, because a pre-v6 file reaching the pipeline outside `get_db` is possible in tests and scripts.
  - Otherwise INSERTs and commits, returning the row id.
  - On `sqlite3.IntegrityError` from the one-`created` index, rolls back and returns `None`.
- **`get_explanation`** reads the alert through `live.API_ALERT_COLUMNS` / `api_alert`, so the response embeds the same `Alert` shape as the list. It then:
  1. Loads the latest snapshot (decision 4), or calls `build_snapshot(conn, alert, reading_id=alert["reading_id"], prediction_id=alert["prediction_id"])` to reconstruct.
  2. Adds `similar_incidents`, `snapshots`, `source`, `snapshot_kind`, `snapshot_at` and `generated_at`.

### `src/prediction/pipeline.py`

- `handle_prediction` (`:49-96`) forwards to `fan_out(conn, applied, at=timestamp, prediction=prediction, broadcast=broadcast, features=features, reading_id=reading_id, prediction_id=prediction_id)`.
- `fan_out(conn, applied, *, at, prediction=None, broadcast=None, features=None, reading_id=None, prediction_id=None)` (`:99`) gains, right after the `applied is None` early return (`:109-110`):

  ```python
  if event_type in _SNAPSHOT_EVENTS:  # {"alert_created": "created", "alert_escalated": "escalated"}
      try:
          explain.record_snapshot(conn, alert, _SNAPSHOT_EVENTS[event_type], reading_id=reading_id,
                                  prediction_id=prediction_id, prediction=prediction,
                                  features=features, at=at, predictor=explain.loaded_predictor())
      except Exception:
          logger.exception("Failed to snapshot the explanation of alert %s", alert.get("id"))
  ```
- The module docstring gains one sentence on the snapshot.

### `src/api/routes/demo.py`

- `simulate_fault` (`:55`) calls `pipeline.fan_out(db, result, at=_now(), reading_id=prediction["reading_id"])`. The demo reading is the trigger; there is no prediction id, because `insert_predictions` returns none.
- `reset_machine` (`:93`) is unchanged: it only resolves, so no snapshot is taken.

### Callers

There are no other signature changes. `replay_service._default_fan_out` and `ingest._default_fan_out` already pass `features`, `prediction_id` and `reading_id` into `handle_prediction` (feedback design, "Callers").

## API

All routes need a session. `alerts.router` is already mounted behind `get_current_user` (`src/api/app.py:175-177`).

| Method & path | Roles | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/alerts/{id}/explanation?similar_limit=5` | any (admin, supervisor, operator) | — | `AlertExplanation` | 401 no session; 404 `unknown alert: {id}`; 422 `similar_limit` outside 0–20 or `id` not an int |

- It is a sync `def` like `get_alert_feedback` (`alerts.py:175-181`), and calls `explain.get_explanation(db, alert_id, similar_limit=..., predictor=explain.loaded_predictor())`.
- `similar_limit: int = Query(5, ge=0, le=20)`.
- **There is no 500 for section failures** (decision 11). An unexpected error outside the sections, such as the database being locked, still propagates as FastAPI's 500, as everywhere else.

`src/api/schemas.py` (after `AlertCloseResponse`, `:98-101`):

```python
class ExplanationReading(BaseModel):
    reading_id: Optional[int] = None        # None only for an unsaved trigger (decision 5)
    timestamp: str
    cycle: Optional[int] = None
    is_trigger: bool = False
    vibration_h_rms: Optional[float] = None
    vibration_h_kurtosis: Optional[float] = None
    vibration_v_rms: Optional[float] = None
    vibration_v_kurtosis: Optional[float] = None
    cross_axis_rms_ratio: Optional[float] = None
    cross_axis_correlation: Optional[float] = None

class ExplanationChannel(BaseModel):
    key: str; label: str; unit: Optional[str] = None

class TriggeringReadings(BaseModel):
    locate: Literal["reading_id", "prediction_reading", "nearest_timestamp", "snapshot_features", "none"]
    trigger_reading_id: Optional[int] = None
    trigger_timestamp: Optional[str] = None
    channels: list[ExplanationChannel]
    readings: list[ExplanationReading]

class KeyFactor(BaseModel):
    feature: str                              # e.g. "h_kurtosis"
    label: str                                # "Horizontal kurtosis"
    axis: Literal["horizontal", "vertical", "combined"]
    unit: Optional[str] = None                # "g" | "fraction" | None
    value: float
    baseline: float
    ratio: float
    direction: Literal["up", "down", "steady"]
    deviation: float                          # |ln ratio|
    importance: Optional[float] = None        # 0..1, None when unweighted
    score: float
    outside_training_bounds: Optional[bool] = None   # None = unknown
    out_of_bounds_features: list[str] = []

class OperatingCondition(BaseModel):
    feature: Literal["speed_rpm", "load_kn"]; label: str; unit: str
    value: Optional[float] = None; outside_training_bounds: Optional[bool] = None

class KeyFactors(BaseModel):
    status: Literal["ok", "insufficient_history", "no_readings", "error"]
    method: Optional[Literal["model_context", "promoted_columns"]] = None
    weighting: Optional[Literal["model_importance", "unweighted"]] = None
    baseline_readings: int = 0
    factors: list[KeyFactor] = []
    operating_conditions: list[OperatingCondition] = []

class ExplanationPrediction(BaseModel):
    locate: Literal["snapshot", "prediction_id", "nearest_timestamp"]
    prediction_id: Optional[int] = None
    timestamp: Optional[str] = None
    model_version: Optional[str] = None
    health_state: Optional[str] = None
    failure_within_horizon_probability: Optional[float] = None
    predicted_rul_minutes: Optional[float] = None
    rul_estimate_kind: Optional[str] = None
    prediction_interval_low: Optional[float] = None
    prediction_interval_high: Optional[float] = None
    prognostic_horizon_minutes: Optional[float] = None
    out_of_distribution: bool = False
    outside_training_features: list[str] = []
    warnings: list[str] = []

class RuleCheck(BaseModel):
    feature: str; value: Optional[float] = None; operator: str
    threshold: Optional[float] = None; passed: bool

class ProbableCauseExplanation(BaseModel):
    label: Optional[str] = None               # the alert's stored probable_cause
    display: str                              # "Probable cause: bearing wear"
    disclaimer: str
    rule: Optional[str] = None                # rule id from explain_probable_cause
    summary: Optional[str] = None
    checks: list[RuleCheck] = []
    evaluated_label: Optional[str] = None
    matches_alert: Optional[bool] = None
    inputs_source: Optional[Literal["snapshot_features", "trigger_reading"]] = None

class IncidentFeedback(BaseModel):
    outcome: str; actual_cause: Optional[str] = None
    actual_failure_at: Optional[str] = None; notes: Optional[str] = None

class IncidentWorkOrder(BaseModel):
    id: int; status: str; title: str; completed_at: Optional[str] = None

class IncidentMaintenance(BaseModel):
    id: int; performed_at: str; type: Optional[str] = None
    description: Optional[str] = None; technician: Optional[str] = None

class SimilarIncident(BaseModel):
    alert_id: int; machine_id: str; opened_at: str; resolved_at: Optional[str] = None
    status: str; severity: str; health_state: str; probable_cause: Optional[str] = None
    synthetic: bool; similarity: float; match_reasons: list[str]
    feedback: Optional[IncidentFeedback] = None
    cause_confirmed: Optional[bool] = None
    work_orders: list[IncidentWorkOrder] = []
    maintenance: list[IncidentMaintenance] = []

class SnapshotRef(BaseModel):
    kind: Literal["created", "escalated"]; created_at: str

class AlertExplanation(BaseModel):
    alert: Alert                              # API_ALERT_COLUMNS shape, incl. feedback
    explanation_version: int
    source: Literal["snapshot", "reconstructed"]
    snapshot_kind: Optional[Literal["created", "escalated"]] = None
    snapshot_at: Optional[str] = None
    snapshots: list[SnapshotRef] = []
    generated_at: str                         # when the body was computed (snapshot or now)
    synthetic: bool
    triggering_readings: Optional[TriggeringReadings] = None
    key_factors: Optional[KeyFactors] = None
    prediction: Optional[ExplanationPrediction] = None
    probable_cause: Optional[ProbableCauseExplanation] = None
    similar_incidents: list[SimilarIncident] = []
    notes: list[str] = []
```

Example (abridged):

```json
{"alert": {"id": 41, "machine_id": "Bearing1_3", "severity": "high", "probable_cause": "bearing_wear", "...": "Alert"},
 "explanation_version": 1, "source": "snapshot", "snapshot_kind": "escalated",
 "snapshot_at": "2026-10-07T14:20:03+00:00",
 "snapshots": [{"kind": "created", "created_at": "2026-10-07T13:05:11+00:00"},
               {"kind": "escalated", "created_at": "2026-10-07T14:20:03+00:00"}],
 "generated_at": "2026-10-07T14:20:03+00:00", "synthetic": false,
 "triggering_readings": {"locate": "reading_id", "trigger_reading_id": 9182,
   "trigger_timestamp": "2026-10-07T14:20:00+00:00", "channels": [{"key": "vibration_h_rms", "label": "Horizontal RMS", "unit": "g"}],
   "readings": [{"reading_id": 9182, "timestamp": "2026-10-07T14:20:00+00:00", "cycle": 412, "is_trigger": true, "vibration_h_rms": 1.82}]},
 "key_factors": {"status": "ok", "method": "model_context", "weighting": "model_importance", "baseline_readings": 20,
   "factors": [{"feature": "h_kurtosis", "label": "Horizontal kurtosis", "axis": "horizontal", "unit": null,
                "value": 9.4, "baseline": 3.0, "ratio": 3.13, "direction": "up", "deviation": 1.14,
                "importance": 0.21, "score": 0.24, "outside_training_bounds": true,
                "out_of_bounds_features": ["h_kurtosis_baseline_ratio"]}],
   "operating_conditions": [{"feature": "speed_rpm", "label": "Shaft speed", "unit": "rpm", "value": 2100, "outside_training_bounds": false}]},
 "prediction": {"locate": "snapshot", "failure_within_horizon_probability": 0.83, "predicted_rul_minutes": 42.0,
   "rul_estimate_kind": "point_estimate", "prediction_interval_low": 12.0, "prediction_interval_high": 72.0,
   "out_of_distribution": true, "outside_training_features": ["h_kurtosis_baseline_ratio"],
   "warnings": ["out_of_distribution: input differs materially from XJTU-SY; ..."]},
 "probable_cause": {"label": "bearing_wear", "display": "Probable cause: bearing wear",
   "disclaimer": "Heuristic rule on vibration features — a probable cause, not a diagnosis. Confirm on inspection.",
   "rule": "high_kurtosis", "checks": [{"feature": "h_kurtosis", "value": 9.4, "operator": ">=", "threshold": 5.0, "passed": true}],
   "evaluated_label": "bearing_wear", "matches_alert": true, "inputs_source": "snapshot_features"},
 "similar_incidents": [{"alert_id": 17, "machine_id": "Bearing1_3", "similarity": 0.86,
   "match_reasons": ["same_probable_cause", "same_machine", "same_severity"],
   "feedback": {"outcome": "maintenance_prevented", "actual_cause": "bearing_wear"}, "cause_confirmed": true,
   "work_orders": [{"id": 7, "status": "done", "title": "Replace bearing", "completed_at": "..."}], "maintenance": [{"id": 12, "...": "..."}]}],
 "notes": []}
```

## Realtime events

There are none new.
- `alert_created` and `alert_escalated` already carry the alert and, from the model path, the `prediction` (`pipeline.py:134-143`). The snapshot is committed before they are broadcast (decision 2).
- `LiveEvent` stays the discriminated union it is (`frontend/src/api/types.ts:381-386`). `describe()`, `NotificationBell` and `MachineDetailPage` are untouched.
- The open panel re-fetches when `lastEvent` is about its alert: any event with `'alert' in e && e.alert.id === alertId` (`alert_escalated`, `alert_resolved`, `alert_acknowledged`, `alert_paged`, `alert_closed`, `alert_feedback_recorded`). It also re-fetches on a work-order event with `work_order.alert_id === alertId`. Similar incidents for *other* alerts are not live-refreshed. They are recomputed on the next open, which is enough for history.

## Notification behaviour

| Trigger | Who is emailed | Notes |
|---|---|---|
| Alert created / escalated | admins + supervisors (level 0), unchanged | The snapshot is written first. A snapshot failure never blocks the email (decision 2). The email body is unchanged; no link is added, because there is no configured public base URL. |
| Snapshot written | nobody | Not an event. |
| Explanation viewed | nobody | Read-only. |

## Frontend UX

### Types and client

- **`frontend/src/api/types.ts`:** `AlertExplanation`, `TriggeringReadings`, `ExplanationReading`, `ExplanationChannel`, `KeyFactors`, `KeyFactor`, `OperatingCondition`, `ExplanationPrediction`, `ProbableCauseExplanation`, `RuleCheck`, `SimilarIncident`, `IncidentFeedback`, `IncidentWorkOrder`, `IncidentMaintenance` and `SnapshotRef`. They mirror the Pydantic models, with string-literal unions for the `Literal`s.
- **`frontend/src/api/client.ts`:** `getAlertExplanation: (id: number, similarLimit?: number) => request<AlertExplanation>(\`/alerts/${id}/explanation${similarLimit != null ? \`?similar_limit=${similarLimit}\` : ''}\`)`, next to `getAlertFeedback` (`:123`).

### Components

- **`components/TrendChart.tsx`:** a new optional `marker?: { timestamp: string; label: string }` prop (`:6-9`).
  - It renders a recharts `ReferenceLine` at the marker's compact `t` label.
  - It appends `, ${label} at ${timestamp}` to the `aria-label` (`:54`), so the trigger is announced and testable under jsdom.
  - Existing callers are unaffected.
- **`components/AlertExplanationView.tsx` (new)** is the chrome-less content that feature 5 reuses. Props `{ explanation: AlertExplanation }`. It renders:
  - **Header strip:**
    - Badges: "Snapshot · captured {relative}" or "Reconstructed" (with tooltip "No snapshot was captured for this alert; rebuilt from stored data"), "Synthetic (demo)" when `synthetic`, and "Escalated" when `snapshot_kind === 'escalated'`.
    - An **OOD banner** whenever `prediction?.out_of_distribution` or any factor or operating condition has `outside_training_bounds === true`. It has `role="status"` and the text "Outside the model's training range — treat this estimate with caution." It is always at the top, regardless of which section is scrolled to.
  - **Triggering readings:**
    - A channel `<Select>` over `channels` (default `vibration_h_kurtosis`).
    - A `TrendChart` with `marker={{ timestamp: trigger_timestamp, label: 'Trigger' }}`.
    - A caption: "Trigger: reading #{id} at {time}", or "Nearest reading to the alert time (no linked reading)" for `nearest_timestamp`.
    - An empty state.
  - **Key factors:**
    - A `<ul aria-label="Key factors">` of rows. Each row has the label, a bar whose width is `score / maxScore` (a `div` with `role="img"` and `aria-label="{label}: {ratio}× baseline, {direction}"`), and the direction glyph and text: "↑ 3.1× baseline" / "↓ 0.5× baseline" / "≈ baseline".
    - Values with units, and an "Outside training range" badge.
    - The footer says how the ranking was made: "Ranked by deviation from this machine's first {baseline_readings} readings, weighted by model importance". Unweighted gives "…unweighted (model not loaded)".
    - Operating conditions are listed as context.
    - Status messages for `insufficient_history`, `no_readings` and `error`.
    - Bar colours use the intensity ramp from `healthStyles.ts`, not red/green.
  - **Probable cause:**
    - `display` as the heading text, then the `disclaimer`.
    - The rule trace as an ordered list, with ✓/✗ and screen-reader text "passed"/"not met" for each check, e.g. "Horizontal kurtosis 9.4 ≥ 5.0 — passed".
    - A note when `matches_alert === false`.
  - **Model prediction:**
    - Failure probability as a percentage, plus the horizon.
    - RUL "42 min (90% interval 12–72)", or "> 120 min (lower bound)" when `rul_estimate_kind === 'lower_bound'`.
    - The model version, and a warnings list with any `out_of_distribution:` warning first.
    - "No model prediction recorded" when null, or "Synthetic demo reading — no model prediction" for a demo alert.
  - **Similar past incidents:**
    - A list with machine, opened time (`formatRelative`), severity chip, similarity %, and match-reason chips.
    - An outcome `Badge` (`feedbackOutcomeLabel` / `feedbackOutcomeTone`, `healthStyles.ts:188-195`) and "Actual cause: …" with "matched" or "differed".
    - Work-order links `Link to=/work-orders/{id}` ("WO #7 · done"), and maintenance lines.
    - A "Why?" link to `/alerts/{alert_id}`, and a "Synthetic" badge.
    - Empty state: "No earlier alerts with this probable cause or on this machine."
  - **Notes:** a muted list at the bottom.
- **`components/AlertExplanationPanel.tsx` (new)** is the drawer. Props `{ alertId: number; onClose: () => void }`.
  - Same chrome as `WorkOrderDrawer`: a fixed right-hand panel, `role="dialog"`, `aria-modal="true"`, `aria-labelledby` the title "Why this alert? — {machine_id}", and `tabIndex={-1}`. `useDialogFocus(true, onClose, panelRef)` (`frontend/src/lib/useDialogFocus.ts:8`) moves focus in, handles Escape and restores focus. There is a backdrop click-to-close and a Close button with `data-autofocus`.
  - It fetches `api.getAlertExplanation(alertId)` on mount and on the live events listed in "Realtime events".
  - It has loading ("Loading explanation…"), error (the message, plus a 404 text "Alert #{id} not found") and data states. The data state renders `AlertExplanationView`.

### Entry points ("Why?")

- **`pages/AlertsPage.tsx`:**
  - A "Why?" button is the first item of the Actions cell (`:192-224`). `onClick={(e) => { e.stopPropagation(); navigate(\`/alerts/${a.id}\`) }}`, `aria-label={\`Why this alert? Alert #${a.id}\`}`.
  - The page reads `useParams<{ id: string }>()`. When `id` parses to an integer it renders `<AlertExplanationPanel key={id} alertId={id} onClose={() => navigate('/alerts')} />`. The list, its status filter and sort stay mounted.
- **`components/AlertsPanel.tsx`:**
  - A new optional `onExplain?: (alert: Alert) => void`. When it is given, each `<li>` becomes a flex row with the existing select button plus a sibling small "Why?" button with the same `aria-label`. A button can't nest inside the existing `motion.button` (`:22-45`).
  - No handler, no button, so existing callers are unchanged.
- **`pages/DashboardPage.tsx`** (`:143`): `onExplain={(a) => navigate(\`/alerts/${a.id}\`)}`.
- **`components/MachineDetail.tsx`:**
  - A new optional `onExplain?: (alert: Alert) => void` prop (`:15-26`). It is passed to `AlertsPanel` (`:231`), and also drives a "Why this alert?" outline button next to `OutcomeButton` (`:222-224`) for the selected alert.
  - **Page-owned**, for the remount reason already documented on the other two handlers.
- **`pages/MachineDetailPage.tsx`:** `const [explainAlertId, setExplainAlertId] = useState<number | null>(null)`, which renders `AlertExplanationPanel` beside the existing dialogs (`:51-64`).

### Routes and navigation

- **`App.tsx`:** `<Route path="alerts/:id?" element={<AlertsPage />} />` replaces `alerts` (`:48`). It sits inside `RequireAuth`/`AppShell` with no role gate, matching the API.
- There is no new nav item. The existing "Alerts" `NavLink` stays active on `/alerts/5`.

### Tests and MSW

- **`frontend/src/test/fixtures.ts`:**
  - `alertExplanation`: a snapshot for alert 2 with two factors (one out of bounds), an OOD prediction, a rule trace, and one similar incident (alert 1, with feedback `maintenance_prevented`, WO #7 and one maintenance record).
  - `alertExplanationReconstructed`: legacy, no prediction, `insufficient_history`.
  - Handlers return `structuredClone(...)`.
- **`frontend/src/test/server.ts`:** default handler `http.get('/api/alerts/:id/explanation', ({ params }) => Number(params.id) === 999 ? HttpResponse.json({ detail: 'unknown alert: 999' }, { status: 404 }) : HttpResponse.json({ ...structuredClone(alertExplanation), alert: { ...structuredClone(alertExplanation.alert), id: Number(params.id) } }))`. MSW runs with `onUnhandledRequest: 'error'` (`frontend/src/test/setup.ts:8`).

## Environment variables

None (decision 13). `.env.example` and the README env table are unchanged.

## Test plan

### Backend (pytest)

New `tests/root_cause/__init__.py`.

`tests/root_cause/test_rule_based.py`
1. **Parity:** `explain_probable_cause(row)["label"] == classify_probable_cause(row)` for kurtosis None, NaN, 4.99, 5.0 (boundary → `bearing_wear`) and 12; and rms None, 0, -0.1 and 0.4. That is the full cross product.
2. **Trace:** `high_kurtosis` has one check `{feature: "vibration_h_kurtosis", operator: ">=", threshold: HIGH_KURTOSIS, passed: true}`. `nonzero_rms` lists the failed kurtosis check before the passed rms check. `missing_kurtosis` and `no_rule_matched` have their ids. The threshold is read from the constant: monkeypatching `HIGH_KURTOSIS` changes it.
3. **Regression:** `classify_probable_cause` and `add_probable_cause` give the same results as before for a fixed frame.

`tests/root_cause/test_explain.py` uses an in-memory DB via `run_migrations` and a helper that seeds machine `mx` with N readings, each with full `features_json` (every `ROLLING_SOURCE_COLUMNS` key) and promoted columns. A `FakePredictor` has `classifiers`, `classifier_feature_columns` and `artifact` (`feature_bounds_99pct`, `model_version`); its classifiers wrap fake estimators with `named_steps={"classifier": obj}` and fixed `feature_importances_`.

4. Triggering readings by `reading_id`: at most 30, ordered, ending at the trigger, `is_trigger` only on it, and `locate == "reading_id"`. Every reading dict has exactly the six vibration keys plus the identity keys. `"temperature"` and `speed_rpm` do not appear in `json.dumps(section)`.
5. Nearest-timestamp fallback when `reading_id` is NULL: it picks the latest `<= opened_at`, or the first after when none is earlier, with `locate == "nearest_timestamp"` and a note.
6. A reading-less snapshot with `features` appends an unsaved trigger point (`reading_id: None`, `is_trigger`) after stored readings with `timestamp <= at`.
7. No readings: `locate == "none"`, `readings == []`, key factors `status == "no_readings"`, and no exception.
8. Ranking (`model_context`): `h_kurtosis` raised 4× in the trigger reading ranks first with `direction == "up"` and ratio ≈ 4. A channel at 0.5× gets `direction == "down"` and the same deviation as a 2× channel (the log symmetry).
9. **Operating conditions excluded:** with `FakePredictor` importances concentrated on `speed_rpm`/`load_kn`, neither appears in `factors`. Candidate importances are renormalised to sum to 1, and `operating_conditions` lists both with values.
10. **Importance weighting:** two channels with equal deviation are ordered by importance, with `weighting == "model_importance"`. Longest-prefix attribution: an importance on `h_envelope_rms_mean_5` goes to `h_envelope_rms`, not `h_rms`.
11. **No predictor:** `weighting == "unweighted"`, `importance is None`, `score == deviation`, ranking still produced, `outside_training_bounds is None`.
12. **Training bounds:** a `feature_bounds_99pct` entry on `h_kurtosis_baseline_ratio` that the trigger exceeds gives `outside_training_bounds is True`, and `out_of_bounds_features` contains it. Within bounds gives `False`.
13. Insufficient history (4 earlier readings) gives `status == "insufficient_history"` and `factors == []`.
14. `features_json == '{}'` with ≥ 5 readings uses `method == "promoted_columns"`, four candidates at most, and `outside_training_bounds is None`.
15. Malformed classifiers (a legacy single classifier without `feature_importances_`, or a length mismatch) fall back to unweighted without raising.
16. Prediction block:
    - By `prediction_id`: `out_of_distribution True` and `warnings` decoded from `warnings_json`.
    - By nearest timestamp when the link is NULL.
    - `None` with a note when there are no predictions.
    - The snapshot `prediction` dict keeps `outside_training_features`.
    - A demo prediction gives null probability and RUL.
17. Probable cause block:
    - `display` starts with "Probable cause: ". The disclaimer is present.
    - Over the whole serialized explanation, a case-insensitive regex finds no `\bdiagnos(is|ed)\b` outside the disclaimer, and no "root cause:".
    - `matches_alert False` plus a note when the stored label differs.
    - `inputs_source` is `snapshot_features` when features are given, else `trigger_reading`.
18. **Section isolation:** monkeypatching `_key_factors` to raise gives `key_factors.status == "error"` and a note, while the other sections are still present.
19. Similar incidents:
    - Same cause on another machine, and same machine with a different cause, are both returned. A later alert and the alert itself are excluded.
    - Order follows the weights.
    - An alert with a different predicted cause but `alert_feedback.actual_cause` equal to this one's probable cause is included with `actual_cause_matches`.
    - Feedback is joined, with notes cut to 280. `cause_confirmed` is True/False/None.
    - Work orders and maintenance come both directly and via `work_orders.maintenance_record_id`, de-duplicated.
    - `limit` is respected, and 0 gives `[]`.
20. **Synthetic:** a `source='demo'` alert gives `synthetic True`. A real alert's similar list excludes demo alerts, while a demo alert's list includes them with `synthetic True`.
21. `record_snapshot`:
    - Writes a `created` row with `reading_id` / `prediction_id` and returns its id.
    - A second `created` for the same alert returns None and leaves one row.
    - Two `escalated` rows are allowed.
    - Returns None, without raising, when `alert_explanations` is absent (a v5 DB).
22. `get_explanation`:
    - Serves the latest snapshot (an escalated row over a created one).
    - Reconstructs when there is none (`source == "reconstructed"`, `snapshots == []`).
    - Reconstructs with a note when `explanation_json` is corrupt or has an unknown `explanation_version`.
    - Returns None for an unknown id.
    - Similar incidents are recomputed: feedback inserted after the snapshot appears.
    - Does not write: the row count of `alert_explanations` is unchanged after reconstructing.
23. `loaded_predictor()` returns None while `_cached_predictor` is empty and never calls it: monkeypatch it to a function that fails the test if invoked, with `cache_info` stubbed.

`tests/prediction/test_pipeline.py` (extend). Use m2 for fresh episodes, per the conftest seed.

24. `handle_prediction` opening an alert on m2 with `reading_id` writes one `created` snapshot with that reading as trigger. A severity escalation writes an `escalated` snapshot whose `reading_id` is the escalating reading, while `alerts.reading_id` keeps the opener. A resolve and a steady same-severity reading write nothing.
25. **Snapshot ordering:** the fake `broadcast` asserts the snapshot row already exists when it is called.
26. **Failure isolation:** with `explain.record_snapshot` monkeypatched to raise, the alert is committed, `notify_alert` is still called, `last_paged_at` is stamped, the broadcast happens, and the error is logged.
27. Existing `fan_out` callers and test hooks that don't pass the new keywords still work.

`tests/test_demo.py` (extend)

28. `POST /api/demo/simulate-fault` (critical on m2) writes a `created` snapshot whose trigger is the demo reading. `GET /api/alerts/{id}/explanation` then shows `synthetic True`, `key_factors.method` either `promoted_columns` or `insufficient_history` (2 seeded readings plus the demo one), and a prediction with null probability.

`tests/api/test_alert_explanation_route.py` (new)

29. 404 `{"detail": "unknown alert: 999"}`.
30. 401 for `anon_client`.
31. Admin, supervisor and operator (`auth_client(role)`) each get 200.
32. Seeded legacy alert 2 (m1, open, bearing_wear) gives `source == "reconstructed"` and `alert.id == 2`. `similar_incidents` contains alert 1 (earlier, same machine and cause) with `match_reasons ⊇ {"same_probable_cause", "same_machine"}`. Alert 1's explanation does not list alert 2.
33. Inserted snapshot rows give `source == "snapshot"`, `snapshot_kind == "escalated"`, and `snapshots` in order.
34. `similar_limit=0` gives `[]`. `similar_limit=21` and `-1` give 422.
35. The response validates against `AlertExplanation`. `GET /api/alerts` is unchanged in shape: no `explanation` key.

`tests/storage/test_migrations.py` (extend; asserts use `LATEST`, `:8`)

36. `_v5_db()`: steps 1–5 applied and stamped, `alert_explanations` dropped. `test_v5_db_upgrades_to_v6_preserving_data` seeds one row in every app table, including `alert_feedback`. After `run_migrations(conn) == LATEST` the counts are preserved, the table and both indexes exist, `versions == list(range(1, LATEST + 1))` with `6 in versions`, and a re-run is idempotent.
37. `test_fresh_db_alert_explanation_constraints`:
    - `kind='other'` raises `IntegrityError`.
    - A second `created` for the same alert raises.
    - Two `escalated` rows insert.
    - A NULL `explanation_json` raises.
38. `ensure_current_schema` upgrades a v5 file (the pattern of `:643-650`).

`tests/docs` (existing) must pass: `alert_explanations` documented, no `temperature_c`.

### Frontend (vitest + RTL + MSW)

`components/AlertExplanationPanel.test.tsx` (new)
1. It renders `role="dialog"` with `aria-modal` and an accessible name containing "Why this alert?". Focus moves into the panel. Escape calls `onClose`, and focus returns to the opener.
2. Section headings appear: Triggering readings, Key factors, Probable cause, Model prediction, Similar past incidents.
3. The OOD banner (`role="status"`) shows when `prediction.out_of_distribution`. It also shows when only a key factor is out of bounds. It is hidden when neither is.
4. Key factors appear in API order with "↑ 3.1× baseline" text, the "Outside training range" badge and the unweighted footnote variant.
5. Probable cause shows "Probable cause: bearing wear", the disclaimer and the trace items with passed/not-met text. No text matches `/diagnos/i` except the disclaimer.
6. Model prediction shows the probability %, "42 min (90% interval 12–72)", the lower-bound variant, and "No model prediction recorded" for null.
7. Similar incidents show the outcome badge text, "Actual cause: bearing wear (matched)", a link to `/work-orders/7` and a "Why?" link to `/alerts/1`.
8. The reconstructed fixture shows the "Reconstructed" badge and the `insufficient_history` message. The synthetic fixture shows the "Synthetic (demo)" badge.
9. A 404 (id 999) shows "Alert #999 not found". The loading state renders first.
10. A live `alert_escalated` event for the same alert id triggers a re-fetch; one for another id does not. Count requests in an MSW handler spy.

`components/TrendChart.test.tsx` (extend)
11. `marker` adds "Trigger at …" to the `aria-label`. Without `marker` the label is unchanged.

`pages/AlertsPage.test.tsx` (extend)
12. Clicking "Why?" on alert 2 opens the drawer (URL `/alerts/2`) and does **not** navigate to `/machines/m1`.
13. Rendering at `/alerts/2` (MemoryRouter `initialEntries`) shows the list and the drawer. Closing returns to `/alerts` with the list still rendered.

`components/AlertsPanel.test.tsx` (extend)
14. With `onExplain`, "Why?" calls `onExplain(alert)` and not `onSelect`. Without it, there is no "Why?" button.

`pages/DashboardPage.test.tsx` / `pages/MachineDetailPage.test.tsx` (extend)
15. Dashboard "Why?" navigates to `/alerts/2`. Machine Detail "Why this alert?" (after selecting the alert) opens the panel. The panel survives a live-event remount of `MachineDetail`.

`api/client.test.ts` (extend)
16. `getAlertExplanation(2)` requests `/api/alerts/2/explanation`; `(2, 0)` adds `?similar_limit=0`.

`App.test.tsx` (extend)
17. An authenticated `/alerts/2` renders the Alerts page with the drawer. Unauthenticated, it redirects to login, and `from.pathname` is kept as `/alerts/2`.

Gates: `python -m pytest -q -p no:cacheprovider`, `npx vitest run --testTimeout=30000`, `npm run build` and `npm run lint` are all green.

## Docs to update

- **`docs/DATA_MODEL.md`:**
  - a `### \`alert_explanations\`` section after `alert_feedback` (`:421-450`): columns, the `kind` CHECK, the one-`created` partial unique index, "snapshot at create and escalate, similar incidents never stored", the `explanation_json` sections with `explanation_version`, and "Added by migration 6";
  - the ER diagram (`:14-37`) gains `alerts ||--o{ alert_explanations : "explained by"` and `readings |o--o{ alert_explanations : "triggered"`;
  - the diagram notes (`:39-55`) gain the sentence "`alert_explanations.reading_id` / `prediction_id` are nullable FKs naming the snapshot's own trigger";
  - Storage layout (`:516-552`) gains a migration 6 sentence ("purely additive, one `executescript`, like migration 2");
  - "Data flow" (`:451-482`) gains one line: create/escalate → snapshot → page/broadcast.
- **`README.md`:** a new "#### Why this alert? (alert explanations)" subsection after "Prediction feedback and field accuracy" (`:268-309`), before "### Docker compose" (`:310`). It covers:
  - the five sections;
  - snapshot vs reconstructed;
  - "probable cause, not a diagnosis";
  - the OOD banner;
  - synthetic demo labelling;
  - the route `GET /api/alerts/{id}/explanation` and the `/alerts/:id` deep link;
  - that key-factor weighting needs the model loaded.
- **`TODO.md`:**
  - checked items "Why this alert? backend (design/2026-10-07-alert-explanation-design.md)" and "Why this alert? frontend" after the prediction-feedback frontend item;
  - an unchecked follow-up: "Fix stale `vibration_h_high_band_energy_ratio` key in `kpi._vibration_severity` (`src/kpi/calculations.py:86`) and the Machine Detail metric list (`MachineDetail.tsx:31`)".
- **`IMPLEMENTATION_PLAN.md`:** `## M10 — Alert explanations` after M9 (`:69-78`).
- **`src/storage/migrations.py`:** a migration 6 docstring paragraph.
- **`src/prediction/pipeline.py`:** a docstring sentence on the snapshot.
- **`src/root_cause/rule_based.py`:** a docstring sentence on `explain_probable_cause` and the parity test.
- **`design/_integration_map.md`:** not edited. This doc supersedes §5 where they differ:
  - a separate many-rows-per-alert table, not columns or a UNIQUE single row;
  - the hook is in `fan_out`, so demo is covered;
  - `|ln ratio|` deviation;
  - the predictor is never loaded;
  - synthetic incidents are isolated.
- **`PROJECT_CONTEXT.md`:** not touched.

## Risks

- **Heuristic factors read as causal.** Global importance × local deviation is not a per-prediction attribution. Mitigations: the footnote states the method, the section is titled "Key factors" (not "causes"), the probable-cause wording rule, and the OOD banner.
- **Baseline mismatch with the live predictor.** `model_context` uses the machine's first 20 *stored* readings. After a restart, the live predictor's in-memory baseline is its first 20 snapshots since the restart (`rul_realtime.py:137-146`). Snapshots taken right after a restart can therefore rank factors against a different baseline than the model used.
  - Accepted: the stored baseline is stable and reproducible, and it is what an engineer would call "this machine's normal".
  - The `baseline_readings` count is shown.
- **Rolling statistics across the baseline/recent gap.** The `add_past_context` frame concatenates the first 20 and the last 60 readings, so rolling windows at the trigger only see the contiguous last 60. That is correct for windows ≤ 60 (`ROLLING_WINDOWS = (5, 20, 60)`). If training ever adds a longer window, `explain.py` must widen the recent slice. A test pins the slice to `max(ROLLING_WINDOWS)`.
- **Fan-out latency.** The snapshot adds about two indexed reads of ≤ 80 rows plus `add_past_context` on ≤ 80 × 20 columns before the email and broadcast. That is milliseconds, and runs only on create or escalate. Replay at high speed opens few alerts, because there is one open per machine.
- **Predictor cache coupling.** `loaded_predictor()` reaches into `predictions._cached_predictor` (lazy import, the mqtt_service precedent). If that cache is renamed, weighting silently becomes "unweighted". Test 23 pins the contract.
- **Test nondeterminism.** If some earlier test in the session loaded the real artifact into `_cached_predictor`, unpatched explain tests would see real importances. Every explain and route test therefore passes `predictor=` or monkeypatches `explain.loaded_predictor`.
- **Snapshot growth.** At most three rows per alert, each a few KB (30 readings × 6 floats, ≤ 6 factors). This is negligible next to `readings`. No retention policy.
- **`explanation_version` drift.** A future shape change bumps the version. Older snapshots then reconstruct (decision 4) rather than fail validation. They lose the captured evidence but stay viewable.
- **Unknown-feature alerts.** Batch-pipeline alerts (`db.insert_alerts`) have no links, and IMS-era rows have no `features_json` keys. They reconstruct from the nearest reading with `promoted_columns` or `insufficient_history`, which the UI labels clearly.
- **Accessibility of charts.** recharts' SVG is not exposed under jsdom. The trigger is announced via the `aria-label`, and the factor bars carry text equivalents, so no information is chart-only.

## Implementation notes (backend, 2026-10-07)

Where the built backend differs from, or pins down, the text above:

- **`triggering_readings.locate` gains `"snapshot_features"`** for the reading-less trigger of decision 5 (the predictions route). The original three values could not describe it truthfully: no reading id, and not a nearest-timestamp guess. The frontend should caption it "The feature vector the model scored (no stored reading)".
- **Key factors read the stored trigger's `features_json`**, not the snapshot's in-memory `features`, when the trigger is a stored reading; the two are the same vector on the replay and MQTT paths. `features` is used only for an unsaved trigger and for the probable-cause rule inputs (decision 8).
- **The top six factors may include `steady` ones** (score 0) when fewer than six channels moved; they are kept, as decision 6 ranks by score only, and the UI shows "≈ baseline".
- **Operating-condition labels** are "Shaft speed" (rpm) and "Radial load" (kN).
- **Escalated legacy alerts** are detected for the decision-4 note by comparing the located prediction's `health_state` with the alert's current one; when they differ the note "escalated after it opened … the opening evidence is shown" is added.
- **Probable cause with no trigger and no features** (a machine with no readings): `rule`, `checks` and `evaluated_label` stay null and a note says no reading was available, rather than reporting the `missing_kurtosis` rule for data that was never there.
- **`record_snapshot` stores the caller's `reading_id` / `prediction_id`** on the row as given (the evidence handed to fan_out), not the located trigger.
- **TODO.md**: the backend item is checked; the frontend item is added unchecked until the frontend half lands.

## Implementation notes (frontend, 2026-10-07)

- **Display rules live in `frontend/src/lib/explanationFormat.ts`** (value/ratio formatting, "↑ 3.1× baseline", rule-check sentences such as "Horizontal kurtosis 9.4 ≥ 5.0 — passed", RUL text, OOD-first warning order, `hasOutOfRange`) with their own unit tests, so the component files only export components.
- **Bar colours** come from a new `healthStyles.factorIntensity(share, direction)`: a factor's share of the top score on the degrading → faulty → critical ramp; a `steady` factor is neutral.
- **`locate: "snapshot_features"`** is captioned "The feature vector the model scored (no stored reading)", per the backend note.
- **The panel ignores the live event already current when it opens** (a stale `lastEvent` would otherwise trigger a second fetch on mount); later events about the alert, or a work order raised from it, re-fetch.
- **Machine Detail** passes `onExplain` to its `AlertsPanel` too, so each alert row there has "Why?" as well as the "Why this alert?" button for the selected alert.
- **`LiveEvent`** is unchanged (no new event types), so `describe()`, `NotificationBell` and `MachineDetailPage`'s machine-id refresh needed no change.
