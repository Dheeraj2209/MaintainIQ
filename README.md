# Smart Predictive Maintenance Decision-Support System

A predictive maintenance decision-support system using vibration and temperature data, with multi-machine monitoring, rule-based and ML-driven fault prediction, root cause identification, and maintenance history tracking.

## Where to start

- [`design/DESIGN_BASELINE.md`](design/DESIGN_BASELINE.md) — canonical scope and design decisions. Where other design documents disagree, this file wins.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — milestone-by-milestone build order.
- [`design/SRS_Document.md`](design/SRS_Document.md) — full requirements specification.
- [`design/`](design/) — all other design documents, presentations, and architecture diagrams.

## Repository layout

- `design/` — SRS, diagrams, presentations, and other design-phase documents.
- `src/` — implementation source, organized per the milestones in `IMPLEMENTATION_PLAN.md` (ingestion, preprocessing, features, prediction, training, root_cause, alerts, storage, maintenance, kpi, api, dashboard).
- `outputs/` — generated build artifacts (not tracked in git).
