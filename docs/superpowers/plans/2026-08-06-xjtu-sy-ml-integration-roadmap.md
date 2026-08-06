# XJTU-SY ML Integration — Implementation Roadmap

> **For agentic workers:** This is the **master roadmap**. It sequences the work
> into eight phases with hard dependency ordering, a code-review gate between
> every phase, and per-phase file maps / interfaces / acceptance criteria.
>
> Each phase is expanded into its own full TDD plan file
> (`docs/superpowers/plans/2026-08-06-phase-N-<slug>-plan.md`) **at the moment
> that phase starts** — not up front. Rationale: every phase consumes the
> *concrete committed code* of the phase before it (exact column names, function
> signatures, response shapes). Writing literal TDD code for phase 6 before
> phase 2 lands would be speculation that drifts. The roadmap is stable; the
> per-phase plans are authored just-in-time against real code.
>
> REQUIRED SUB-SKILL when expanding any phase into its plan: `superpowers:writing-plans`.
> REQUIRED SUB-SKILL when executing a phase plan: `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans`.

**Goal:** Make the XJTU-SY bearing RUL model the single, explicit, observable
backbone of MaintainIQ — one canonical dataset schema, explicit streaming +
batch ingestion, persisted inference with heartbeat/telemetry/reports, and a
frontend that shows analytics on real ingested data — retiring the legacy NASA
IMS classifier that currently powers the UI.

**Architecture:** FastAPI + SQLite (raw SQL, hand-rolled migrations) serve a
`RealTimeRULPredictor` (ExtraTrees classifier→regressor cascade) whose feature
history is persisted so restarts don't lose context. Ingestion is a streaming
replay service plus a batch backfill, both routed through the one predictor.
Every inference writes an audit/telemetry row; health, telemetry, and reports
are exposed as endpoints and rendered by the React 19 + Vite + Tailwind v4 +
Recharts frontend.

**Tech Stack:** Python 3 · FastAPI · SQLite (no ORM) · scikit-learn (ExtraTrees) ·
joblib · numpy/pandas/scipy · pytest + TestClient · React 19 · TypeScript · Vite ·
Tailwind v4 · Recharts · Vitest + React Testing Library + MSW.

**Design authority:** `docs/superpowers/specs/2026-08-06-xjtu-sy-ml-integration-design.md`
(approved as-is, temperature dropped). This roadmap operationalizes that spec;
if the two conflict, the spec wins and this file is corrected.

---

## Global Constraints

Every task in every phase implicitly includes these. Copied from the spec.

- **Canonical dataset is XJTU-SY.** No NASA/IMS assumptions in schema, model, or
  UI. NASA IMS is *archived*, re-introduced only later behind a `dataset` column
  as a validation-only adapter. XJTU rows populate every column with zero NULLs.
- **Drop synthetic temperature entirely.** No `temperature_c`,
  `temperature_is_synthetic`, or temperature severity anywhere. XJTU has no
  thermal channel; the schema must stay honest.
- **RUL is in minutes.** `rul_minutes` is the unit of record; drop `rul_hours`
  from storage (hours may still be *derived* in a response for display).
- **Time is `elapsed_minutes` + `cycle`.** `cycle` is the per-machine snapshot
  index; `UNIQUE(machine_id, cycle)` is the idempotency key for ingestion.
- **No new heavyweight deps.** scikit-learn/joblib/numpy/pandas/scipy only for
  ML; no PyTorch/TF. No new frontend chart lib beyond Recharts.
- **Model artifact paths are stored relative** to the repo root in
  `model_registry.artifact_path`; never absolute.
- **RBAC preserved:** admin/supervisor/operator. Report generation and ingestion
  control are supervisor+; read endpoints follow existing auth rules.
- **TDD, DRY, YAGNI, frequent commits.** Failing test first, minimal code,
  green, commit. One deliverable per task.
- **Feature extraction has exactly one home:** `src.ingestion.xjtu_sy.extract_snapshot_features`.
  Offline (`build_feature_table`) and online (`RealTimeRULPredictor.predict`)
  paths must both call it. Never fork the feature math.
- **Code-review gate between phases (user mandate, verbatim):** "before
  integrating any two modules use the code review plugin or any relevant plugins
  then review then integrate them." Each phase ends with a `code-review` pass on
  that phase's diff before the next phase begins. A phase is not "done" until its
  review findings are resolved or explicitly deferred with a reason.

---

## Dependency Graph

