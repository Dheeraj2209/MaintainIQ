# Implementation Plan

Build order for the Smart Predictive Maintenance Decision-Support System, translating `design/SRS_Document.md` and `design/DESIGN_BASELINE.md` into milestones. Each milestone should be demoable end-to-end before moving to the next.

## M1 — Dataset ingestion and feature pipeline
- Load a stored/simulated vibration + temperature dataset covering multiple machines.
- Tag each record with timestamp, machine ID, sensor ID.
- Preprocess: clean missing/invalid readings, normalize, window the signal.
- Extract features: mean, std, peak, RMS, optional FFT for vibration; current value, rolling average, rate of increase for temperature.
- Output: a feature table per machine per time window, ready for classification.

## M2a — Rule-based baseline
- Threshold/heuristic classifier mapping feature values to health state (healthy, degrading, faulty, critical).
- Rule-based root cause module mapping abnormal feature patterns to probable causes (bearing wear, misalignment, imbalance, overheating, excessive load, looseness, sensor/data-quality issue).
- This stays in the system permanently as the fallback path, not just a placeholder to delete later.

## M2b — Candidate ML model training and selection
- Offline training script using the M1 feature tables.
- Train multiple candidate models on the same train/test split: Random Forest, Gradient Boosting (XGBoost/LightGBM), SVM, Logistic Regression (baseline), Isolation Forest (unsupervised anomaly detection for when fault labels are scarce).
- Evaluate each on the SRS's own KPIs: prediction accuracy, false alarm count, missed fault count, confidence score.
- Export the winning model artifact (joblib/ONNX) plus its evaluation report.
- Note: a raw-signal deep model (1D-CNN/LSTM) is a documented future option, not part of this milestone.

## M3 — Storage and alert generation
- SQLite schema for: raw/processed readings, feature windows, predictions, alerts, maintenance records.
- Prediction routing: use the deployed ML model if available and confident; fall back to the M2a rule-based classifier otherwise.
- Alert generation from whichever prediction path was used, including severity, probable root cause, and duplicate-alert suppression for unresolved issues.

## M4 — Backend API and dashboard
- FastAPI service exposing machine status, trends, alerts, root cause, and KPIs.
- Web dashboard: current health per machine, vibration/temperature trend graphs, alert list, severity, root cause, combined risk score, KPI summary.

## M5 — Maintenance history
- Dashboard form/API for an operator to log a completed maintenance action and date.
- Wire last-maintenance-date and days-since-maintenance into the dashboard and into maintenance-priority/KPI calculations.

## M6 — Live telemetry over MQTT (in progress)
Contract: `design/M6_LIVE_TELEMETRY.md` (topics, payloads, tables, env vars, API, KPI formulas). Replaces the M1 dataset input with live telemetry; the rest of the pipeline (M2–M5) is unchanged — every live snapshot goes through `src/prediction/pipeline.py`, the same path as replay and `/demo`.

Software-only path (no hardware needed):
- Mosquitto broker in `docker-compose.yml` (`deploy/mosquitto/mosquitto.conf`; `just broker`).
- `src/telemetry/`: wire protocol + settings (done), MQTT ingest service writing `telemetry_messages` / `device_status` (storage migration 2, done) and feeding the pipeline, downstream alert topic.
- Simulator (`python -m src.telemetry.simulator`, `just simulate`): N simulated nodes with real MQTT clients, LWT, a bounded drop-oldest edge buffer, and injected network outages / loss. Replaces `/demo` as the primary live demo source (`/demo` stays as a manual tool).
- System KPIs (`sensor_collection_rate`, `transmission_success_rate`, `edge_buffer_health`, `cloud_sync_health`) computed from live telemetry instead of `not_applicable`.

Hardware path:
- `firmware/esp32-node/` (PlatformIO): ESP32 + ADXL345 over SPI, LittleFS edge buffer flushed oldest-first on reconnect, MQTT over Wi-Fi upstream, alert topic downstream driving a status LED — per `design/diagrams/communication_protocol_comparison.puml` and `design/diagrams/system_architecture_diagram.puml`.
- Known limitation: the ADXL345's 3.2 kHz ceiling is far below the 25.6 kHz the RUL model was trained on, so live ADXL345 features are typically flagged out-of-distribution; production nodes need a wide-band sensor. The original DS18B20 temperature channel is not part of M6 (the shipped model is vibration-only).

