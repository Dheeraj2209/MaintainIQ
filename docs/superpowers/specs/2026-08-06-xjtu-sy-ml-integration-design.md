# XJTU-SY ML Integration — Design Spec

**Date:** 2026-08-06
**Status:** Approved (design), pending implementation plan
**Supersedes for scope:** the NASA IMS classifier as the platform's primary model. `design/DESIGN_BASELINE.md` remains the canonical scope authority; this spec refines the "ML model selection / alert triggering / storage" rows of that baseline onto the XJTU-SY dataset.

---

## 1. Purpose & goal

Make the **XJTU-SY bearing RUL model the single model that powers the MaintainIQ platform end to end**: one explicit XJTU-SY-native data model, a real ingestion path that feeds the model, analytics and dashboards driven by the model's real output, first-class model observability (heartbeat + telemetry), automated ML reports, and frontend surfaces for all of it.

Today there are **two disconnected ML systems**: a legacy NASA IMS 4-class classifier that a batch script writes into SQLite (the dashboard reads those rows), and a newer XJTU-SY RUL model that is served at `POST /api/predictions/rul` but that **nothing in the frontend consumes** and whose state is in-memory only. This spec retires the first and makes the second the backbone.

### Success criteria

1. The database schema is explicitly XJTU-SY-shaped, and loading XJTU-SY produces **zero NULL columns** in `machines`/`readings`.
2. Every prediction shown in the UI originates from the XJTU-SY RUL model (via persisted rows), not the legacy classifier.
3. There is an explicit, documented ingestion path — a **streaming replay service** (live) plus a **batch backfill** (history) — both routed through the same `RealTimeRULPredictor`.
4. Model heartbeat and telemetry endpoints exist and are surfaced in the UI.
5. Three report types (per-machine prognostic, model-performance, fleet summary) can be generated, stored with an audit trail, and downloaded (Markdown/JSON).
6. `docs/DATA_MODEL.md` exists as the living reference for the data model, ingestion, storage, telemetry, and report catalog.
7. Backend `pytest` and frontend `Vitest` suites stay green; each integration seam passes a `code-review` gate before merge.

### Non-goals (this iteration)

- Real ESP32/MQTT hardware ingestion (M6). The streaming replay service is the honest stand-in, as the demo endpoint is today.
- PDF report rendering (Markdown/JSON now; PDF is a later add).
- Retraining automation / CI model gating (the training CLI stays manual).
- Acoustic/current sensor channels (XJTU-SY is vibration-only; out of scope).
- Migrating off SQLite.

---

## 2. Current state (ground truth, with file references)

| Area | Today |
| --- | --- |
| Primary model in UI | Legacy IMS `stage_classifier.joblib` (logistic regression, acc 0.796) via `src/prediction/ml_model.py`, written to SQLite by `src/training/run_pipeline.py`. |
| XJTU-SY model | `models/xjtu_rul_model.joblib`, served by `src/api/routes/predictions.py` → `src/prediction/rul_realtime.py` (`RealTimeRULPredictor`). **No frontend consumer.** In-memory state (`rul_realtime.py:21-27`). |
| Schema | Raw-SQL `SCHEMA` in `src/storage/db.py:32-126`; IMS-shaped `machines` (`source_test`,`bearing`) + `readings` (2-axis vibration + **synthetic** `temperature_c`). No RPM/load columns. Hand-rolled `ALTER` migrations in `init_schema()`. |
| Ingestion | Batch CSV loaders (`src/ingestion/ims_bearing.py`, `src/ingestion/xjtu_sy.py`); admin `POST /api/demo/simulate-fault` synthesizes readings and **bypasses ML** (`src/prediction/live.py`). No streaming. |
| Observability | `GET /api/health` returns `{"status":"ok"}`, does not check the model. Per-prediction diagnostics exist in the RUL response but are never logged/aggregated. |
| Reports | Static `models/xjtu_rul_evaluation.json`, `models/evaluation_report.json`. No generation. |
| Frontend | React 19 + Vite + Recharts; Analytics shows the **legacy** classifier metrics; no RUL surface. |