```
Phase 1  Canonical schema + migration runner  (foundation; touches everything)
   │
   ▼
Phase 2  Batch backfill ingestion → readings  (needs the new schema)
   │
   ▼
Phase 3  Model wiring: persisted predictor + inference log + model_registry
   │        (needs readings to replay from; produces predictions rows)
   ├───────────────┬───────────────┐
   ▼               ▼               ▼
Phase 4         Phase 5         Phase 6
Streaming       Observability   Reports subsystem
replay service  (health +       (per-machine / fleet /
+ control       telemetry       model-performance /
endpoints       endpoints)      downloadable export)
   └───────────────┴───────────────┘
                   │
                   ▼
Phase 7  Frontend surfaces (consumes phases 3–6 endpoints)
                   │
                   ▼
Phase 8  Documentation: DATA_MODEL.md + baseline/README reconciliation
```

Phases 4, 5, 6 are mutually independent once Phase 3 lands and MAY be built in
any order or in parallel worktrees. Phase 7 needs all of 3–6. Phase 8 is last so
it documents what actually shipped.

---

## Ripple-Effect Register (read before Phase 1)

The schema change is not local. These existing readers reference columns the
canonical schema removes or renames; each must be updated in the phase noted, or
the test suite goes red. This register exists so no consumer is missed.

| Consumer | Current dependency | Breaks because | Fixed in |
|---|---|---|---|
| `src/kpi/calculations.py` `_temperature_severity`, `_machine_health` | reads `temperature_c`; returns `temperature_severity` | column dropped | Phase 1 |
| `src/api/schemas.py` `MachineSummary.temperature_severity` | required field | field dropped | Phase 1 |
| `src/api/routes/machines.py` `_TREND_COLUMNS["temperature_c"]` | trend metric | column dropped | Phase 1 |
| `src/kpi/calculations.py` health/risk (`_RISK_BY_STATE`, `_abnormal_event_count`) | `predictions.health_state`, `source` | source becomes `xjtu_rul`; IMS states gone | Phase 3 |
| any reader of `machines.source_test` | `source_test` column | dropped for `dataset` | Phase 1 |
| any reader of `readings.rul_hours` / `sensor_id` | those columns | dropped | Phase 1 |
| `src/ingestion/ims_bearing.py`, `src/prediction/ml_model.py`, `src/prediction/live.py` (legacy classifier path) | old schema + IMS model | retired/quarantined | Phase 1 (archive) / Phase 3 (unwire) |
| frontend fields `temperature_severity`, temperature trend option | API shape | fields gone | Phase 7 |

**Discovery step (do this first, in Phase 1 Task 0):** grep the repo for each
removed token (`temperature_c`, `temperature_is_synthetic`, `temperature_severity`,
`rul_hours`, `source_test`, `sensor_id`) and turn every hit into a checklist item
in the Phase 1 plan. Do not trust this table to be exhaustive — regenerate it
from `grep` at plan-authoring time.

---

## Phase 1 — Canonical Schema & Migration Runner

**Goal:** The database matches the XJTU-SY canonical model; a versioned migration
runner upgrades existing DBs in place; the full existing test suite is green
against the new schema with temperature/legacy columns removed.

**Files:**
- Create: `src/storage/migrations.py` — versioned migration runner + `schema_version` table.
- Modify: `src/storage/db.py` — replace `SCHEMA`, `init_schema()`, and the
  `insert_*` helpers to match the canonical model below.
- Modify: `src/kpi/calculations.py` — remove `_temperature_severity` and the
  `temperature_severity` key; drop temperature from `_machine_health`.
- Modify: `src/api/schemas.py` — remove `MachineSummary.temperature_severity`.
- Modify: `src/api/routes/machines.py` — remove `temperature_c` from `_TREND_COLUMNS`.
- Archive: move `src/ingestion/ims_bearing.py`, `src/prediction/ml_model.py`,
  and the legacy IMS classifier path in `src/prediction/live.py` under
  `src/legacy/` (import-guarded, not deleted — needed later for NASA validation).
- Test: `tests/storage/test_migrations.py`, and update every fixture in
  `tests/conftest.py` / dataset seeders that build rows with removed columns.

**Interfaces:**
- Produces:
  - `src.storage.migrations.run_migrations(conn) -> int` — applies all pending
    migrations in order, returns the resulting schema version. Idempotent.
  - `src.storage.migrations.current_version(conn) -> int`.
  - `db.init_schema(conn)` — now delegates to `run_migrations` on a fresh DB.
  - Canonical `SCHEMA` DDL (below) — the contract every later phase reads/writes.
