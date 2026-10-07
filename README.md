# Smart Predictive Maintenance Decision-Support System

A predictive maintenance decision-support system built on the XJTU-SY run-to-failure bearing dataset, providing multi-machine vibration monitoring, remaining-useful-life (RUL) prediction, model observability, automated reports, and maintenance history tracking.

## Where to start

- [`design/DESIGN_BASELINE.md`](design/DESIGN_BASELINE.md) — canonical scope and design decisions. Where other design documents disagree, this file wins.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — milestone-by-milestone build order.
- [`design/SRS_Document.md`](design/SRS_Document.md) — full requirements specification.
- [`design/`](design/) — all other design documents, presentations, and architecture diagrams.
- [`research/XJTU_SY_MODEL_CARD.md`](research/XJTU_SY_MODEL_CARD.md) — RUL dataset choice, training, validation, and real-time limitations.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — the living data-model, ingestion, storage, telemetry, and report-catalog reference.

## Repository layout

- `design/` — SRS, diagrams, presentations, and other design-phase documents.
- `src/` — Python implementation source, organized per the milestones in `IMPLEMENTATION_PLAN.md` (ingestion, preprocessing, features, prediction, training, root_cause, alerts, storage, maintenance, kpi, api).
- `frontend/` — React + TypeScript + Vite + Tailwind dashboard (built to `frontend/dist`, served by the API).
- `tests/` — backend pytest suite; frontend tests live under `frontend/src/**/*.test.tsx`.
- `outputs/` — generated build artifacts (not tracked in git).

## Running it

Prerequisites: Python 3.12+, Node.js 20+.

```bash
# 1. Backend deps
pip install -r requirements.txt

# 2. Generate the SQLite DB from the dataset (one time)
python -m src.training.run_pipeline

# 3. Seed demo login accounts (admin/supervisor/operator - see Demo below)
python -m src.auth.seed

# 4. Build the dashboard
cd frontend
npm install
cp .env.example .env        # optional; defaults work out of the box
npm run build
cd ..

# 5. Serve API + dashboard together on http://localhost:8000
uvicorn src.api.app:app --reload
```

### Environment variables

All optional — sensible defaults let everything run with zero config. Set these
in `.env` (copied from `.env.example`) or your shell environment.

**`.env` is read only by `docker compose`** (its `env_file`). The app itself
never loads it, so for a host run either pass it to uvicorn —
`uvicorn src.api.app:app --env-file .env` — or export it into the shell first
(`set -a; . ./.env; set +a`). Otherwise VAPID keys, `MAINTAINIQ_SWEEP_INTERVAL_S`,
`ESCALATION_*`, `MQTT_*` and the rest set only in `.env` are silently ignored
(push reports `enabled: false`, paging and the device watchdog stay off).

