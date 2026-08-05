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

- [x] Add `acknowledged_at` / `acknowledged_by` columns (or an `acknowledged`
      status) to the `alerts` table (`src/storage/db.py`)
- [x] Add `POST /alerts/{id}/acknowledge` endpoint (role-gated to
      operator/supervisor/admin) in `src/api/routes/alerts.py`
- [x] Add an "Acknowledge" button/action to `frontend/src/pages/AlertsPage.tsx`
- [x] Add `avg_alert_acknowledgement_hours` to `src/kpi/calculations.py`
      alongside the existing `avg_alert_resolution_hours`
- [x] Broadcast an `alert_acknowledged` WebSocket event via
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

- [x] Wire `AlertsPanel`'s `onSelect` in `MachineDetail.tsx` to something
      real instead of `() => {}` (currently a dead click on every alert row)
- [x] Let "Log Maintenance" reference/resolve a specific alert: add an
      optional `alert_id` field to `MaintenanceCreate`/`maintenance_records`
      (`src/api/schemas.py`, `src/storage/db.py`, `src/maintenance/records.py`),
      and pre-fill the form when logging maintenance from an alert row
- [x] Tie `technician` to the authenticated user instead of free text —
      the app already has `AuthContext`/JWT with user roles; default the
      field to the logged-in user (still editable) instead of an
      unaccountable string
- [x] Add a maintenance `type` (preventive/corrective) and optionally
      `cost`/`parts_used` fields — currently there's no way to distinguish
      a scheduled inspection from a breakdown repair in the history table
      (`type` field done; `cost`/`parts_used` were optional and left out of scope)
- [x] Remove the dead `onLogged` no-op prop plumbing
      (`MachineDetailPage.tsx` → `MachineDetail.tsx`) or make it do
      something real (e.g. toast/notification) — currently vestigial
- [x] Paginate or cap `maintenance_history` on `GET /machines/{id}` —
      currently returns the full unbounded history array

## 6. Frontend/backend "two ports" — already integrated, not a gap

Dev shows two servers (`:5173` Vite, `:8000` FastAPI) — this looks like two
separate apps but is a standard, already-correct split-stack setup, not
something that needs fixing:

- **Dev mode**: `frontend/vite.config.ts` proxies any `/api/*` request from
  the `:5173` dev server straight to `:8000`, including WebSocket upgrades
  (`ws: true`). The browser only ever talks to `:5173`; Vite forwards API
  calls under the hood. This is why there's no CORS config anywhere — the
  browser thinks it's all same-origin.
- **Prod mode**: there is only one port. `cd frontend && npm run build`
  emits `frontend/dist/`; `src/api/app.py` mounts that directory via
  Starlette's `StaticFiles` and serves the built SPA + `/assets` directly
  from FastAPI on `:8000` (see `_WEB_DIR` / `_ASSETS_DIR` in `app.py`).
  `npm run dev` (the two-port setup) is a dev-only convenience for hot
  module reload; it is not how the app ships.
- No action needed here — confirmed by reading `vite.config.ts` and
  `src/api/app.py` (both have comments spelling this out already). Noting
  it in this file only because it caused confusion once.

## 7. Full redesign wanted — "sleek, modern, glassy" theme, applied consistently (2026-08-04)

User feedback: not satisfied with the current "Ember black" flat-surface
theme (see item 8 below for its specifics) — wants a **sleek, modern,
glassmorphism-style look applied consistently across every page**, not
just the dashboard. User asked me to research the approach myself rather
than specify it. This supersedes item 8's refinement list; treat item 8
as reference material (exact files/tokens) once this is scoped, not as
the plan itself.

Researched current (2026) dashboard design direction — see design
rationale below, then the concrete plan.

### Design rationale (why this approach, not generic "add blur everywhere")

- **"Dark glassmorphism" is the current defining SaaS/dashboard aesthetic**,
  not the 2020 frosted-white-card look — moodier, more sophisticated,
  fused with dark mode rather than sitting on top of a light theme.