- Consumes: nothing (foundation).

**Canonical schema (author the migration to produce exactly this):**

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS machines (
    machine_id           TEXT PRIMARY KEY,
    bearing_id           TEXT,
    operating_condition  INTEGER,               -- 1|2|3
    speed_rpm            REAL,
    load_kn              REAL,
    dataset              TEXT NOT NULL DEFAULT 'xjtu_sy',   -- isolation seam
    is_documented_failure INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS readings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id            TEXT NOT NULL REFERENCES machines(machine_id),
    timestamp             TEXT NOT NULL,
    cycle                 INTEGER NOT NULL,
    elapsed_minutes       REAL NOT NULL,
    speed_rpm             REAL NOT NULL,
    load_kn               REAL NOT NULL,
    sample_rate_hz        REAL NOT NULL,
    vibration_h_rms       REAL NOT NULL,
    vibration_h_kurtosis  REAL NOT NULL,
    vibration_v_rms       REAL NOT NULL,
    vibration_v_kurtosis  REAL NOT NULL,
    cross_axis_rms_ratio  REAL NOT NULL,
    cross_axis_correlation REAL NOT NULL,
    rul_minutes           REAL,                  -- NULL for live rows w/o known label
    features_json         TEXT NOT NULL,         -- full extract_snapshot_features dict
    dataset               TEXT NOT NULL DEFAULT 'xjtu_sy',
    UNIQUE(machine_id, cycle)
);

CREATE TABLE IF NOT EXISTS predictions (
    id                                INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id                        TEXT NOT NULL REFERENCES machines(machine_id),
    reading_id                        INTEGER REFERENCES readings(id),
    timestamp                         TEXT NOT NULL,
    health_state                      TEXT NOT NULL,
    source                            TEXT NOT NULL DEFAULT 'xjtu_rul',
    predicted_rul_minutes             REAL,
    rul_estimate_kind                 TEXT,       -- point_estimate | lower_bound
    failure_within_horizon_probability REAL,
    prognostic_horizon_minutes        REAL,
    prediction_interval_low           REAL,
    prediction_interval_high          REAL,
    model_version                     TEXT,
    out_of_distribution               INTEGER NOT NULL DEFAULT 0,
    history_snapshots                 INTEGER,
    warnings_json                     TEXT
);

