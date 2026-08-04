# Alert Acknowledge Workflow — Design

Status: Approved
Date: 2026-08-04
Source: `TODO.md` item 1 ("highest priority — designed, never built"), SRS "average time to acknowledge an alert" KPI, `multimachine_uml_class.png` (`Dashboard.acknowledgeAlert()`).

## Problem

Alerts currently only transition `open -> resolved`, automatically, when the pipeline sees a healthy reading again. There is no human-driven acknowledge step, even though it's designed into the UML class diagram and named as its own Maintenance KPI in the SRS. This adds that step end-to-end: schema, API, realtime broadcast, KPI, and UI.

## Decisions

1. **Status model**: `alerts.status` stays `open` / `resolved` (no third `acknowledged` status). Acknowledgement is an independent, orthogonal flag — new nullable `acknowledged_at` (TEXT, ISO-8601) and `acknowledged_by` (INTEGER, `users.id`) columns on `alerts`. This avoids touching every existing `status = 'open'` check in the live pipeline, KPI queries, and unresolved-count logic.
2. **Re-acknowledge behavior**: idempotent no-op. Acknowledging an already-acknowledged alert returns 200 with the existing alert unchanged (no error, no re-broadcast). Simplifies the frontend button (just disable/hide after success) and avoids race handling between two dashboards.
3. **Scope**: any alert can be acknowledged regardless of `status` (open or resolved). "Time to acknowledge" is a KPI independent of resolution; restricting to open-only would bias the KPI away from fast-resolving alerts.
4. **Identity**: `acknowledged_by` stores the numeric `users.id` (matches JWT `sub`, matches `get_current_user()`'s return shape), not free text. No FK constraint is added, consistent with `alerts.machine_id` also lacking one today.
5. **UI surface**: acknowledge button appears only on the full `AlertsPage.tsx` table, not the compact `AlertsPanel` widget — keeps scope matched to TODO.md.
6. **Role gating**: `admin`, `supervisor`, and `operator` can all acknowledge (i.e., every existing role) — equivalent to just requiring login, but implemented via `require_role(...)` so the endpoint also receives the user identity in one dependency.

## Changes by layer

### Database — `src/storage/db.py`
- Add to the `alerts` `CREATE TABLE` SQL: `acknowledged_at TEXT`, `acknowledged_by INTEGER`.
- Since there's no migration framework (schema is a single `executescript` `CREATE TABLE IF NOT EXISTS` string), add a small guarded `ALTER TABLE alerts ADD COLUMN ...` (wrapped in try/except for `sqlite3.OperationalError: duplicate column`) in `init_schema()` so existing `maintainiq.db` files pick up the new columns without manual deletion.

### API — `src/api/routes/alerts.py`
- New endpoint: `POST /alerts/{id}/acknowledge`.
- Dependency: `user: dict = Depends(require_role("admin", "supervisor", "operator"))`.
- Logic: look up alert by id (404 if missing). If `acknowledged_at` already set, return the alert unchanged. Otherwise set `acknowledged_at = datetime.now(timezone.utc).isoformat()`, `acknowledged_by = user["id"]`, persist, broadcast `alert_acknowledged`, return the updated `Alert`.
- `src/api/schemas.py`: add `acknowledged_at: Optional[str]`, `acknowledged_by: Optional[int]` to the `Alert` model.

### Realtime — broadcast from the new route handler
- `await manager.broadcast({"type": "alert_acknowledged", "machine_id": ..., "alert": {...}, "at": ...})`, matching the existing `alert_created`/`alert_escalated`/`alert_resolved` payload shape.

### KPI — `src/kpi/calculations.py`
- Add `avg_alert_acknowledgement_hours` to the per-machine maintenance dict in `_maintenance_for_machine()`, computed the same way as `avg_alert_resolution_hours`: query alerts with `acknowledged_at IS NOT NULL`, average `(acknowledged_at - opened_at)` in hours, `None` if no data (not the `not_applicable` dict pattern — this is genuinely computable per machine).
- `src/api/schemas.py`: add the new field to whichever model represents this maintenance dict (mirroring `avg_alert_resolution_hours`'s existing field).

### Frontend
- `frontend/src/api/types.ts`: add `acknowledged_at?: string | null`, `acknowledged_by?: number | null` to `Alert`; add `'alert_acknowledged'` to `LiveEventType`; add `avg_alert_acknowledgement_hours` to the maintenance summary type.
- `frontend/src/api/client.ts`: add `acknowledgeAlert: (id: number) => request<Alert>(\`/alerts/${id}/acknowledge\`, { method: 'POST' })`.
- `frontend/src/pages/AlertsPage.tsx`: add an Actions column with an "Acknowledge" button per row; hide/disable it once `acknowledged_at` is set; `e.stopPropagation()` so the click doesn't trigger row navigation; on click, call `acknowledgeAlert` then refetch via the existing `load(status)`.
- `frontend/src/realtime/LiveEventsProvider.tsx`: add an `alert_acknowledged` case to the toast `describe()` switch (currently falls through to a generic default).
- `frontend/src/components/MachineDetail.tsx`: add an "Avg acknowledgement (h)" `<Fact>` next to the existing "Avg resolution (h)" fact, rendering `—` when `null`.

## Testing

- Backend (`tests/test_api.py`): acknowledge success (sets fields, returns 200), idempotent re-acknowledge (second call unchanged), 404 for missing alert id, accessible to all three roles.
- Backend (`tests/test_kpi.py`): `avg_alert_acknowledgement_hours` computed correctly against seeded fixture data, `None` when no acknowledged alerts exist.
- Frontend (`frontend/src/pages/AlertsPage.test.tsx`): acknowledge button calls the API and updates the row; button suppressed once already acknowledged.
- Frontend (`frontend/src/realtime/LiveEventsProvider.test.tsx`): `alert_acknowledged` event produces the expected toast description.

## Out of scope

- `AlertsPanel.tsx` compact widget (dashboard/machine-detail) does not get an acknowledge button.
- No new `acknowledged` status value; no changes to the auto open/resolve pipeline logic in `alerts/live.py`.
- No FK constraint added for `acknowledged_by` or `machine_id` (matches existing schema conventions).
