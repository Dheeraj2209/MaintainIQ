# Smart Predictive Maintenance Decision-Support System

A predictive maintenance decision-support system using vibration and temperature data, with multi-machine monitoring, rule-based and ML-driven fault prediction, root cause identification, and maintenance history tracking.

## Where to start

- [`design/DESIGN_BASELINE.md`](design/DESIGN_BASELINE.md) — canonical scope and design decisions. Where other design documents disagree, this file wins.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — milestone-by-milestone build order.
- [`design/SRS_Document.md`](design/SRS_Document.md) — full requirements specification.
- [`design/`](design/) — all other design documents, presentations, and architecture diagrams.

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

# 3. Build the dashboard
cd frontend
npm install
cp .env.example .env        # optional; defaults work out of the box
npm run build
cd ..

# 4. Serve API + dashboard together on http://localhost:8000
uvicorn src.api.app:app --reload
```

### Running it with Docker

Prerequisite: generate `maintainiq.db` once on the host (step 2 above needs
the raw IMS dataset, which isn't bundled into the image).

```bash
mkdir -p data
cp maintainiq.db data/maintainiq.db

cp .env.example .env   # optional; defaults work out of the box
docker compose up --build
```

The dashboard + API are then served at http://localhost:8000. `./data/` is
bind-mounted into the container so the DB survives rebuilds — rerun the `cp`
above whenever you regenerate `maintainiq.db` on the host.

### Frontend development (hot reload)

Run the backend on :8000 (`uvicorn src.api.app:app`) in one terminal, then in
another:

```bash
cd frontend
npm run dev          # http://localhost:5173, proxies /api -> :8000
```

### Tests

```bash
python -m pytest              # backend (31 tests)
cd frontend && npm run test   # frontend, Vitest (32 tests)
```