| Variable | Default | Purpose |
| --- | --- | --- |
| `JWT_SECRET` | `dev-secret-change-me-in-production-32bytes-min` | Signs the `miq_session` auth cookie. Set a real secret before deploying anywhere but localhost. |
| `SMTP_HOST` | `localhost` | SMTP server for alert emails. Point at Mailpit (see below) in dev/demo. |
| `SMTP_PORT` | `1025` | SMTP port. Mailpit's default SMTP listener. |
| `SMTP_FROM` | `alerts@maintainiq.local` | From-address on outgoing alert emails. |
| `APP_PORT` | `8000` | Host port the API/dashboard is published on (Docker only). |
| `MAILPIT_SMTP_PORT` | `1025` | Host port for Mailpit's SMTP listener (Docker only). |
| `MAILPIT_WEB_PORT` | `8025` | Host port for Mailpit's web inbox (Docker only). |
| `MQTT_BROKER_HOST` | empty | MQTT broker for live telemetry ingest (M6). Empty disables ingest. All `MQTT_*` variables are described in `.env.example` and [Live telemetry (M6)](#live-telemetry-m6). |
| `MQTT_PORT` | `1883` | Host port for the Mosquitto broker (Docker only). |
| `MAINTAINIQ_SWEEP_INTERVAL_S` | `0` | Background job tick in seconds. `0`/unset runs no background jobs, so no sensor-node silence alerting. A non-numeric or negative value is logged and jobs stay off. `5` is recommended; `docker-compose.yml` defaults it to `5` because the compose stack always runs MQTT ingest. |
| `DEVICE_SILENT_GRACE_S` | `30` | Seconds the MQTT connection must be continuously up before the silence watchdog may open or resolve device incidents (covers retained-status replay and reconnect backlog). Raise it for nodes with heartbeats longer than ~20 s. Invalid values are logged and the default is used. |
| `ESCALATION_L1_MINUTES` | `15` | Minutes an open alert may stay unacknowledged, with no active work order, before supervisors are re-paged (paging level 1). Measured from when the alert was created. Needs `MAINTAINIQ_SWEEP_INTERVAL_S > 0`. |
| `ESCALATION_L2_MINUTES` | `30` | Minutes before admins are paged (level 2, the last level). Must be greater than `ESCALATION_L1_MINUTES`; invalid values are logged and both defaults are used. |
| `VAPID_PUBLIC_KEY` | empty | Base64url uncompressed P-256 public key for Web Push (the browser's `applicationServerKey`). Push is off unless both VAPID keys are set and valid. Generate with `python -m src.notifications.push --generate-vapid`. See [Mobile & push (PWA)](#mobile--push-pwa). |
| `VAPID_PRIVATE_KEY` | empty | Base64url private key matching the public key. A secret: never logged, never sent to the browser. |
| `VAPID_SUBJECT` | `mailto:` + `SMTP_FROM` | Contact URI sent to push services (`mailto:` or `https://`). Set a real address in production; Apple's push service rejects subjects it cannot resolve. An invalid value is logged and push stays off. |
| `PUSH_ALLOWED_HOSTS` | the six push-service hosts below | Comma-separated host suffixes that subscription endpoints may use. Replaces the default list (`fcm.googleapis.com`, `android.googleapis.com`, `updates.push.services.mozilla.com`, `push.services.mozilla.com`, `notify.windows.com`, `push.apple.com`). |

### Demo login accounts

Seeded by `python -m src.auth.seed` (idempotent — safe to re-run):

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@maintainiq.local` | `Admin123!` |
| Supervisor | `supervisor@maintainiq.local` | `Supervisor123!` |
| Operator | `operator@maintainiq.local` | `Operator123!` |

These are demo-only credentials, not a real secret boundary — don't reuse this
seed step against a production database.

### Running it with Docker

Prerequisite: generate `maintainiq.db` once on the host (step 2 above needs
the raw XJTU-SY dataset, which isn't bundled into the image), and seed the demo
login accounts into that same file before copying it in.

```bash
python -m src.auth.seed     # writes demo users into ./maintainiq.db

cp .env.example .env   # optional; defaults work out of the box
docker compose up --build
```

The dashboard + API are then served at http://localhost:8000, and Mailpit's
web inbox (every alert email the demo sends) at http://localhost:8025.
The repo-root `./maintainiq.db` is bind-mounted into the container (at
`/app/data/maintainiq.db`), so the container serves exactly the DB you
generated and seeded on the host, and its writes survive rebuilds.

### Frontend development (hot reload)

Run the backend on :8000 (`uvicorn src.api.app:app`) in one terminal, then in
another:

```bash
cd frontend
npm run dev          # http://localhost:5173, proxies /api -> :8000
```

### Mobile & push (PWA)

The operator view at `/m` is built for a phone: `/m/alerts` (open alerts with
large **Acknowledge**, **Work order**, **Close** and **Why?** buttons),
`/m/alerts/{id}`, `/m/machines`, `/m/machines/{id}`, `/m/scan` (QR labels) and
`/m/settings` (push toggle, log out). Every role can use it. It is installable
(manifest + service worker) and launches straight into `/m`. The service worker
only exists in `npm run build` output, never under `npm run dev`. Design:
[`design/2026-10-07-mobile-operator-pwa-design.md`](design/2026-10-07-mobile-operator-pwa-design.md).

**Push notifications** (Web Push, VAPID). Generate keys once and put them in
`.env` — and on a host run start uvicorn with `--env-file .env` (see
[Environment variables](#environment-variables)), or the keys are never read:

```bash
python -m src.notifications.push --generate-vapid   # prints the three .env lines
```

Without keys push is simply off (`GET /api/push/vapid-public-key` reports
`enabled: false`) and email works exactly as before. Each user opts in per
device from `/m/settings` (or the desktop header). Who gets pushed:

| Trigger | Email | Push |
| --- | --- | --- |
| Alert opened / severity up (level 0) | admin, supervisor | admin, supervisor, **operator** |
| Paging ladder level 1 | supervisor | supervisor |
| Paging ladder level 2 | admin | admin |
| Sensor-node silence | admin, supervisor | admin, supervisor, **operator** |
| Resolutions, acknowledgements, closes, work orders | — | — |

Every push attempt is a row in the notifications log with `channel = 'push'`.
A subscription the push service reports gone (404/410) is deactivated.
Subscription endpoints must be `https` on a known push-service host
(`PUSH_ALLOWED_HOSTS`).

- `GET /api/push/vapid-public-key` — any role; `{enabled, public_key}`
- `POST /api/push/subscribe` — any role; body is `PushSubscription.toJSON()`;
  upserts on the endpoint and binds it to the caller (503 when push is off, 400
  for a disallowed endpoint or malformed keys)
- `DELETE /api/push/subscribe` — any role; body `{endpoint}`; removes the
  caller's own row; always 204
- `POST /api/push/test` — any role; pushes the caller's own devices (409 when
  they have none)
- `GET /api/notifications?channel=email|push` — admin, supervisor

**HTTPS.** Service workers and push need a secure context.
`http://localhost:8000` is one on the machine itself, so desktop testing works
with the built SPA (`npm run build`, then `uvicorn src.api.app:app --env-file .env`). A phone
reaching the laptop over the LAN (`http://192.168.x.y:8000`) is **not**: the app
works but cannot be installed with a service worker or receive push. Either:

- **mkcert** — the files are `.pem`, not `.crt`, on purpose: the Dockerfile
  trusts every `certs/*.crt` as a CA, and `certs/` is git-ignored.

  ```bash
  mkcert -install
  mkcert -key-file certs/dev-key.pem -cert-file certs/dev-cert.pem <lan-ip> localhost
  uvicorn src.api.app:app --env-file .env --host 0.0.0.0 --ssl-keyfile certs/dev-key.pem --ssl-certfile certs/dev-cert.pem
  ```

  then install mkcert's `rootCA.pem` on the phone (Android: Settings → Security
  → Install certificate; iOS: install the profile, then enable it under
  Certificate Trust Settings); or
- **a tunnel** that terminates TLS with a public certificate
  (`cloudflared tunnel --url http://localhost:8000`, or ngrok).

On iOS/iPadOS (16.4+) push works only after **Add to Home Screen**, and
permission must be requested from a tap (the toggle does). iOS has no in-app QR
scanner; point the phone's camera app at a label instead.

**QR labels.** The "QR label" button on a machine page prints a label encoding
`<origin>/m/machines/{id}`. Print it from the HTTPS origin phones will use — a
label printed from `localhost` points nowhere on the shop floor.

### Demo script: live fault cascade

Shows realtime dashboard updates and realtime email alerts end-to-end,
without any hardware — the admin-only **Demo Control** page (`/demo`) injects
a synthetic sensor reading through the exact same prediction → alert →
notify → broadcast pipeline that live MQTT telemetry uses (see
[Live telemetry (M6)](#live-telemetry-m6) for the continuous, simulator-driven
version of this demo).

1. Open two browsers (or one regular + one private/incognito window):
   - Browser A: log in as `supervisor@maintainiq.local` / `Supervisor123!`.
   - Browser B: log in as `admin@maintainiq.local` / `Admin123!`.
2. In a third tab, open Mailpit's web inbox — `http://localhost:8025` (Docker)
   or wherever `SMTP_HOST`/`SMTP_PORT` point in your local setup.
3. In Browser A, leave the Dashboard open so you can watch it update live.
4. In Browser B, go to **Demo Control** (`/demo`), pick a machine, set
   severity to `critical`, and click **Simulate reading**.
5. Watch all three surfaces update within about a second, with no manual
   refresh:
   - Both dashboards' machine health/risk score update over the shared
     WebSocket, and a toast notification appears in each browser.
   - The Mailpit inbox receives new alert emails addressed to the admin and
     supervisor accounts.
   - The **Notifications** page (`/notifications`) logs a delivery row per
     recipient, and **Analytics** (`/analytics`) reflects the machine's new
     risk ranking.

Once the backend is running, admins/supervisors can start streaming replay
from the Ingestion page (or `POST /api/ingestion/replay/start`) to drive the
dashboard from stored XJTU-SY cycles in near-real-time.

### Tests

```bash
python -m pytest              # backend
cd frontend && npm run test   # frontend, Vitest
```

## Live telemetry (M6)

M6 replaces the dataset input with a live feed over MQTT. Each node (a
simulated one, or an ESP32 with an accelerometer) publishes vibration
snapshots to a Mosquitto broker. The app subscribes and runs every snapshot
through the same prediction → alert → notify → broadcast path that replay and
`/demo` use (`src/prediction/pipeline.py`). The wire contract (topics,
payloads, tables, env vars, KPI formulas) is
[`design/M6_LIVE_TELEMETRY.md`](design/M6_LIVE_TELEMETRY.md).

```
 ESP32 node / simulator              Mosquitto (deploy/mosquitto/)         MaintainIQ app
 +----------------------+  QoS 1    +--------------------------+  QoS 1  +-----------------------------+
 | capture snapshot     |-telemetry>| 1 MiB cap, persistence,  |-------->| mqtt_service (paho thread)  |
 | edge FIFO buffer     | /{machine}| retained status/alerts,  |         |  -> bounded queue           |
 | (drop-oldest, flush  |-status--->| queues QoS-1 for the     |         | ingest worker: validate ->  |
 |  oldest-first on     | /{device} | app's persistent session |         |  reading -> RUL predict ->  |
 |  reconnect)          | (+ LWT)   |                          |         |  pipeline.handle_prediction |
 | status LED           |<-alerts---|                          |<--------| alert listener (any source) |
 +----------------------+ /{machine}+--------------------------+         +-----------------------------+
```

Buffering happens in two places. If the device loses the network, it keeps
capturing into its edge buffer and flushes that buffer oldest-first
(`buffered: true`) before sending new data. If the app is down, the broker
holds QoS-1 telemetry for the app's fixed client id and delivers it when the
app reconnects.

### Quick start (broker in Docker, app and simulator on the host)

Prerequisites: Docker, plus the setup in [Running it](#running-it).

```bash
just broker                                    # Mosquitto on localhost:1883
just broker-check                              # optional: publish/subscribe roundtrip
MQTT_BROKER_HOST=localhost just serve          # terminal 1: app with MQTT ingest on
just simulate --devices 3 --outage-every 60    # terminal 2: 3 simulated nodes
```

Simulated machines `sim-01`, `sim-02` and so on are registered automatically
(`MQTT_AUTO_REGISTER=1`) and appear on the dashboard. Their health worsens as
the synthetic fault grows. `--outage-every 60` drops each node's connection
for about 20 s every minute, so you can watch the edge buffer fill and flush
and the device go offline and come back. Useful simulator flags (the full list
is in `python -m src.telemetry.simulator --help`):

- `--source xjtu` replays real XJTU-SY run-to-failure snapshots from `local_data/`.
- `--loss-rate 0.05` loses 5% of snapshots, which lowers the transmission KPI.
- `--count N` stops after N snapshots per device.
- `--encoding i16le-b64` switches to the ESP32's wire format.

Without `just`, the equivalents are `docker compose up -d mosquitto`, then
`MQTT_BROKER_HOST=localhost uvicorn src.api.app:app` and
`python -m src.telemetry.simulator --devices 3 --outage-every 60`.

Ingest status and per-device health are at `GET /api/telemetry/status` and
`GET /api/telemetry/devices`. The system KPIs are in `GET /api/kpis/detail`.

#### Device health (sensor-node silence)

Every node is classified by one rule (`src/telemetry/device_health.py`):
`online`, `stale` (no message for 1.5–3 heartbeats), `offline` (3 heartbeats,
or a last-will/clean-shutdown status) or `never_reported` (sent snapshots but
never a status). With `MAINTAINIQ_SWEEP_INTERVAL_S` set, a background watchdog
opens a `device_incidents` row when a node goes offline, emails admins and
supervisors (not again if the same node re-opened within 15 minutes), and pushes
a `device_offline` event to every dashboard; when the node is heard again the
incident resolves and a `device_online` event follows. It acts only while live
MQTT ingest is connected and past `DEVICE_SILENT_GRACE_S`, so a broker outage on
our side never pages anyone. Note that on the first armed tick after
deployment every node that is already silent gets an incident and an email.

- `GET /api/telemetry/devices?machine_id=` — every node with `state`,
  `silent_for_s`, `expected_heartbeat_s` and `open_incident_id`
- `GET /api/telemetry/devices/{device_id}` — one node plus its 10 newest incidents
- `GET /api/telemetry/incidents?status=open|resolved&device_id=&limit=` — incident history
- `POST /api/telemetry/incidents/{id}/acknowledge` — any role; idempotent
- `GET /api/telemetry/status` — now includes a `device_watchdog` block
  (`enabled`, `interval_s`, `grace_s`, `armed`, `last_tick_at`)

The dashboard shows all of this on the **Devices** page (Monitor section, every
role) and as a "Sensor node" chip on each machine's detail page. Design:
[`design/2026-10-06-device-health-design.md`](design/2026-10-06-device-health-design.md).

#### Work orders and paging

An alert can be turned into a **work order**: a tracked repair job with an
owner, a status (`open → assigned → in_progress → done`, or `cancelled`) and an
audit trail. Any role can raise one from an alert, which also acknowledges the
alert; admins and supervisors can raise free-standing ones, assign or reassign,
edit and cancel; the assignee (or an admin/supervisor) starts and completes it.
Completing an order writes a corrective (or preventive) maintenance record and
links it. There is at most one active order per alert (a second one is a 409).

Alerts that nobody acknowledges climb a **paging ladder**. The email sent when
an alert opens or worsens is level 0, as before. With `MAINTAINIQ_SWEEP_INTERVAL_S`
set, a background job re-pages supervisors once the alert has been open,
unacknowledged and without an active work order for `ESCALATION_L1_MINUTES`
(level 1), then admins after `ESCALATION_L2_MINUTES` (level 2, the last). Each
level pages once, with an `[Unacknowledged — page n]` subject, and broadcasts an
`alert_paged` event. When Web Push is configured, each level is also pushed to
the same roles ([Mobile & push](#mobile--push-pwa)). Acknowledging the alert or raising a work order stops it.
Alerts that predate the ladder are never paged by it: upgrading a database
marks its existing open alerts as past the top level (no "Paged" badge), and the
legacy batch seed does the same for the historical alerts it writes. Pages are
sent on a separate delivery thread, so a slow or unreachable SMTP server never
delays `alert_paged` events, other pages or the device watchdog.

- `GET /api/work-orders?status=open|assigned|in_progress|done|cancelled|active&machine_id=&assigned_to=&alert_id=&limit=` — any role
- `GET /api/work-orders/{id}` — the order, its event timeline and the linked alert
- `GET /api/work-orders/assignees` — admin, supervisor; active users without emails
- `POST /api/work-orders` — admin, supervisor; free-standing order
- `PATCH /api/work-orders/{id}` — admin, supervisor; title/description/priority/due date
- `POST /api/work-orders/{id}/assign` — admin, supervisor
- `POST /api/work-orders/{id}/start`, `/complete` — the assignee, admin, supervisor
- `POST /api/work-orders/{id}/cancel` — admin, supervisor
- `POST /api/alerts/{id}/work-order` — any role (assigning on create: admin, supervisor)

Alerts now also carry `page_level`, `last_paged_at` and `active_work_order_id`,
and the KPIs report open work orders. Realtime events: `work_order_created`,
`work_order_updated` (with `change`) and `alert_paged`. Design:
[`design/2026-10-07-work-orders-escalation-design.md`](design/2026-10-07-work-orders-escalation-design.md).

#### Prediction feedback and field accuracy

When an alert is **closed**, the person closing it records what actually
happened: `confirmed_failure` (optionally with the failure time),
`maintenance_prevented` (a real fault fixed in time), `false_alarm` or
`unknown`, plus the actual cause (the classifier's labels or `other`), notes and
the work order that handled it. Any role can close an alert or record an
outcome; only the person who recorded it, an admin or a supervisor can change
it afterwards. An outcome can also be recorded for an alert that already
auto-resolved.

Closing is a human resolve, not a new status: the alert becomes `resolved`
(with `closed_by` and `resolved_at` = the close time), is acknowledged if nobody
had, and stops paging. **If the machine still reads abnormal, the next reading
opens a new alert** (and a new level-0 email) — closing records a judgement
about *this* episode; a work order is the way to track an unfinished repair.
Closing does not complete or cancel the alert's work order. Human closures now
count towards `avg_alert_resolution_hours`.

New alerts record the `prediction_id`, `reading_id` and `model_version` that
opened them, so each outcome can be scored against the exact prediction.
`GET /api/model/feedback-accuracy` reports precision, false-alarm rate, lead
time against the 120-minute horizon, RUL error (point estimates only — lower
bounds are censored), root-cause accuracy and a per-model-version breakdown,
next to the offline benchmark; demo alerts are excluded and missed failures
can't be measured from alert feedback. The KPI `prediction` block gains a
`real_world` summary (and a real `root_cause_accuracy` once causes are
labelled), and the `model_performance` report a "Real-World Accuracy" section.

- `POST /api/alerts/{id}/close` — any role; body `{outcome, actual_cause?, actual_failure_at?, notes?, work_order_id?}`; returns `{alert, feedback, closed}` (`closed: false` if it had already resolved)
- `PUT /api/alerts/{id}/feedback` — any role to create; recorder, admin or supervisor to replace (full replacement)
- `GET /api/alerts/{id}/feedback` — any role; `null` when none
- `GET /api/model/feedback-accuracy?model_version=&period_start=&period_end=` — any role
- `GET /api/model/feedback/export?model_version=` — admin, supervisor; retraining CSV (below)

Alert rows from the list and detail routes now embed `feedback`. Realtime
events: `alert_closed` and `alert_feedback_recorded`; neither is emailed. A
device never sees them as such, but a close is published to the device's alert
topic as the `alert_resolved` it implies, so the node's alert LED clears.
No new environment variables. Design:
[`design/2026-10-07-prediction-feedback-design.md`](design/2026-10-07-prediction-feedback-design.md).

#### Why this alert? (alert explanations)

Every alert can be explained, to any signed-in user, in five sections:

- **Triggering readings** — up to 30 of the machine's vibration readings ending
  at the reading that raised (or escalated) the alert, with the trigger marked.
  Vibration channels only.
- **Key factors** — the vibration features that moved furthest from *this
  machine's own* baseline (its first 20 readings), weighted by how much the
  model relies on them, with direction (↑/↓) and an "outside training range"
  flag. Shaft speed and load are shown as operating conditions, not ranked.
  The weighting and the training-range check need the model to be loaded in
  the running app (it is, once anything has predicted); otherwise the factors
  are listed unweighted and the range is "unknown". This is a heuristic, not a
  per-prediction attribution.
- **Probable cause** — the label plus the rule trace that produced it (e.g.
  "kurtosis 9.4 ≥ 5.0"). Always a *probable cause, not a diagnosis*.
- **Model prediction** — failure probability, RUL with its 90% interval, and the
  out-of-distribution flag and warnings. OOD is always surfaced.
- **Similar past incidents** — earlier alerts with the same probable cause (or
  a recorded actual cause equal to it) or on the same machine, ranked by
  similarity, with what actually happened: the recorded outcome and actual
  cause, work orders and maintenance.

The first four are **snapshotted** when an alert opens and again at each
severity escalation, because the model's rolling context lives in memory and
an escalation overwrites the alert's probable cause. Alerts with no snapshot
(older alerts, batch backfill) are **reconstructed** from stored data on
request and labelled as such; similar incidents are always computed fresh.
Demo alerts are labelled **synthetic**, and a real alert never lists demo
incidents.

- `GET /api/alerts/{id}/explanation?similar_limit=5` — any role; `similar_limit`
  0–20; 404 `unknown alert: {id}`. Read-only.
- Dashboard deep link: `/alerts/{id}` opens the explanation drawer on the
  Alerts page.

No new environment variables. Design:
[`design/2026-10-07-alert-explanation-design.md`](design/2026-10-07-alert-explanation-design.md).

### Docker compose

`docker compose up --build` now starts the broker as well. The app container is
always pointed at it (`MQTT_BROKER_HOST=mosquitto`), and the app waits for the
broker's healthcheck to pass. The simulator is opt-in because it publishes
continuously:

```bash
docker compose up --build -d
docker compose --profile sim up -d simulator      # 3 synthetic nodes
docker compose logs -f mosquitto                  # connects, LWTs, rejects
docker compose --profile sim stop simulator
```

To use other simulator flags, edit the `simulator` service's `command` in
`docker-compose.yml`, or start a one-off container with
`docker compose run simulator python -m src.telemetry.simulator --broker mosquitto --source xjtu`.
`./local_data` is mounted read-only for this. The broker's data volume
(`mosquitto-data`) keeps retained status and alert messages across restarts.

### System KPIs

Until a live message has arrived, these four KPIs report `not_applicable`.
After that they are computed over a rolling window, 60 minutes by default:

| KPI | What it measures |
| --- | --- |
| `sensor_collection_rate` | Snapshots each device actually delivered divided by the number it should have captured at its reported `snapshot_interval_s`. |
| `transmission_success_rate` | Snapshots received divided by snapshots sent, using gaps in each device's `seq` numbers. Also reports the device's own publish-failure rate. |
| `edge_buffer_health` | How full each device's offline buffer is (`ok` / `degraded` / `critical`), how many snapshots were dropped, and what share of recent data arrived late from the buffer. |
| `cloud_sync_health` | Lag from capture to receipt (p50/p95), devices online vs total, age of the last message, and the rejected-message rate, summarized as `ok` / `degraded` / `down`. |

Exact formulas are in contract §9. `breakdown_reduction` and
`emergency_maintenance_reduction` stay `not_applicable`. They need
before-and-after data from a real plant, and a simulator cannot provide that.

### ESP32 node

Firmware for an ESP32 with an ADXL345 accelerometer is in `firmware/esp32-node/`
(PlatformIO). Wiring, configuration and flashing are covered in
[`firmware/esp32-node/README.md`](firmware/esp32-node/README.md). Point the
node's `include/config.h` at the machine running Mosquitto, port 1883. The
Docker broker only listens on `127.0.0.1` by default; set `MQTT_BIND=0.0.0.0`
in `.env` (isolated bench network only) so the node can reach it.

### Limitations

- **Dev broker security.** The dev broker accepts anonymous clients on plain
  port 1883, bound to `127.0.0.1` unless `MQTT_BIND` says otherwise. Anyone
  who can reach it can create machines (`MQTT_AUTO_REGISTER=1`) and trigger
  alert emails, so only open it up on an isolated bench network.
  `deploy/mosquitto/mosquitto.conf` has a commented password/TLS example, and
  the app supports `MQTT_USERNAME`/`MQTT_PASSWORD`/`MQTT_TLS`.
- **ADXL345 vs the model's training data.** The RUL model was trained on
  25.6 kHz XJTU-SY data, but an ADXL345 samples at most 3.2 kHz. Predictions
  from a real ADXL345 node will usually be flagged out-of-distribution. The
  pipeline, alerts and KPIs still work, but production nodes need a wide-band
  sensor (for example an ADXL1002, or an IEPE sensor with an I2S ADC).
- **Ingest throughput is bound by the model.** Each snapshot runs one RUL
  prediction on a single worker (to keep per-device order), about 1 s per
  32768-sample snapshot on an idle dev laptop and several seconds under load.
  That is below the default simulator rate (3 devices x one snapshot every
  2 s = 1.5/s): the backlog shows as `queue_depth` and a growing
  `last_message_age_s`, and past `MQTT_INGEST_QUEUE_MAX` snapshots are dropped
  (`queue_overflows`). For long runs use fewer devices or a longer
  `--interval`. Status/heartbeat messages have their own queue, so online
  status stays current even while snapshots back up.
- **One app instance.** `MQTT_CLIENT_ID` is fixed so the broker can queue
  telemetry while the app is down. Two app instances sharing that id will
  keep disconnecting each other.
- **Clock skew.** Lag figures are only as accurate as the device clocks.
  Snapshots from devices that are not NTP-synced (`time_synced: false`) are
  left out of the lag percentiles.
- **Broker queue limit.** Telemetry queued for an offline app is capped at
  1000 messages (`max_queued_messages`). Beyond that the broker drops
  messages, and they show up as lost in `transmission_success_rate`.

## Train the real run-to-failure RUL model

The XJTU-SY RUL pipeline is the platform's model. (The earlier IMS stage
classifier is retained only under `src/legacy/`, import-guarded, and is not
part of the running system.) Download and extract the official dataset, then
run:

```bash
python -m src.training.xjtu_rul --data-dir /path/to/XJTU-SY_Bearing_Datasets --rebuild-features
```

This creates `models/xjtu_rul_model.joblib` and an evaluation report using
strict leave-one-bearing-out validation. Full details and the real-time API
contract are in [`research/XJTU_SY_MODEL_CARD.md`](research/XJTU_SY_MODEL_CARD.md).

### Retraining with field feedback

Confirmed failures recorded with a failure time (see "Prediction feedback and
field accuracy") become extra run-to-failure trajectories. Export them, merged
onto the XJTU-SY feature table, then train exactly as above:

```bash
python -m src.feedback.export --out outputs/feedback_features.csv \
    --merge-with outputs/xjtu_features.csv --merged-out outputs/xjtu_plus_feedback.csv
python -m src.training.xjtu_rul --features-csv outputs/xjtu_plus_feedback.csv --no-register \
    --artifact outputs/xjtu_rul_candidate.joblib --report outputs/xjtu_rul_candidate_evaluation.json
```

(`--db PATH` and `--model-version V` are optional; admins and supervisors can
also download the field-only CSV from the Model page or
`GET /api/model/feedback/export`.) Training reads one CSV and needs at least
three trajectories, which is why the field episodes are merged rather than
trained alone. Retraining stays a deliberate manual step:

- Only live MQTT readings are exported; replayed XJTU-SY data is already in the
  table with exact labels.
- Live sensors differ from the XJTU-SY rig (sample rate, mounting) and
  `condition` is blank, so field episodes can shift the leave-one-bearing-out
  metrics. The command above writes a candidate next to, not over, the live
  `models/xjtu_rul_model.joblib`; compare its evaluation report with the
  current one, and only when it is better retrain into the default
  `--artifact` without `--no-register` (which registers it as the active model).