The XJTU-SY ingestion (`src/ingestion/xjtu_sy.py`) already produces the authoritative feature record shape: `bearing_id, condition, cycle, elapsed_minutes, rul_minutes, speed_rpm, load_kn, source_file`, plus `h_*/v_*/m_*` features and `cross_axis_rms_ratio`/`cross_axis_correlation`. Operating conditions are fixed per condition (`xjtu_sy.py:21-25`): C1 2100 rpm/12 kN, C2 2250 rpm/11 kN, C3 2400 rpm/10 kN.

The RUL predictor already returns a rich contract (`rul_realtime.py:215-231`, `src/api/schemas.py:159-174`): `predicted_rul_minutes/_hours`, `rul_estimate_kind`, `failure_within_horizon_probability`, `prediction_interval_90_minutes`, `health_state`, `model_version`, `history_snapshots`, `out_of_distribution`, `outside_training_features`, `warnings`.

---

## 3. Architecture & data flow

```
XJTU-SY raw snapshots (per-minute dual-axis CSVs @ 25.6 kHz)
   │
   ├─ BATCH BACKFILL (src/ingestion/backfill.py)
   │     per bearing, replay in cycle order through a per-machine predictor
   │     ─► persist readings + predictions + model_inference_log
   │        (populates history so the dashboard opens non-empty)
   │
   └─ STREAMING REPLAY SERVICE (src/ingestion/replay_service.py)
         async task; plays chosen bearings snapshot-by-snapshot at cadence
         ─► RealTimeRULPredictor.predict
              ├─► persist reading + prediction + model_inference_log
              ├─► alert state machine (src/alerts/live.py)
              │        ─► email notifications (src/notifications/)
              └─► WebSocket broadcast (src/realtime/manager.py)

   FastAPI reads persisted rows ─► Dashboard / Analytics / Model Health / Reports
```

**Key invariant:** batch and streaming both call the same `RealTimeRULPredictor`, so a backfilled row and a live row are byte-compatible in `predictions`. There is exactly one inference code path.

---

## 4. Canonical XJTU-SY data model

SQLite remains the store. Column types follow the existing raw-SQL convention (`src/storage/db.py`). All timestamps are ISO-8601 TEXT.

### 4.1 `machines` (reshaped)

| Column | Type | Notes |
| --- | --- | --- |
| `machine_id` | TEXT PK | e.g. `Bearing1_1` |
| `bearing_id` | TEXT | same as `machine_id` for XJTU; kept explicit for clarity |
| `operating_condition` | INTEGER | 1–3 |
| `speed_rpm` | REAL | from operating condition (never NULL for XJTU) |
| `load_kn` | REAL | from operating condition (never NULL for XJTU) |
| `dataset` | TEXT NOT NULL DEFAULT `'xjtu_sy'` | **isolation seam**; NASA-validation machines tag `'nasa_ims'` |
| `is_documented_failure` | INTEGER | XJTU bearings are run-to-failure |
| `created_at` | TEXT NOT NULL | |

**Dropped:** `source_test` (IMS-specific).