## M7 — Device health
Design: `design/2026-10-06-device-health-design.md` (contract addendum: `design/M6_LIVE_TELEMETRY.md` §12). "Show the status of each sensor node, and alert when one goes silent."

- One node-state rule (`online` / `stale` / `offline` / `never_reported`) in `src/telemetry/device_health.py`, shared by ingest, the devices route and the system KPIs.
- `device_incidents` table (storage migration 3), one row per silence episode; `notifications.device_incident_id` links pages to it.
- Generic in-process background scheduler (`src/background/scheduler.py`, off unless `MAINTAINIQ_SWEEP_INTERVAL_S > 0`, reused by later paging escalation) running the silence watchdog (`src/telemetry/watchdog.py`): opens/resolves incidents only while MQTT ingest is connected past `DEVICE_SILENT_GRACE_S`, emails admins + supervisors on open, broadcasts `device_offline` / `device_online`.
- API: `GET /api/telemetry/devices[/{id}]` with state fields, `GET /api/telemetry/incidents`, `POST /api/telemetry/incidents/{id}/acknowledge` (all roles), `device_watchdog` block on `/api/telemetry/status`.
- Dashboard: Devices page under Monitor for every role, node-status chip on machine detail, `LiveEvent` as a discriminated union with toasts for the two device events.

## M8 — Work orders + escalation
Design: `design/2026-10-07-work-orders-escalation-design.md`. "Turn alerts into tracked repair jobs, and escalate unacknowledged alerts to higher roles."

- `work_orders` + `work_order_events` tables and `alerts.page_level` / `last_paged_at` (storage migration 4). `migrations.ensure_current_schema` upgrades a stale versioned file on the first request (`get_db`) and on the paging job's first tick.
- `src/work_orders/service.py`: strict status machine (`open → assigned → in_progress → done`, cancel from any non-terminal state), RBAC in the service, one active order per alert (partial unique index, 409), creation acknowledges the alert, completion writes a maintenance record via `log_maintenance`.
- Paging ladder (`src/alerts/paging.py`, scheduler job `alert_paging`): supervisors after `ESCALATION_L1_MINUTES`, admins after `ESCALATION_L2_MINUTES`, compare-and-set under the alert lock, `alert_paged` event. `notify_alert` gains `roles` / `page_level`.
- `pipeline.fan_out` shared by model predictions and the demo routes (level-0 email, `last_paged_at` stamp, broadcast).
- API: `/api/work-orders` (list, detail, assignees, create, patch, assign, start, complete, cancel) and `POST /api/alerts/{id}/work-order`; alerts carry `page_level`, `last_paged_at`, `active_work_order_id`; KPIs gain open-work-order counts and mean completion time.
- Dashboard: Work Orders page with detail drawer and timeline, create actions on Alerts and Machine Detail, paging badges, open-work-order KPI card.

## M9 — Prediction feedback
Design: `design/2026-10-07-prediction-feedback-design.md`. "When an alert is closed, record what actually happened, then use it to measure real-world model accuracy and for retraining."

- `alert_feedback` (one editable outcome row per alert) and `alerts.prediction_id` / `reading_id` / `model_version` / `closed_by` (storage migration 5). The links are written when an alert opens and kept through escalation; `persist_prediction`'s id is threaded through `pipeline.handle_prediction` from the prediction route, replay and live MQTT ingest.
- `src/feedback/service.py`: close = human resolve + acknowledge + feedback upsert in one transaction under the alert lock (idempotent on an already-resolved alert); create by any role, replace by recorder/admin/supervisor; validation of outcome, cause, failure time and work order.
- `src/feedback/accuracy.py`: precision, false-alarm rate, lead time vs. horizon, RUL error on point estimates only (lower bounds censored), root-cause accuracy, per model version, offline benchmark beside it; demo alerts excluded. Wired into the KPI `prediction` block (`real_world`, `root_cause_accuracy`) and the `model_performance` report.
- `src/feedback/export.py`: confirmed-failure episodes from live readings as a `build_feature_table`-shaped CSV, merged onto the XJTU-SY table for a manual `xjtu_rul --features-csv` retrain.
- API: `POST /api/alerts/{id}/close`, `PUT|GET /api/alerts/{id}/feedback`, `GET /api/model/feedback-accuracy`, `GET /api/model/feedback/export`; realtime `alert_closed` / `alert_feedback_recorded` (not emailed; a close reaches devices as `alert_resolved`).
- Dashboard: close/outcome dialog from Alerts, Machine Detail and work-order completion, outcome badges, field-accuracy card and CSV export on the Model page.