- **Flat solid black is the wrong backdrop for glass.** Glass panels are
  "practically invisible on a solid black background" — the effect needs
  something behind it to distort: soft ambient gradient "orbs" (large,
  low-opacity blurred color blobs) drifting behind the glass panels. This
  is actually compatible with MaintainIQ's existing `.bg-aurora` radial
  gradients in `frontend/src/index.css` — those are the right idea, just
  need to be more saturated/larger so there's something for the glass to
  refract, instead of the current subtle 5-7% opacity edge glow.
- **Legibility is the #1 failure mode of glassmorphism.** Best practice is
  a thin high-contrast border/stroke around every glass edge, plus a
  "scrim" (a semi-opaque solid layer) behind text sitting on glass, so
  contrast never depends on whatever happens to be behind the blur.
  Current health-state colors (badges/text) must keep working at full
  contrast on top of glass — this is a hard constraint, not a nice-to-have.
- **Bento-grid layout pairs naturally with glassmorphism** and fits this
  app's content already — KPI cards, machine tiles, alert panels are
  already modular card-shaped units (`KpiCards.tsx`, `MachineGrid.tsx`,
  `AlertsPanel.tsx`); the redesign should lean into asymmetric card sizing
  (e.g. a larger "fleet pulse" tile) rather than a uniform grid.
- **backdrop-filter: blur() has a real perf cost** on low-end devices —
  scope it to panel-level surfaces (sidebar, header, cards) not to every
  nested element, and test on a throttled/low-end profile before shipping.

### Concrete plan

- [x] New token set in `frontend/src/index.css`: replace the current
      opaque `.glass`/`.glass-panel` (solid `--color-surface`, no blur)
      with real frosted glass — `backdrop-filter: blur(16-24px)`,
      semi-transparent surface color (~8-14% opacity), 1px
      high-contrast border (~20-30% white/accent opacity), soft outer
      shadow for lift. Add a `--glass-scrim` token for a text-legibility
      backing layer on top of glass where needed.