### 4.2 `readings` (XJTU-native, explicit columns)

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `machine_id` | TEXT NOT NULL FK→machines | |
| `timestamp` | TEXT NOT NULL | synthesized from `cycle` for replay/backfill; wall-clock for live |
| `cycle` | INTEGER NOT NULL | snapshot index (0-based) |
| `elapsed_minutes` | REAL NOT NULL | `cycle * 1.0` |
| `speed_rpm` | REAL NOT NULL | |
| `load_kn` | REAL NOT NULL | |
| `sample_rate_hz` | REAL NOT NULL | 25600 |
| `vibration_h_rms` | REAL NOT NULL | promoted for query/sort |
| `vibration_h_kurtosis` | REAL NOT NULL | |
| `vibration_v_rms` | REAL NOT NULL | |
| `vibration_v_kurtosis` | REAL NOT NULL | |
| `cross_axis_rms_ratio` | REAL NOT NULL | |
| `cross_axis_correlation` | REAL NOT NULL | |
| `rul_minutes` | REAL | label from remaining snapshots; NULL only for live rows with unknown future |
| `features_json` | TEXT NOT NULL | full 200+ feature vector (the model's true input) |
| `dataset` | TEXT NOT NULL DEFAULT `'xjtu_sy'` | |
| — | | UNIQUE(`machine_id`, `cycle`) |

**Dropped:** `temperature_c`, `temperature_is_synthetic`, `rul_hours`, `sensor_id` (XJTU has no thermal channel, no per-sensor id; `rul_hours` is derivable from `rul_minutes`). Removing synthetic temperature keeps the schema honest and null-free (decision confirmed with the product owner).

`rul_minutes` is the single labelled target. It is NOT NULL for backfilled/labelled XJTU rows; it is nullable only to accommodate live rows whose true remaining life is not yet known — those rows carry the model's *predicted* RUL in `predictions`, never a fabricated label in `readings`.

### 4.3 `predictions` (extended)

Existing columns kept: `id, reading_id, machine_id, timestamp, health_state, confidence, source, model_name, probable_cause, created_at`.

Added (mirror of the RUL model output):

| Column | Type |
| --- | --- |
| `predicted_rul_minutes` | REAL |
| `rul_estimate_kind` | TEXT (`point_estimate`\|`lower_bound`) |
| `failure_within_horizon_probability` | REAL |
| `prognostic_horizon_minutes` | REAL |
| `prediction_interval_low` | REAL |
| `prediction_interval_high` | REAL |
| `model_version` | TEXT |
| `out_of_distribution` | INTEGER |
| `history_snapshots` | INTEGER |
| `warnings_json` | TEXT |

`source` becomes `'xjtu_rul'` for model rows (legacy `'ml'`/`'rule_based'` rows are removed with the IMS retirement). `probable_cause` is still populated by `src/root_cause/rule_based.py`.

### 4.4 `model_inference_log` (NEW — audit + telemetry source)

One row per inference (backfill and live).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `machine_id` | TEXT NOT NULL | |
| `timestamp` | TEXT NOT NULL | inference time |
| `model_version` | TEXT NOT NULL | |
| `latency_ms` | REAL NOT NULL | wall-clock of `.predict()` |
| `failure_probability` | REAL | |
| `predicted_rul_minutes` | REAL | |
| `out_of_distribution` | INTEGER NOT NULL DEFAULT 0 | |
| `warming_up` | INTEGER NOT NULL DEFAULT 0 | |
| `warnings_count` | INTEGER NOT NULL DEFAULT 0 | |
| `status` | TEXT NOT NULL DEFAULT `'ok'` | `ok`\|`error` |
| `error_message` | TEXT | |
| `created_at` | TEXT NOT NULL | |

Index: `(model_version, timestamp)` and `(machine_id, timestamp)`.

### 4.5 `model_registry` (NEW)

| Column | Type |
| --- | --- |
| `model_version` | TEXT PK |
| `artifact_path` | TEXT NOT NULL (relative, never absolute) |
| `algorithm` | TEXT |
| `trained_at` | TEXT |
| `metrics_json` | TEXT (headline eval metrics) |
| `deployed_at` | TEXT |
| `is_active` | INTEGER NOT NULL DEFAULT 0 |

Fixes today's problems: `model_version` is a bare timestamp (`xjtu_rul.py:255`) and `xjtu_rul_evaluation.json` embeds a foreign absolute path.

### 4.6 `reports` (NEW)

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `report_type` | TEXT NOT NULL | `machine_prognostic`\|`model_performance`\|`fleet_summary` |
| `scope` | TEXT NOT NULL | `machine_id` or `'fleet'` |
| `format` | TEXT NOT NULL | `markdown`\|`json` |
| `period_start` | TEXT | |
| `period_end` | TEXT | |
| `generated_at` | TEXT NOT NULL | |
| `generated_by` | INTEGER FK→users(id) | |
| `content` | TEXT NOT NULL | rendered report body |
| `summary_json` | TEXT | machine-readable headline for list views |

### 4.7 Unchanged tables

`alerts`, `maintenance_records`, `users`, `notifications` are unchanged in shape. `alerts` already has `acknowledged_at`/`acknowledged_by`.

### 4.8 Migration & schema versioning

Replace the try/except `ALTER` pattern with an explicit, ordered migration runner backed by a `schema_version` table (`version INTEGER PK, applied_at TEXT`). Each migration is a numbered idempotent step. Because the data model changes are substantial and the committed `maintainiq.db` is stale IMS data, the DB is **regenerated from XJTU-SY** via the backfill rather than in-place migrated; the migration runner exists for forward changes from this new baseline. IMS artifacts move to `archive/`.

---

## 5. Ingestion & storage (explicit)

### 5.1 Batch backfill — `src/ingestion/backfill.py`

For each selected bearing: read snapshots in cycle order, run each through a **per-machine** `RealTimeRULPredictor` instance (so rolling/causal context is built exactly as live), and persist `readings` + `predictions` + `model_inference_log`. Timestamps are synthesized from `cycle` at 1-minute spacing anchored to a configurable start. Idempotent via `UNIQUE(machine_id, cycle)`.

### 5.2 Streaming replay service — `src/ingestion/replay_service.py`

An async background task (managed on the FastAPI event loop) that plays chosen bearings snapshot-by-snapshot at a configurable cadence:
- **cadence:** real-time (1 snapshot/min) or accelerated `Nx` for demos.
- **per snapshot:** `RealTimeRULPredictor.predict` → persist reading+prediction+inference-log → `apply_reading` alert state machine → email notify → WS broadcast.
- **control endpoints (admin):** `POST /api/ingestion/replay/start` (bearings, speed), `POST /api/ingestion/replay/stop`, `GET /api/ingestion/replay/status`.

This is the honest M6 stand-in and **supersedes `simulate-fault` as the primary live feed**; `simulate-fault` is retained only as a quick single-shot smoke trigger.

### 5.3 Restart-safety

On first `predict` for a machine after process start, the predictor seeds its rolling context from the most recent persisted `readings` rows for that machine (bounded by `max_history`), closing the in-memory-only gap (`rul_realtime.py:21-27`).

### 5.4 Storage summary

SQLite `maintainiq.db`. Time-series in `readings`; model output in `predictions`; per-inference audit in `model_inference_log`. Raw waveforms are not stored (only extracted features, consistent with today). Trained artifacts in `models/`, registered in `model_registry`.

---

## 6. Model observability

### 6.1 Heartbeat — `GET /api/model/health`

```
{
  "loaded": true,
  "model_version": "...",
  "artifact_path": "models/xjtu_rul_model.joblib",
  "trained_at": "...",
  "last_inference_at": "...",
  "seconds_since_last_inference": 12.4,
  "stale": false,                       // > configurable threshold since last inference
  "machines_warming_up": ["Bearing2_3"]
}
```

Returns `loaded: false` (HTTP 200 with a degraded body, plus a non-ok summary) when the artifact is missing, so the frontend can render a clear "model down" state rather than a generic 503. The generic `GET /api/health` gains an optional `model` sub-field sourced from this.

### 6.2 Telemetry — `GET /api/model/telemetry?window=...`

Aggregates `model_inference_log` over the window: inference volume, latency p50/p95, error rate, OOD rate, warming-up rate, failure-probability distribution (histogram/percentiles), and a **drift indicator** = rolling fraction of inferences flagged `out_of_distribution` (built on the model's existing `feature_bounds_99pct` mechanism). No new statistical model is introduced; drift reuses the model's own OOD signal aggregated over time.

---

## 7. Reports subsystem

`src/reports/` with one generator module per type, each producing **Markdown + JSON**, stored in `reports` with `generated_at` + `generated_by` (audit trail).

| Type | Contents | Source |
| --- | --- | --- |
| `machine_prognostic` | current RUL + 90% interval + confidence, health-state trajectory (last N), recent alerts, probable cause, recommended maintenance window | `predictions`, `alerts`, `readings` |
| `model_performance` | inference volume/latency/error/OOD/drift + eval metrics from `model_registry.metrics_json` | `model_inference_log`, `model_registry` |
| `fleet_summary` | fleet health distribution, top at-risk by predicted RUL, alert/maintenance counts for period | `machines`, `predictions`, `alerts`, `maintenance_records` |

Endpoints: `POST /api/reports` (generate: type, scope, period, format), `GET /api/reports` (list, filterable), `GET /api/reports/{id}`, `GET /api/reports/{id}/download` (sets content-disposition; `text/markdown` or `application/json`). RBAC: operators can generate/read `machine_prognostic`; `model_performance` and `fleet_summary` are admin/supervisor.

---

## 8. Frontend surfaces

Stack unchanged (React 19 + TS + Vite + Tailwind v4 + Recharts). `frontend/src/api/types.ts` (hand-maintained mirror) and `client.ts` extended for every new endpoint.

| Surface | Change |
| --- | --- |
| `MachineDetail` | Add a **Prognostics card**: predicted RUL + 90% interval + confidence, health-state, `warming_up`/`out_of_distribution` badges; RUL trend chart; "Generate report" button. Remove temperature metric. |
| `AnalyticsPage` | Replace legacy classifier metrics with XJTU RUL metrics (precision/recall/F1/ROC-AUC/RUL-MAE from `model_registry`) + live telemetry tiles; at-risk ranking by predicted RUL. |
| **Model Health page (NEW)** | admin/supervisor: heartbeat status, telemetry charts (latency, volume, OOD/drift, error rate). |
| **Reports page (NEW)** | list / generate (type+scope+period) / download. |
| `DashboardPage` | Fleet pulse driven by real RUL health states; live updates via existing WS on replay events. |
| **Ingestion control (NEW, admin)** | start/stop/status of the replay stream; bearing + cadence picker (extends/replaces `DemoPage`). |

WebSocket event types extend the existing set with replay/ingestion status events; pages continue the re-fetch-on-event pattern.

---

## 9. Legacy IMS retirement

- Move to `archive/`: `src/ingestion/ims_bearing.py`, `src/prediction/ml_model.py` (stage classifier loader), `src/prediction/router.py`'s IMS branch, `research/ims_bearing_baseline/` stays as-is (already research-only).
- `stage_classifier.joblib` / `evaluation_report.json` retained under `models/` but no longer read by the live API.
- A thin **NASA-validation adapter** (future, optional) can map IMS features into the canonical schema tagged `dataset='nasa_ims'`; IMS's absent RPM/load are the only NULL-bearing case and are quarantined by the `dataset` column.
- `run_pipeline.py` is repurposed/retired in favor of `backfill.py` (or kept as a thin wrapper that calls it).

---

## 10. Testing & integration discipline

- **Backend:** `pytest` with `TestClient` + `dependency_overrides` against a temp SQLite DB (existing pattern). New modules built test-first where practical (migrations, backfill, telemetry aggregation, report generators, endpoints).
- **Frontend:** Vitest + RTL + MSW, red→green per component (existing pattern). Respect existing test constraints (charts need `role="img"`+aria-label; KpiCards label-as-direct-child; `.panel-notch` on Analytics cards).
- **Code-review gates (per product-owner instruction):** each integration seam runs the `code-review` skill on the increment; findings are addressed before merge. Seams:
  1. schema + migration runner ↔ ingestion writers
  2. ingestion (backfill + replay) ↔ `RealTimeRULPredictor`
  3. predictor/persistence ↔ API (`predictions`, `model/health`, `model/telemetry`)
  4. API ↔ frontend (types/client/pages)
  5. reports module ↔ API ↔ frontend
  6. legacy IMS retirement (ensure nothing live still imports archived code)

---

## 11. Documentation deliverables

1. **This spec** — design of record.
2. **`docs/DATA_MODEL.md`** (living reference, produced during implementation): ER diagram, table-by-table columns, data-flow diagram, ingestion modes (streaming vs batch), storage layout, model heartbeat/telemetry contract, report catalog. This is the "document explaining the data model" the product owner requested.
3. Update `design/DESIGN_BASELINE.md`'s ML/storage rows to point at this spec; update `README.md` run instructions (backfill + replay service).

---

## 12. Open risks & honest limitations (carried from the model card)

- The XJTU-SY model is trained on 15 accelerated-life lab bearings; it is a research prototype, **not field-validated** on the fans/pumps the product ultimately targets. All UI surfaces must keep the `out_of_distribution` warning visible and honest.
- SQLite single-writer concurrency is fine for replay-at-demo-scale but is a known ceiling under real multi-machine live load.
- Drift detection reuses the model's OOD bounds; it is a distributional tripwire, not a full drift-analytics suite.
