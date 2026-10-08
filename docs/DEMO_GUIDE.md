# MaintainIQ demo guide

How to start the full stack and walk through every feature, in the order that
tells the story best. Verified end to end on 2026-10-08 (Windows 11, Docker
Desktop 29, Git Bash).

## 1. What runs where

Everything runs in Docker Compose. Run the commands from the repo root
(`C:\projects\MaintainIQ`), in Git Bash or PowerShell.

| Container | What it is | URL / port |
| --- | --- | --- |
| `app` | FastAPI backend + the built React dashboard, MQTT ingest, background jobs (device watchdog, alert paging) | http://localhost:8000 |
| `mailpit` | Fake mail server: catches every alert email | http://localhost:8025 (inbox), SMTP 1025 |
| `mosquitto` | MQTT broker the sensor nodes publish to | localhost:1883 (bound to 127.0.0.1) |
| `simulator` | 3 simulated ESP32 vibration nodes (`sim-01..03`), opt-in profile `sim` | no port; publishes to mosquitto |

The app serves `./maintainiq.db` from the repo root (bind-mounted), so what you
see in the browser is that file.

## 2. One-time setup

Only needed on a fresh machine or a fresh database. Skip if `maintainiq.db`
already exists.

```bash
pip install -r requirements.txt
# Load all 15 XJTU-SY bearings into the DB (idempotent; uses outputs/xjtu_features.csv,
# or pass --data-dir local_data/xjtu_full/XJTU-SY_Bearing_Datasets to rebuild it)
python -m src.ingestion.backfill
# Demo logins (idempotent)
python -m src.auth.seed
cp .env.example .env        # optional; every value has a working default
```

## 3. Start the demo

1. Start **Docker Desktop** and wait until it reports "Engine running".
2. Start the whole stack, simulator included:

   ```bash
   docker compose --profile sim up --build -d
   ```

   The first build takes a few minutes (frontend build + Python deps). Later
   starts take seconds.
3. Check that all four containers are up:

   ```bash
   docker compose --profile sim ps
   curl http://localhost:8000/api/health        # {"status":"ok"}
   ```

   `app`, `mailpit` and `mosquitto` should say `healthy`.

The app's first prediction for each machine after a start takes a few seconds
while it rebuilds that machine's state. The dashboard is fully live within about
a minute.

## 4. Logins

| Role | Email | Password | Can do |
| --- | --- | --- | --- |
| Admin | `admin@maintainiq.local` | `Admin123!` | everything, incl. Users and Demo control |
| Supervisor | `supervisor@maintainiq.local` | `Supervisor123!` | work orders, maintenance with health reset, reports |
| Operator | `operator@maintainiq.local` | `Operator123!` | own work orders, acknowledge, mobile view |

Admins and supervisors receive the alert emails; operators do not.

## 5. Demo script (about 15 minutes)

