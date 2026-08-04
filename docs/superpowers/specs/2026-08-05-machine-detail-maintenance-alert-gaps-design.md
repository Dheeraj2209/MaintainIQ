# Machine Detail Page — Maintenance/Alert Workflow Gaps

Source: `TODO.md` item 5. Closes the five checklist bullets there.

## Problem

`MachineDetail.tsx`'s "Log Maintenance" feature is a bare 3-field logbook
entry (when/description/technician free text) with no connection to the
alerts shown right next to it, and the Alerts panel on the same page has a
dead click handler (`onSelect={() => {}}`). Additionally:

- Technician is an unaccountable free-text string despite the app already
  having authenticated users with roles.
- There's no way to distinguish a scheduled inspection from a breakdown
  repair in the maintenance history.
- A vestigial `onLogged` no-op prop is threaded from `MachineDetailPage`
  through `MachineDetail` for no purpose.
- `GET /machines/{id}` returns the full, unbounded `maintenance_history`
  array.

## Data model

Additive, nullable columns on `maintenance_records` (`src/storage/db.py`) —
no backfill needed, existing rows remain valid with both fields `NULL`:

```sql
ALTER TABLE maintenance_records ADD COLUMN alert_id INTEGER REFERENCES alerts(id);
ALTER TABLE maintenance_records ADD COLUMN type TEXT CHECK(type IN ('preventive','corrective'));
```

## Backend

**`src/maintenance/records.py`**
- `log_maintenance(conn, machine_id, performed_at, description=None, technician=None, alert_id=None, type=None)`:
  if `alert_id` is given, validate it exists *and* belongs to `machine_id`
  (raise `MaintenanceError` otherwise, to prevent a stale/cross-machine
  form submission from mislinking a record).
- `get_history(conn, machine_id, limit=None, offset=0)`: optional
  pagination. `limit=None` preserves today's unbounded behavior for
  existing callers, so this is non-breaking.

**`src/api/schemas.py`**
- `MaintenanceCreate` gains `alert_id: Optional[int] = None`,
  `type: Optional[Literal["preventive", "corrective"]] = None`.
- `MaintenanceRecord` gains the same two fields on the response side.

**`src/api/routes/maintenance.py`**
- `GET /maintenance/{machine_id}` gains `limit: int = Query(20, ge=1, le=200)`
  and `offset: int = Query(0, ge=0)`, used by the frontend's "Load more".

**`src/api/routes/machines.py`**
- `GET /machines/{machine_id}` calls `maintenance.get_history(db, machine_id, limit=10)`
  instead of unbounded — the embedded `maintenance_history` becomes "10 most
  recent"; the full list stays available via the paginated endpoint above.

## Frontend

**`frontend/src/api/types.ts`**
- `MaintenanceRecord` and `MaintenanceCreate` gain
  `alert_id?: number | null` and `type?: 'preventive' | 'corrective' | null`.
- `api.getMaintenanceHistory` client method gains optional `limit`/`offset`
  params.

**`AlertsPanel.tsx`**
- `onSelect` signature changes from `(machineId: string) => void` to
  `(alert: Alert) => void` — the parent needs to know *which* alert, not
  just which machine (machine is already known on this page).
- Visually indicate the selected alert (subtle border/ring) so the link to
  the form below reads clearly.

**`MachineDetail.tsx`**
- New state: `selectedAlert: Alert | null`, set via `AlertsPanel`'s
  `onSelect`, passed down to `MaintenanceForm`, cleared on successful
  submit.
- Remove the `onLogged` prop entirely (and its no-op pass-through in
  `MachineDetailPage.tsx`); on successful log, call
  `toast.success('Maintenance logged')` via `sonner` (same library already
  wired app-wide in `App.tsx` / used in `LiveEventsProvider.tsx`).
- History table: add a "Type" column. Add a "Load more" button that calls
  the paginated history endpoint and appends rows, hidden once a page
  returns fewer than `limit` rows.

**`MaintenanceForm.tsx`**
- New optional prop `linkedAlert?: Alert | null`. When set, show a small
  dismissible chip above the form ("Logging maintenance for alert #{id}:
  {message}") and include `alert_id: linkedAlert.id` in the submitted
  payload.
- New `type` field (`Select`: preventive / corrective). Defaults to
  `corrective` when `linkedAlert` is set (logging off an alert is
  reactive by definition), otherwise `preventive`.
- Technician field pre-fills from `useAuth().user?.name` on mount but
  remains a plain editable text input — not locked/read-only.

## Testing

**Backend**
- `alert_id` validation: valid, unknown id, id belonging to a different
  machine.
- `type` accepted values.
- `get_history` pagination (`limit`/`offset` slicing; unbounded when
  omitted).
- `POST /maintenance` round-trips `alert_id`/`type` through the response
  model.
- `GET /maintenance/{machine_id}?limit=&offset=` pagination.
- `GET /machines/{id}` returns only the capped `maintenance_history`.

**Frontend**
- `AlertsPanel`: clicking a row calls `onSelect` with the full alert
  object.
- `MaintenanceForm`: renders the alert chip when `linkedAlert` is passed
  and includes `alert_id` in the submitted payload; technician field
  pre-fills from auth context; `type` default depends on whether an alert
  is linked.
- `MachineDetail`: selecting an alert populates `selectedAlert` and passes
  it through; "Load more" appends additional history rows;
  `toast.success` fires after a successful log (no `onLogged` prop).

## Out of scope

- Cost / parts_used fields on maintenance records (deferred — TODO called
  these "optional" and no immediate need surfaced).
- A dedicated full-history view (modal or route) — "Load more" inline
  pagination covers the current need without new UI primitives.
