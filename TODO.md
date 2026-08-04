# TODO — Next Implementations

Tracks concrete next-work items identified against `design/SRS_Document.md`,
`design/DESIGN_BASELINE.md`, and the rendered UML/architecture diagrams.
See `IMPLEMENTATION_PLAN.md` for the milestone history (M1-M5 done, M6 pending).

## 1. Alert acknowledge workflow (highest priority — designed, never built)

Both `design/diagrams/rendered/multimachine_uml_class.png` and
`factory_scenario_description.png` design a `Dashboard.acknowledgeAlert()`
action distinct from resolution, and the SRS lists "average time to
acknowledge an alert" as its own Maintenance KPI. Currently alerts only ever
go open -> resolved automatically (when the pipeline sees a healthy reading
again) — there is no human acknowledge step anywhere in the code.

- [ ] Add `acknowledged_at` / `acknowledged_by` columns (or an `acknowledged`
      status) to the `alerts` table (`src/storage/db.py`)
- [ ] Add `POST /alerts/{id}/acknowledge` endpoint (role-gated to
      operator/supervisor/admin) in `src/api/routes/alerts.py`
- [ ] Add an "Acknowledge" button/action to `frontend/src/pages/AlertsPage.tsx`
- [ ] Add `avg_alert_acknowledgement_hours` to `src/kpi/calculations.py`
      alongside the existing `avg_alert_resolution_hours`
- [ ] Broadcast an `alert_acknowledged` WebSocket event via
      `src/realtime/manager.py` so other connected dashboards update live

## 2. Commit the uncommitted realtime/auth/notifications layer

Auth (JWT/RBAC), WebSocket realtime, email notifications, and the demo
injection endpoint are all working (61 backend / 107 frontend tests green)
but still uncommitted on top of `016d040`. Commit before starting new work.

## 3. M6 — Real hardware ingestion (documented future milestone)

- [ ] ESP32 + DS18B20 (temperature) + ADXL345/MPU6050 (vibration) per machine
- [ ] MQTT over Wi-Fi telemetry upstream, alert topic downstream
- [ ] Replace the `/demo` injection endpoint with real telemetry ingestion
      feeding the same `prediction/live.py` -> `alerts/live.py` pipeline
- [ ] Edge-side offline buffering + resync on reconnect (FR-09, FR-07/08)

## 4. KPIs unlocked once M6 lands

Currently reported as `not_applicable` in `src/kpi/calculations.py`, by
design — revisit once real telemetry exists:

- [ ] `sensor_collection_rate`, `transmission_success_rate`,
      `edge_buffer_health`, `cloud_sync_health` (System-Performance KPIs)
- [ ] `breakdown_reduction`, `emergency_maintenance_reduction`
      (Operational KPIs — need longitudinal before/after real-world data)

## 5. Machine detail page — maintenance/alert workflow gaps

Surfaced while reviewing `frontend/src/components/MachineDetail.tsx`: the
"Log Maintenance" feature is a bare 3-field logbook entry (when/description/
technician free text) with no connection to the alerts shown right next to
it, and the Alerts panel on this same page has a dead click handler. This
overlaps with item 1 (alert acknowledge workflow) — a coherent fix likely
addresses both together.

- [ ] Wire `AlertsPanel`'s `onSelect` in `MachineDetail.tsx` to something
      real instead of `() => {}` (currently a dead click on every alert row)
- [ ] Let "Log Maintenance" reference/resolve a specific alert: add an
      optional `alert_id` field to `MaintenanceCreate`/`maintenance_records`
      (`src/api/schemas.py`, `src/storage/db.py`, `src/maintenance/records.py`),
      and pre-fill the form when logging maintenance from an alert row
- [ ] Tie `technician` to the authenticated user instead of free text —
      the app already has `AuthContext`/JWT with user roles; default the
      field to the logged-in user (still editable) instead of an
      unaccountable string
- [ ] Add a maintenance `type` (preventive/corrective) and optionally
      `cost`/`parts_used` fields — currently there's no way to distinguish
      a scheduled inspection from a breakdown repair in the history table
- [ ] Remove the dead `onLogged` no-op prop plumbing
      (`MachineDetailPage.tsx` → `MachineDetail.tsx`) or make it do
      something real (e.g. toast/notification) — currently vestigial
- [ ] Paginate or cap `maintenance_history` on `GET /machines/{id}` —
      currently returns the full unbounded history array

## 6. Deferred / explicitly out of scope for now

- Root-cause accuracy KPI — no labeled ground truth in the IMS dataset
- SMS/mobile alert delivery (FR-30) — email-only for now
- 1D-CNN/LSTM raw-signal deep model — noted as a future option in
  `IMPLEMENTATION_PLAN.md`, not needed while classical ML performs adequately