Open three tabs: **A** the app logged in as admin, **B** Mailpit
(http://localhost:8025), and optionally **C** a private window logged in as
supervisor, to show updates arriving live in a second session.

### Step 1 — Landing and login
- http://localhost:8000 → **Log in** → admin credentials.

### Step 2 — Fleet overview (Dashboard)
- **Machines grid**: 15 XJTU-SY bearings plus 3 live simulated nodes
  (`sim-01..03`). Each card shows its health state and risk.
- **Held** badges: a machine stays at its worst health state until maintenance
  resets it ("Held since …"), so it doesn't flap between healthy and critical.
- **Failure-detection F1** card: quality of the deployed XJTU-SY model (67.7%,
  version `xjtu-rul-20260930T164318Z`).
- **Machines at risk** and **Shortest remaining life** lists, and **Open alerts**
  at the bottom.

### Step 3 — Live sensor telemetry (Ingestion, Devices)
- **Ingestion** (`/ingestion`): broker connected, messages received and accepted,
  queue depth near 0, system KPIs (collection rate, transmission success,
  edge buffer, cloud sync), and the three simulated devices with RSSI and
  firmware.
- **Devices** (`/devices`): per-node online/offline state and incident history.

### Step 4 — Inject a fault (Demo control) → alert → email
- **Demo control** (`/demo`): pick a healthy or "unknown" machine (e.g.
  `Bearing3_5`), severity **critical**, click **Simulate reading**.
- The simulation log shows "Alert high severity · 2 email(s) sent".
- Tab **C** (supervisor) gets a toast and the dashboard updates, with no refresh.
- Tab **B** (Mailpit): two new "MaintainIQ CRITICAL: … needs attention" emails,
  to admin and supervisor.
- **Notifications** (`/notifications`) logs one delivery row per recipient.

### Step 5 — Why this alert? (Alerts)
- **Alerts** (`/alerts`) → on a model-generated alert (e.g. `Bearing1_1`)
  click **Why?**
- It shows the triggering readings chart, the key factors with their weights,
  and the model version.

### Step 6 — Repair workflow: work order → complete → health reset
Use a machine with a model alert (not one injected on Demo control: demo
alerts are kept out of health resets on purpose).

1. **Alerts** → **Create work order** on the alert → assign to *Otis Operator*
   → **Create work order** (this also acknowledges the alert).
2. **Work orders** (`/work-orders`) → open it → **Start work** → **Complete**.
3. The completion form is prefilled: type **Corrective (repair)**, and
   **"Component replaced / fault fixed — restart health tracking"** ticked.
   Add a note → **Confirm completion**.
4. Result:
   - the work order is **done** and the alert is **resolved**, with outcome
     "Prevented by maintenance";
   - the machine card shows **Reset, awaiting reading**, risk 0, and no stale
     remaining-life figure;
   - **Maintenance** (`/maintenance`) has the corrective record.

### Step 7 — Watch a bearing's whole life live (Ingestion → Dataset replay)
- **Ingestion** → **Dataset replay**: machine = the one you just repaired (e.g.
  `Bearing1_1`), **Speed × 60** → **Start replay**. Its 123-minute run plays
  in about 2 minutes through the real prediction pipeline.
- Open that machine's page (`/machines/Bearing1_1`) and watch:
  1. **Commissioning k/20**: the first 20 readings learn the new baseline and
     never alert.
  2. **Degrading** → **Faulty** → **Critical**, with remaining life counting
     down (about 110 min → 2 min).
  3. One alert opens and escalates with the machine. Mailpit receives a pair
     of emails (admin and supervisor) at each step: degrading, then faulty,
     then critical.

### Step 8 — Model, Analytics, Reports
- **Model** (`/model`): active model version, inference heartbeat, latency,
  out-of-distribution rate, and field-accuracy export for retraining.
- **Analytics** (`/analytics`): fleet health distribution, plus failure-
  detection F1, precision, recall, ROC AUC, false alarms and RUL error.
- **Reports** (`/reports`): generate a machine prognostic, fleet summary or
  model-performance report (markdown/JSON).

### Step 9 — Mobile operator view
- Click **Mobile view** (top bar), or open http://localhost:8000/m on a phone.
  For best effect use a narrow window or the browser's device toolbar (F12 →
  toggle device).
- Alert cards with Acknowledge / Work order / Close / Why?, plus **Scan** (QR
  labels from each machine page), **Machines** and **Settings**.

## 6. Stopping and resetting

```bash
docker compose --profile sim stop simulator   # stop only the simulated nodes
docker compose --profile sim down             # stop everything (DB and broker data persist)
docker compose --profile sim logs -f app      # follow the app log
```

**Resetting the database.** The demo writes alerts, work orders and
maintenance records into `./maintainiq.db`. Back it up before a demo, and
restore it afterwards with the stack stopped:

```bash
docker compose --profile sim down
cp maintainiq.db tmp/maintainiq.before-demo.db      # before
cp tmp/maintainiq.before-demo.db maintainiq.db      # after, to restore
```

## 7. Running without Docker (development)

```bash
docker compose up -d mosquitto mailpit                       # broker + mail only
MQTT_BROKER_HOST=localhost MAINTAINIQ_SWEEP_INTERVAL_S=5 \
  uvicorn src.api.app:app --reload                           # terminal 1 (API + built UI on :8000)
python -m src.telemetry.simulator --devices 3 --interval 5   # terminal 2
cd frontend && npm run dev                                   # optional: hot-reload UI on :5173
```

The app never reads `.env` itself. Pass it with `--env-file .env`, or export
the variables first.

## 8. Known limitations seen while demoing

- **Ingest throughput.** One serial worker predicts each snapshot in about 0.5 s,
  so the app keeps up with roughly 1.8 snapshots per second. The compose
  simulator runs 3 nodes at a 5 s interval (0.6/s). With more nodes, or the
  simulator's default 2 s interval, the dashboard falls behind.
- **Leftover alerts in the local DB.** 11 open alerts belong to machines that
  no longer exist (`test1_B1` … from the retired NASA IMS / temperature model).
  Close them on the Alerts page with **Close…** to tidy the list.
- **Seeded held states.** Machines that already had open alerts when the
  health-hold feature was deployed (migration 8) start in their held state
  (e.g. `Bearing1_3`, `Bearing1_4` degrading).
- **Dataset timestamps.** Replayed XJTU-SY machines carry synthetic
  timestamps starting 2020-01-01. That's expected, not a clock bug.