CREATE TABLE IF NOT EXISTS model_inference_log (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id             TEXT NOT NULL,
    timestamp              TEXT NOT NULL,
    model_version          TEXT NOT NULL,
    latency_ms             REAL NOT NULL,
    failure_probability    REAL,
    predicted_rul_minutes  REAL,
    out_of_distribution    INTEGER NOT NULL DEFAULT 0,
    warming_up             INTEGER NOT NULL DEFAULT 0,
    warnings_count         INTEGER NOT NULL DEFAULT 0,
    status                 TEXT NOT NULL,          -- ok | error
    error_message          TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS model_registry (
    model_version  TEXT PRIMARY KEY,
    artifact_path  TEXT NOT NULL,                 -- relative to repo root
    algorithm      TEXT,
    trained_at     TEXT,
    metrics_json   TEXT,
    deployed_at    TEXT,
    is_active      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type   TEXT NOT NULL,   -- per_machine | fleet_summary | model_performance
    scope         TEXT,            -- machine_id or 'fleet'
    format        TEXT NOT NULL,   -- json | csv | markdown
    period_start  TEXT,
    period_end    TEXT,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    generated_by  INTEGER,         -- users.id
    content       TEXT NOT NULL,
    summary_json  TEXT
);
```

**Migration-runner discipline:** keep the existing `alerts`, `maintenance_records`,
`users`, `notifications` tables intact. The runner is a numbered list of
`(version, up_sql_or_callable)` steps; `run_migrations` reads `schema_version`,
applies steps whose version exceeds current, and records each. For the reshape of
`machines`/`readings` (dropping columns SQLite can't `ALTER DROP` on old versions),
use the create-new / copy / drop-old / rename table dance inside a transaction.

**Review gate (Phase 1 → 2):** run `code-review` on the Phase 1 diff. Focus:
migration is idempotent and reversible-safe (no data loss for `alerts`/`maintenance`),
every removed-token grep hit is handled, no `temperature*`/`rul_hours`/`source_test`
survives, full suite green.

**Acceptance:**
- `pytest` green (backend), `npm test` green (frontend) — with temperature
  fields removed from any frontend fixtures/types touched here.
- Fresh DB and an upgraded old DB both reach the same `schema_version`.
- `grep -rn "temperature_c\|rul_hours\|source_test\|sensor_id"` returns only
  archived `src/legacy/` matches.

---

## Phase 2 — Batch Backfill Ingestion

**Goal:** A batch loader turns the XJTU-SY feature table into `machines` +
`readings` rows, idempotently, with zero NULLs on XJTU rows.

**Files:**
- Create: `src/ingestion/backfill.py` — `backfill_dataset(conn, dataset_dir)` /
  `backfill_from_table(conn, feature_df)`.
- Modify: `src/storage/db.py` — ensure `insert_machines`/`insert_readings` accept
  the canonical row dict (from Phase 1).
- Test: `tests/ingestion/test_backfill.py`.

**Interfaces:**
- Consumes: `src.ingestion.xjtu_sy.build_feature_table` (record keys: `bearing_id`,
  `condition`, `cycle`, `elapsed_minutes`, `rul_minutes`, `speed_rpm`, `load_kn`,
  `source_file`, `h_*`/`v_*`/`m_*`, `cross_axis_rms_ratio`, `cross_axis_correlation`);
  Phase 1 `SCHEMA` + `insert_*` helpers.
- Produces: `backfill_dataset(conn, dataset_dir: Path, *, dataset='xjtu_sy') -> BackfillResult`
  with `machines_written`, `readings_written`, `skipped` counts. `machine_id`
  convention = `bearing_id`. `timestamp` synthesized from a base epoch +
  `elapsed_minutes` (deterministic; document the base). `features_json` = full
  `extract_snapshot_features` dict; the six promoted columns are copied out of it.

**Mapping rules (author tests around these):**
- One `machines` row per bearing: `operating_condition = condition`,
  `speed_rpm`/`load_kn` from `OPERATING_CONDITIONS`, `is_documented_failure = 1`
  (all XJTU bearings are run-to-failure), `dataset='xjtu_sy'`.
- One `readings` row per snapshot; `UNIQUE(machine_id, cycle)` makes re-running a
  no-op (INSERT OR IGNORE + report skipped).
- Promoted columns: `vibration_h_rms = h_rms`, `vibration_h_kurtosis = h_kurtosis`,
  `vibration_v_rms = v_rms`, `vibration_v_kurtosis = v_kurtosis`, plus the two
  cross-axis features.

**Review gate (Phase 2 → 3):** `code-review` the ingestion diff. Focus:
idempotency, no NULLs on XJTU rows, feature math not re-implemented (calls
`extract_snapshot_features` via `build_feature_table` only), deterministic
timestamps.

**Acceptance:** backfilling a small fixture dataset twice yields identical row
counts the second time (all skipped); every `readings` NOT NULL column is
populated; trends endpoint returns real points for a backfilled machine.

---

## Phase 3 — Model Wiring: Persisted Predictor + Inference Log + Registry

**Goal:** `RealTimeRULPredictor` state survives restarts; every inference writes
a `predictions` row and a `model_inference_log` row; the active model is
registered in `model_registry`; the machine-health KPI is driven by XJTU RUL
predictions, not the IMS classifier.

**Files:**
- Create: `src/prediction/rul_store.py` — persistence for per-machine feature
  history + a thin `PersistentRULPredictor` wrapper (or history rehydration on
  startup) so restarts rebuild context from `readings`.
- Modify: `src/prediction/rul_realtime.py` — accept an injected persistence hook;
  keep the in-memory fast path, back it with the store. (Do NOT fork feature math.)
- Modify: `src/api/routes/` RUL endpoint — persist prediction + inference-log row
  on each call; register/lookup `model_version` in `model_registry`.
- Modify: `src/kpi/calculations.py` — `_machine_health` reads the latest
  `predictions` row (source `xjtu_rul`): `health_state`, `predicted_rul_minutes`,
  `failure_within_horizon_probability`, `out_of_distribution`. Remove IMS-source
  assumptions.
- Modify: `src/api/schemas.py` — extend `MachineSummary` with `predicted_rul_minutes`,
  `rul_estimate_kind`, `out_of_distribution` (optional, defaulted); drop the last
  temperature remnants if any.
- Unwire: remove the legacy IMS classifier as the health source (archived in P1).
- Test: `tests/prediction/test_rul_store.py`, `tests/api/test_rul_persistence.py`,
  `tests/kpi/test_health_from_rul.py`.

**Interfaces:**
- Consumes: Phase 1 `predictions`/`model_inference_log`/`model_registry` schema;
  `RealTimeRULPredictor.predict(...) -> dict` (existing keys, see
  `src/prediction/rul_realtime.py:215-231`); Phase 2 `readings` (for rehydration).
- Produces:
  - `rul_store.persist_prediction(conn, result: dict, reading_id: int|None) -> int`
    mapping predictor dict → `predictions` columns (`prediction_interval_90_minutes`
    → `prediction_interval_low/high`; `warnings` → `warnings_json`).
  - `rul_store.log_inference(conn, *, machine_id, model_version, latency_ms, result|error) -> None`.
  - `rul_store.rehydrate(predictor, conn, machine_id)` — replays stored `readings`
    (or a compact history) so a cold predictor matches a warm one.
  - `model_registry` upsert of the active artifact on startup.

**Restart-safety note:** the predictor's docstring (`rul_realtime.py:21-27`) flags
in-memory state as the production gap this phase closes. Rehydration must be
deterministic and bounded by `max_history`.

**Review gate (Phase 3 → 4/5/6):** `code-review` the wiring diff. Focus: feature
math still single-sourced; prediction dict → DB column mapping is lossless and
correct; inference log written on both success and error paths; KPI no longer
references IMS; latency measured around inference only.

**Acceptance:** a predict call writes exactly one `predictions` + one
`model_inference_log` row; restart + rehydrate reproduces the same next-prediction
health_state as no-restart; fleet list shows RUL-derived health.

---

## Phase 4 — Streaming Replay Service + Ingestion Control

**Goal:** Replay stored/dataset snapshots through the live predictor at a
controllable rate, exposed via start/stop/status endpoints, so the platform
shows analytics updating on real data.

**Files:**
- Create: `src/ingestion/replay_service.py` — background replay loop + state.
- Modify: `src/api/routes/` — `POST /api/ingestion/replay/start`,
  `POST /api/ingestion/replay/stop`, `GET /api/ingestion/replay/status`.
- Test: `tests/ingestion/test_replay_service.py`, `tests/api/test_ingestion_control.py`.

**Interfaces:**
- Consumes: Phase 3 predictor+persistence; Phase 2 `readings` as the replay source.
- Produces: `ReplayService.start(machine_id, speed_multiplier)`, `.stop(machine_id)`,
  `.status() -> {machine_id: {cycle, running, last_ts}}`. Each replayed snapshot
  goes through the *same* predict+persist path as a live call (no shortcut writes).
- RBAC: start/stop are supervisor+; status is any authenticated role.

**Review gate (Phase 4 → 7):** `code-review`. Focus: thread/task lifecycle (clean
stop, no leaked loops in tests), replay reuses the predict+persist path, WebSocket
broadcast (if wired) matches existing realtime contract.

**Acceptance:** start → status shows running + advancing cycle → stop halts it;
replayed snapshots produce `predictions` rows indistinguishable from live ones.

---

## Phase 5 — Model Observability (Heartbeat + Telemetry)

**Goal:** The model's health and self-telemetry are first-class endpoints.

**Files:**
- Create: `src/observability/model_health.py`, `src/observability/telemetry.py`.
- Modify: `src/api/routes/` — `GET /api/model/health`, `GET /api/model/telemetry`.
- Test: `tests/observability/test_model_health.py`, `tests/api/test_model_endpoints.py`.

**Interfaces:**
- Consumes: `model_inference_log` (Phase 1/3), `model_registry`.
- Produces:
  - `/api/model/health` → `{status, model_version, last_inference_at,
    seconds_since_last_inference, active}` (heartbeat: healthy if a successful
    inference occurred within a configurable window).
  - `/api/model/telemetry` → rolling aggregates over `model_inference_log`:
    `inference_count`, `error_rate`, `p50/p95 latency_ms`, `ood_rate`,
    `warming_up_rate`, windowed. Drift signal derived from `out_of_distribution`
    rate (reuses the predictor's `feature_bounds_99pct` OOD flag; no new model).

**Review gate (Phase 5 → 7):** `code-review`. Focus: aggregates computed in SQL
where possible; percentiles correct; heartbeat window configurable; no PII in
telemetry.

**Acceptance:** with N logged inferences, telemetry counts/percentiles match a
hand-computed fixture; health flips to stale after the window with no new inference.

---

## Phase 6 — Reports Subsystem

**Goal:** Generate and download the four approved report types.

**Files:**
- Create: `src/reports/__init__.py`, `src/reports/generators.py`,
  `src/reports/service.py`.
- Modify: `src/api/routes/` — `POST /api/reports`, `GET /api/reports`,
  `GET /api/reports/{id}`, `GET /api/reports/{id}/download`.
- Test: `tests/reports/test_generators.py`, `tests/api/test_reports.py`.

**Interfaces:**
- Consumes: `predictions`, `readings`, `model_inference_log`, `machines`.
- Produces:
  - `generate(report_type, scope, format, period) -> Report` for
    `per_machine` (prognostic), `fleet_summary`, `model_performance`.
  - Persisted `reports` row; `download` streams `content` with the right
    content-type (`csv`/`markdown`/`json`). "Downloadable export" is the `format`
    dimension, not a separate type.
- RBAC: create is supervisor+; read/download follow existing rules.

**Review gate (Phase 6 → 7):** `code-review`. Focus: generators read only real
persisted data; CSV/markdown escaping; large-range queries bounded; `summary_json`
matches `content`.

**Acceptance:** each report type generates from seeded data with a stable
`summary_json`; download returns correct content-type and body; re-fetch by id
returns the stored report.

---

## Phase 7 — Frontend Surfaces

**Goal:** The UI reflects XJTU RUL health, model observability, ingestion control,
and reports — on real ingested data — with temperature fully removed.

**Files (indicative; confirm against `frontend/src` at plan time):**
- Modify: fleet/machine components consuming `MachineSummary` — swap
  `temperature_severity` for RUL fields (`predicted_rul_minutes`,
  `rul_estimate_kind`, `out_of_distribution`); drop the temperature trend option.
- Create: model-health / telemetry panel, ingestion-control panel, reports view.
- Modify: API client + TypeScript types to match phases 3–6 response shapes.
- Test: Vitest + RTL + MSW handlers for each new endpoint.

**Interfaces:**
- Consumes: endpoints/response shapes from phases 3–6 (freeze them from the
  committed OpenAPI/schemas, not from this roadmap's prose).
- Produces: no backend contract; MSW handlers must mirror real shapes exactly.

**Review gate (Phase 7 → 8):** `code-review`. Focus: no dead temperature UI,
loading/empty/error states, types match backend, charts read left-to-right on
real data.

**Acceptance:** `npm test` green; manual smoke: backfill + replay a machine →
UI shows advancing RUL, health, telemetry, and a generated report.

---

## Phase 8 — Documentation Reconciliation

**Goal:** One authoritative `DATA_MODEL.md` describing the shipped system, plus
baseline/README/catalog updates so docs match code.

**Files:**
- Create: `docs/DATA_MODEL.md` — the document the user explicitly asked for:
  which DB holds the dataset (SQLite, tables), ingestion (streaming replay +
  batch backfill, with the diagram), the model heartbeat, model telemetry,
  reports, and the full canonical schema with column semantics.
- Modify: `design/DESIGN_BASELINE.md` (retire NASA-first framing → XJTU-SY
  backbone), `design/FAN_DATASET_CATALOG.md` (mark temperature/RUL-hours
  resolved), root README run instructions (backfill → replay → view).
- Test: n/a (docs) — but verify every schema/endpoint/claim against committed code.

**Review gate (Phase 8 → done):** `code-review` on docs for accuracy against code
(no aspirational claims), and a final full-suite green run.

**Acceptance:** `DATA_MODEL.md` answers every question from the original request;
no doc references dropped columns/legacy IMS as current; suites green.

---

## How To Resume Later

1. Open this roadmap; find the earliest phase whose review gate hasn't passed.
2. Invoke `superpowers:writing-plans` to expand **only that phase** into
   `docs/superpowers/plans/2026-08-06-phase-N-<slug>-plan.md`, authoring literal
   TDD steps against the *current committed code* (re-read the touched files;
   don't trust prose here for signatures).
3. Execute it via `superpowers:subagent-driven-development` (recommended) or
   `superpowers:executing-plans`.
4. Run the phase's `code-review` gate; resolve findings.
5. Return here, mark the phase gate passed, proceed to the next.

Do not skip a review gate. Do not let a later phase begin against a red suite.
