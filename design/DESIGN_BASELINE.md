# Design Baseline

## Smart Predictive Maintenance Decision-Support System

This document is the single source of truth for project scope and design decisions. Where it conflicts with any other document in this repository (including `SRS_Document.md` or the diagrams), **this document wins**. It exists because the repo accumulated several rounds of design documents (project idea → feature expansion → SRS → PlantUML diagrams) that disagreed on scope in places. See `Technical_Presentation_Material.md`'s "Repository Analysis Summary" for the document history.

## Resolved scope decisions

| Topic | Decision | Why |
| --- | --- | --- |
| Hardware vs. dataset | Dataset-only pipeline first. Real ESP32 + sensor hardware is a later milestone, not a prerequisite. | The SRS already treats dataset-based demonstration as a valid, risk-mitigated path; none of the diagrams designed a dataset-input flow even though they assume live hardware. Building against a dataset lets the ML/pipeline work start immediately. |
| Machine scope | Multi-machine (factory-floor scenario) from the start. | Matches the diagrams (`multimachine_uml_class.puml`, `factory_scenario_description.puml`), which are the most concrete and recent design layer. Single-machine documents (`PROJECT_CONTEXT.md`, `Academic_Project_Presentation.md`) are superseded. |
| Temperature sensor | Mandatory for every machine, not optional. | Matches `feature_requirements_to_include.md` and the UML class diagram, which model it as a required part of every sensor module. Simplifies the data model. |
| Root cause identification | A real module in the MVP, not deferred. Rule-based to start. | The SRS defines it as a first-class feature (FR-23–26), but no diagram gave it a distinct module — it was folded into vague "alert logic." It needs an actual design, not a placeholder. |
| Alert triggering | ML-model-driven once a model is trained and validated, with the rule-based classifier kept as an always-available fallback. | The rule-based classifier alone under-uses the SRS's own ML requirements (FR-18–22). ML models generally outperform fixed thresholds, but the SRS also flags "limited labeled fault data" as a risk — the rule-based path stays as a safety net when ML confidence is low or a model isn't deployed yet. |
| ML model selection | Train several candidate models on the same feature set and benchmark them; deploy whichever wins, plus keep an unsupervised anomaly detector as an option when fault labels are scarce. Candidates: Random Forest, Gradient Boosting (XGBoost/LightGBM), SVM, Logistic Regression (baseline), Isolation Forest (unsupervised anomaly detection). A raw-signal deep model (1D-CNN/LSTM) is noted as a future option if enough labeled data becomes available. | The pipeline produces tabular feature vectors per window (RMS, peak, mean, std, optional FFT), which favors classical ML over deep learning for a small academic dataset. Isolation Forest specifically addresses the SRS's "limited labeled fault data" risk since it needs no fault labels. |
| Maintenance history | Manual entry via a dashboard form/API. | No diagram designed how maintenance records get into the system; SRS's KPIs depend on "last maintenance date" existing. Manual entry is the simplest way to close that gap for a prototype. |
| Backend/ML stack | Python + FastAPI. | The only concrete technology hint across all documents (`training_inference_upstream_downstream.puml` assumes a FastAPI-style inference service); Python fits every other document's ML/data-processing expectations. |
| Storage | SQLite. | Matches the SRS's "lightweight database" option; no cloud dependency needed for an academic prototype. |
| Dashboard | Web dashboard. | Matches the SRS's default UI option; simplest to demo without extra infrastructure. |

## Documents superseded by this baseline

- `PROJECT_CONTEXT.md` (root) — single-machine, vibration-only framing
- `design/Academic_Project_Presentation.md` — same stale framing, kept for slide structure
- `design/Existing_vs_Proposed_System.md` — same stale framing, kept because the competitive analysis itself (reactive/scheduled/manual/threshold/industrial methods) is still valid

## Documents still authoritative for their subject matter

- `design/SRS_Document.md` — functional/non-functional requirements, use cases, KPIs (scope items above override where they conflict)
- `design/diagrams/*.puml` — architecture, hardware, and protocol detail (still the reference for the eventual ESP32/MQTT milestone)
- `design/Technical_Presentation_Material.md` — architecture diagrams and reasoning, still accurate

See `IMPLEMENTATION_PLAN.md` (repo root) for how these decisions translate into build milestones.