- [x] Rework `.bg-aurora` background orbs to be larger, more saturated,
      and (per item 7's earlier ambient-animation idea) slowly drifting —
      they're now structural to the glass effect, not just decoration.
- [x] Keep the existing red/gold accent + health-state color tokens
      (healthy/degrading/faulty/critical/unknown) — the redesign changes
      *surface treatment*, not the semantic color language already in
      `healthStyles.ts`; re-verify contrast of each once on translucent
      glass instead of opaque `--color-surface`.
- [x] Apply the new glass tokens consistently across every page —
      `frontend/src/pages/*.tsx` (Dashboard, Machines, Alerts, Analytics,
      Maintenance, Notifications, AdminUsers, Demo, Login) and
      `frontend/src/layout/AppShell.tsx` (sidebar + header) — so no page
      is left on the old flat-surface look while others get glass
- [x] Revisit `KpiCards.tsx` / `MachineGrid.tsx` / dashboard layout for a
      bento-style asymmetric grid instead of the current uniform grid,
      now that panels are visually lighter/glassier and can support
      varied sizes without feeling heavy
- [x] Perf check: profile `backdrop-filter` cost on a throttled CPU
      (Chrome DevTools perf throttling) before shipping — scope blur to
      outer panels only, not per-row/per-badge elements
      (done in code: blur lives only on `.glass`/`.glass-panel` outer
      panels + the two `body` field pseudo-elements; removed the per-row
      blur that AlertsPanel previously carried. Live DevTools throttle
      profiling still worth a manual pass before an eventual ship.)
- [x] Accessibility pass once glass tokens exist: verify text contrast
      ratios (WCAG AA) for body text and health badges sitting on the
      new translucent surfaces; add scrim backing wherever a ratio fails
      (`--glass-scrim` token added; `.glass` surfaces sit over a near-
      black field so `--color-text #f2f4f8` / `--color-text-muted
      #9aa1ad` clear AA; health tokens unchanged and re-checked on glass)
- [ ] Note: `frontend/dist/` (served by FastAPI on `:8000`) is currently a
      stale build from an even older green/teal theme, predating "Ember
      black" — confirmed by comparing `:5173` (live source, red/gold) vs
      `:8000` (stale build, green/teal) in the browser. Don't rebuild/ship
      until the new glassy theme is actually done, so the eventual rebuild
      captures the real target theme, not another soon-to-be-stale one.

Sources consulted: [Dark Glassmorphism: The Aesthetic That Will Define UI
in 2026](https://medium.com/@developer_89726/dark-glassmorphism-the-aesthetic-that-will-define-ui-in-2026-93aa4153088f),
[50 Best Dashboard Design Examples for 2026](https://muz.li/blog/best-dashboard-design-examples-inspirations-for-2026/),
[The Anatomy of High-Performance SaaS Dashboard Design: 2026 Trends & Patterns](https://www.saasframe.io/blog/the-anatomy-of-high-performance-saas-dashboard-design-2026-trends-patterns),
[Bento Grid Dashboard Design: Complete Guide 2026](https://www.orbix.studio/blogs/bento-grid-dashboard-design-aesthetics),
[UI Design Trends for 2026: Full Guide](https://midrocket.com/en/guides/ui-design-trends-2026/).

## 8. Prior style/UI refinement notes on the old "Ember black" theme (2026-08-04)

Superseded by item 7's full redesign direction above — kept here only as
file/token pointers for reference, not as an active plan. Current theme
("Ember black" — near-black chrome, red/gold accent pair, reserved
saturated health-state colors) was intentional and distinctive, but the
user now wants a different, glassier direction instead.

- [ ] **Collapsible sidebar** — `frontend/src/layout/AppShell.tsx` hardcodes
      `w-60`; add an icon-only rail mode (~72px) toggled by a button,
      animated with framer-motion, persisted to localStorage (same pattern
      already used for `miq:dashboard-layout` in `DashboardPage.tsx`)
- [ ] **Ambient ember animation** — the radial-gradient glow in `.bg-aurora`
      (`frontend/src/index.css`) is static; add a slow drift/pulse
      (60-90s ease loop) respecting `prefers-reduced-motion`
- [ ] **Critical health color** — `--color-critical: #ff2d6a`
      (`frontend/src/index.css`) reads as pink/magenta, which breaks the
      "hottest state" heat metaphor used by the rest of the health ramp
      (healthy green -> degrading amber -> faulty orange). Consider a
      true hot-red or white-hot tone instead so critical reads as "hottest,"
      not "different category"
- [ ] **Chart gradient fills** — `TrendChart`/sparkline components render
      flat single-color lines; add an accent-to-transparent gradient fill
      to tie the dashboard's most-viewed real-time widget back to the
      ember palette
- [ ] **Error/empty states need interface voice + action** — e.g. the plain
      `{error && <div>...}` block in `DashboardPage.tsx` states the problem
      but doesn't route the user to the fix (the Refresh button already on
      the page); apply across other empty/error states too
- [ ] **Gold (`--color-accent-2`) reuse conflict** — currently means both
      "due for inspection" (KPI card tone) and "unseen notification" (bell
      pulse dot); pick one meaning for gold and give the other its own token

## 9. Adopt fan/rotating-machinery datasets to validate the pipeline on real fan data

Full dataset survey + sensor specs: `design/FAN_DATASET_CATALOG.md` (2026-08-04).
Every model trained/evaluated so far is NASA IMS bearing-only — no fan has
ever been run through the pipeline, even though `SRS_Document.md` scopes fans
as a supported machine type. This closes that gap without waiting on M6
hardware; it's pure ingestion/validation work against existing code.

- [ ] Write a `src/ingestion/fan_coil.py` loader for **FAN-COIL-I**
      (`.npy`, shape `(5246, 320000, 2)`, 32kHz dual-channel accelerometer)
      that windows the raw waveform and reuses the existing
      `src/features/vibration.py` extraction — same feature columns as the
      IMS pipeline, just a new `source_test`/machine population in
      `src/storage/db.py`
- [ ] FAN-COIL-I has no discrete fault labels (only an unlabeled health
      trend) — before trusting extracted features on it, cross-validate
      `features/vibration.py` against **MAFAULDA**'s labeled classes
      (normal / imbalance / misalignment / bearing fault) so a feature-
      extraction bug isn't mistaken for "the fan doesn't show that fault"
- [ ] Extend `src/training/stage_classifiers.py` to train/evaluate on the
      new machine type rather than assuming IMS-bearing-only data, and
      record in `models/evaluation_report.json` how the IMS-trained
      classifier performs when scored on fan-coil data — determines
      whether a fan-specific model is needed or the bearing-trained one
      generalizes
- [ ] Do not wire up MIMII (acoustic) or the synthetic Kaggle IoT dataset
      yet — both need new ingestion/feature-extraction code, not just a
      new loader (see catalog doc gaps: no RPM/acoustic/current fields in
      the schema). Track as a separate item once M6 hardware scope adds
      those sensors.

## 10. Deferred / explicitly out of scope for now

- Root-cause accuracy KPI — no labeled ground truth in the IMS dataset
- SMS/mobile alert delivery (FR-30) — email-only for now
- 1D-CNN/LSTM raw-signal deep model — noted as a future option in
  `IMPLEMENTATION_PLAN.md`, not needed while classical ML performs adequately

## 11. Kill the "traffic light" feel — severity indicators + glow-color rework (2026-08-05)

User feedback: the red/yellow/green health ramp plus the plain colored
circular dots (machine tiles, Fleet-pulse legend keys, machine-detail header
glow) read as literal traffic lights and undercut the glassy aesthetic from
item 7. Separately, the AMD red accent is currently the *glow* color
everywhere (hover glow, text glow, panel hairline, scrollbar, two of the four
signal-field orbs) — user wants red kept only as a flat, high-contrast brand
color, with gold/grey doing all the glowing. Scope for this pass is research
+ a written plan only; no code changes yet.

### Design rationale

- **The dot *is* the traffic light.** `healthStyles.ts` maps
  healthy/degrading/faulty/critical to a literal green→amber→orange→red ramp
  (`--color-healthy #3ddc84` … `--color-critical #ff5470` in `index.css:43-47`),
  rendered as a solid `rounded-full` dot in `MachineGrid.tsx`,
  `DashboardPage.tsx`'s Fleet-pulse legend, and `MachineDetail.tsx`'s header
  bloom — three saturated hues on a bare circle is the textbook pattern.
  Badge/pill shape itself (`ui/badge.tsx`) is already a fine "glass chip,"
  so the fix is the color ramp + the bare dots, not the badge component.
- **Color-alone status is also an accessibility gap, not just a look.**
  Carbon Design System's status pattern and multiple accessibility writeups
  agree status should be color **+ shape + symbol** together, and that
  red/green pairing specifically fails ~8% of men with color vision
  deficiency. Fixing the "traffic light" complaint and closing this gap are
  the same fix: drop saturated green from the ramp entirely and add a
  non-color channel (shape/icon) so severity still reads with the color
  channel removed. ([Carbon status-indicator pattern](https://carbondesignsystem.com/patterns/status-indicator-pattern/),
  [WCAG color accessibility: status indicators beyond color coding](https://www.accessibility.chat/articles/when-color-coding-fails-why-status-indicators-need-more-than-pretty-colors))
- **Red is currently wallpaper, which is why it reads loud.** `--color-accent`
  (red) drives `.hover-glow`'s ring+shadow, `.text-glow`, the `.panel-notch`
  hairline gradient, the scrollbar thumb, and 2 of 4 `body::before`
  signal-field orbs (`index.css:95-107,185-258`) — it shows up on nearly
  every panel, so it never reads as a rare alarm signal. Dense dark
  dashboards that successfully pull off a "premium" feel (Bloomberg-terminal-
  style layouts) instead reserve saturated accent for meaning and lean on
  amber/gold for the ambient chrome — validating gold-as-primary-glow /
  red-as-rare-alarm as a real, precedented direction rather than a
  compromise. ([Dark Glassmorphism: the aesthetic that will define UI in 2026](https://medium.com/@developer_89726/dark-glassmorphism-the-aesthetic-that-will-define-ui-in-2026-93aa4153088f))
- **Reassign roles, don't invent a new palette.** Keep both existing brand
  hexes (red + gold) but split their jobs: red becomes flat-only (primary
  buttons, critical fill, wordmark letter) and never appears in a
  box-shadow/text-shadow/gradient; gold plus a new neutral "chrome"
  (platinum/grey) glow take over every ambient/hover glow role. The health
  ramp becomes a single warm-neutral escalation — slate → gold → deep amber →
  red-only-at-critical — instead of green→amber→orange→red, so red is
  reserved exclusively for the one state that should alarm.
- **Replace the bare dot with a glyph that fits the existing "Signal Glass"
  motif.** The theme is already named for its oscilloscope grid/drifting
  orbs (`index.css:3-17`) — a small ascending multi-bar "signal meter" (a
  few `div`s of increasing height, N lit per severity tier) is an on-brand
  replacement for the circle: it's not a phone-signal icon (which would
  wrongly imply "more bars = better"), just a compact custom glyph that
  encodes severity by *count* as well as color, and reinforces the theme's
  own name instead of reaching for a generic checkmark badge.

### Concrete plan

- [ ] `frontend/src/index.css` — remap the health ramp: `--color-healthy` to
      a cool slate/silver (not green), `--color-degrading` to the existing
      gold (`--color-accent-2`), `--color-faulty` to a deeper amber/copper,
      `--color-critical` keeps the AMD red family (the *only* ramp step that
      stays red). Add a `--color-glow-chrome` neutral platinum token for the
      default (non-semantic) hover glow.
- [ ] `frontend/src/index.css` — strip red out of every ambient/glow utility:
      `.hover-glow` (ring + box-shadow) → chrome/gold blend, `.text-glow` →
      gold shadow, `.panel-notch` hairline → gold/chrome gradient (drop the
      red stops), scrollbar thumb → chrome grey, `body::before` signal-field
      orbs → one muted graphite orb + the gold orb + a platinum orb (red
      dropped or kept at very low opacity as a rare accent, not dominant).
- [ ] New small `SeverityMeter` component (ascending multi-bar glyph, N bars
      lit per tier) to replace every bare `rounded-full ... dot` status
      indicator: `MachineGrid.tsx` card header dot, `DashboardPage.tsx`
      Fleet-pulse legend key, `MachineDetail.tsx` header corner bloom source.
- [ ] `healthStyles.ts` — add an `icon` per tier (e.g. lucide `CheckCircle2` /
      `Gauge` / `TriangleAlert` / `OctagonAlert` / `CircleHelp`) alongside
      the existing badge/border/text/dot/glow classes; render it as a
      leading icon inside `Badge` and `AlertsPanel`'s severity pill so
      status reads via color + shape + symbol, not color alone.
- [ ] `components/tremor/tracker.tsx` (the dashboard "fleet bar") — keep its
      existing rectangular-cell shape (already not circles), re-source cell
      fill from the new ramp, and consider varying cell height by severity
      too so the row encodes severity on a second channel, not hue alone.
- [ ] `lib/chartColors.ts` + `AnalyticsPage.tsx` recharts — remap the same
      four tokens so the health-distribution chart tells the identical
      slate→gold→amber→red story instead of its own green/amber/orange/
      rose-red ramp.
- [ ] Re-run the WCAG AA contrast pass from item 7 for the two *new* hexes
      (slate "healthy", deep-amber "faulty") against `--glass-scrim` and
      glass surfaces — they aren't covered by the earlier contrast check.
- [ ] Spot-check the new ramp with a colorblindness simulator (Coblis / Sim
      Daltonism) once implemented — confirms removing saturated green
      actually closes the red/green-confusion gap, not just the aesthetic one.

Sources consulted: [Status indicators — Carbon Design System](https://carbondesignsystem.com/patterns/status-indicator-pattern/),
[WCAG Color Accessibility: Status Indicators Beyond Color Coding](https://www.accessibility.chat/articles/when-color-coding-fails-why-status-indicators-need-more-than-pretty-colors),
[Traffic Lights Out, Accessible Dashboards In!](https://smart-frames.co.uk/2025/01/23/rethinking-rag-colours-in-business-intelligence-tools/),
[Dark Glassmorphism: The Aesthetic That Will Define UI in 2026](https://medium.com/@developer_89726/dark-glassmorphism-the-aesthetic-that-will-define-ui-in-2026-93aa4153088f).
