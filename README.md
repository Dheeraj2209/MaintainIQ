# Smart Predictive Maintenance Decision-Support System

A predictive maintenance decision-support system using vibration and temperature data, with multi-machine monitoring, rule-based and ML-driven fault prediction, root cause identification, and maintenance history tracking.

## Where to start

- [`design/DESIGN_BASELINE.md`](design/DESIGN_BASELINE.md) — canonical scope and design decisions. Where other design documents disagree, this file wins.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — milestone-by-milestone build order.
- [`design/SRS_Document.md`](design/SRS_Document.md) — full requirements specification.
- [`design/`](design/) — all other design documents, presentations, and architecture diagrams.
- [`research/XJTU_SY_MODEL_CARD.md`](research/XJTU_SY_MODEL_CARD.md) — RUL dataset choice, training, validation, and real-time limitations.

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

| Variable | Default | Purpose |
| --- | --- | --- |
| `JWT_SECRET` | `dev-secret-change-me-in-production-32bytes-min` | Signs the `miq_session` auth cookie. Set a real secret before deploying anywhere but localhost. |
| `SMTP_HOST` | `localhost` | SMTP server for alert emails. Point at Mailpit (see below) in dev/demo. |
| `SMTP_PORT` | `1025` | SMTP port. Mailpit's default SMTP listener. |
| `SMTP_FROM` | `alerts@maintainiq.local` | From-address on outgoing alert emails. |
| `APP_PORT` | `8000` | Host port the API/dashboard is published on (Docker only). |
| `MAILPIT_SMTP_PORT` | `1025` | Host port for Mailpit's SMTP listener (Docker only). |
| `MAILPIT_WEB_PORT` | `8025` | Host port for Mailpit's web inbox (Docker only). |

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
the raw IMS dataset, which isn't bundled into the image), and seed the demo
login accounts into that same file before copying it in.

```bash
python -m src.auth.seed     # writes demo users into ./maintainiq.db

mkdir -p data
cp maintainiq.db data/maintainiq.db

cp .env.example .env   # optional; defaults work out of the box
docker compose up --build
```

The dashboard + API are then served at http://localhost:8000, and Mailpit's
web inbox (every alert email the demo sends) at http://localhost:8025.
`./data/` is bind-mounted into the container so the DB survives rebuilds —
rerun the `cp` above whenever you regenerate or re-seed `maintainiq.db` on
the host.

### Frontend development (hot reload)

Run the backend on :8000 (`uvicorn src.api.app:app`) in one terminal, then in
another:

```bash
cd frontend
npm run dev          # http://localhost:5173, proxies /api -> :8000
```

### Demo script: live fault cascade

Shows realtime dashboard updates and realtime email alerts end-to-end,
without any hardware — the admin-only **Demo Control** page (`/demo`) injects
a synthetic sensor reading through the exact same prediction → alert →
notify → broadcast pipeline a future live telemetry feed would use.

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

### Tests

```bash
python -m pytest              # backend
cd frontend && npm run test   # frontend, Vitest
```

## Train the real run-to-failure RUL model

The older IMS stage classifier remains for the original dashboard demo. For
an actual remaining-useful-life experiment, use the XJTU-SY pipeline. Download
and extract the official dataset, then run:

```bash
python -m src.training.xjtu_rul --data-dir /path/to/XJTU-SY_Bearing_Datasets --rebuild-features
```

This creates `models/xjtu_rul_model.joblib` and an evaluation report using
strict leave-one-bearing-out validation. Full details and the real-time API
contract are in [`research/XJTU_SY_MODEL_CARD.md`](research/XJTU_SY_MODEL_CARD.md).