## M10 — Alert explanations
Design: `design/2026-10-07-alert-explanation-design.md`. ""Why this alert?": explain each alert with the triggering readings, key factors, probable cause and similar past incidents."

- `alert_explanations` (one `created` row per alert plus one per severity escalation; storage migration 6), written by `pipeline.fan_out` before paging and broadcast, inside a try/except so it never breaks the alert path. Demo alerts are covered because the demo routes share `fan_out`.
- `src/root_cause/explain.py`: triggering readings (30-reading window, vibration channels only), key factors (`add_past_context` baseline ratios vs. the machine's first 20 readings × the loaded model's `feature_importances_`, operating conditions excluded, training-bounds flags; promoted-column fallback), prediction block with OOD, probable cause with the rule trace from `rule_based.explain_probable_cause`, and similar incidents joined to feedback, work orders and maintenance. Reads the ML modules only; never loads the model.
- API: `GET /api/alerts/{id}/explanation` (any role) serves the latest snapshot, else reconstructs from stored data without writing; similar incidents are always computed at request time.
- Dashboard: AlertExplanationPanel drawer and `/alerts/:id` deep link, "Why?" on Alerts, dashboard alerts and Machine Detail.

## M11 — Mobile operator view (PWA)
Design: `design/2026-10-07-mobile-operator-pwa-design.md`. "Mobile operator view: installable, with push notifications, quick alert actions and QR access to machines."

- Storage migration 7: `push_subscriptions` (one row per browser push endpoint, rebound to whoever subscribed last; unsubscribe deletes, a 404/410 deactivates) and a guarded `notifications.channel` column (`'email' | 'push'`, default `'email'`).
- `src/notifications/push.py`: VAPID keys from env (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`), push off and reported as `enabled: false` without them, https + push-service host allowlist (`PUSH_ALLOWED_HOSTS`), synchronous sends from worker threads only, one `notifications` row per attempt, `python -m src.notifications.push --generate-vapid`.
- Dispatch audience: email unchanged; push goes to admins, supervisors and operators at level 0 and for sensor-node silence, and to the ladder roles at levels 1-2. No push for resolutions.
- API: `GET /api/push/vapid-public-key`, `POST`/`DELETE /api/push/subscribe`, `POST /api/push/test` (every role, bound to the caller); `?channel=` on `GET /api/notifications`. The SPA catch-all serves `sw.js` and `manifest.webmanifest` with explicit MIME types and `no-cache`, guards path traversal and returns a JSON 404 for unknown `/api` paths.
- PWA files: manifest (`start_url /m`), hand-written service worker (never caches `/api` or `/ws`; push and notificationclick deep-link to `/m/alerts/:id`), generated PNG icons.
- Mobile UI: `/m` shell with bottom tabs (Alerts, Scan, Machines, Settings), quick alert actions (optimistic acknowledge and work order, close dialog, "Why?"), push toggle, login preserving the deep link.
- QR: printable machine labels on Machine Detail encoding `<origin>/m/machines/{id}`; `/m/scan` with BarcodeDetector or manual entry, same-origin labels only.
- Responsive desktop AppShell (collapsible sidebar) with a link to, and a dismissible suggestion for, the mobile view.

## Suggested `src/` layout

```
src/
  ingestion/       # M1: dataset/telemetry loading, tagging
  preprocessing/    # M1: cleaning, normalization, windowing
  features/         # M1: feature extraction
  prediction/
    rule_based.py   # M2a
    ml_model.py      # M2b: loads deployed model artifact
    router.py        # M3: ML-first, rule-based fallback
  training/          # M2b: candidate model registry, training runner, evaluation/leaderboard, export
  root_cause/        # M2a/M2b: root cause logic
  alerts/            # M3: alert generation, duplicate suppression
  storage/           # M3: SQLite schema and access
  maintenance/        # M5: maintenance log entry and priority logic
  kpi/                # M4/M5: KPI calculations
  api/                # M4: FastAPI service
  dashboard/          # M4: web dashboard
```
