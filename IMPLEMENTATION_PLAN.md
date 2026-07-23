# Implementation Plan

Build order for the Smart Predictive Maintenance Decision-Support System, translating `design/SRS_Document.md` and `design/DESIGN_BASELINE.md` into milestones. Each milestone should be demoable end-to-end before moving to the next.

## M1 — Dataset ingestion and feature pipeline
- Load a stored/simulated vibration + temperature dataset covering multiple machines.
- Tag each record with timestamp, machine ID, sensor ID.
- Preprocess: clean missing/invalid readings, normalize, window the signal.
- Extract features: mean, std, peak, RMS, optional FFT for vibration; current value, rolling average, rate of increase for temperature.
- Output: a feature table per machine per time window, ready for classification.

## M2a — Rule-based baseline
- Threshold/heuristic classifier mapping feature values to health state (healthy, degrading, faulty, critical).
- Rule-based root cause module mapping abnormal feature patterns to probable causes (bearing wear, misalignment, imbalance, overheating, excessive load, looseness, sensor/data-quality issue).
- This stays in the system permanently as the fallback path, not just a placeholder to delete later.

## M2b — Candidate ML model training and selection
- Offline training script using the M1 feature tables.
- Train multiple candidate models on the same train/test split: Random Forest, Gradient Boosting (XGBoost/LightGBM), SVM, Logistic Regression (baseline), Isolation Forest (unsupervised anomaly detection for when fault labels are scarce).
- Evaluate each on the SRS's own KPIs: prediction accuracy, false alarm count, missed fault count, confidence score.
- Export the winning model artifact (joblib/ONNX) plus its evaluation report.
- Note: a raw-signal deep model (1D-CNN/LSTM) is a documented future option, not part of this milestone.

## M3 — Storage and alert generation
- SQLite schema for: raw/processed readings, feature windows, predictions, alerts, maintenance records.
- Prediction routing: use the deployed ML model if available and confident; fall back to the M2a rule-based classifier otherwise.
- Alert generation from whichever prediction path was used, including severity, probable root cause, and duplicate-alert suppression for unresolved issues.

## M4 — Backend API and dashboard
- FastAPI service exposing machine status, trends, alerts, root cause, and KPIs.
- Web dashboard: current health per machine, vibration/temperature trend graphs, alert list, severity, root cause, combined risk score, KPI summary.

## M5 — Maintenance history
- Dashboard form/API for an operator to log a completed maintenance action and date.
- Wire last-maintenance-date and days-since-maintenance into the dashboard and into maintenance-priority/KPI calculations.

## M6 — Real hardware (future)
- ESP32 + DS18B20 (temperature) + ADXL345/MPU6050 (vibration) per machine, per `design/diagrams/sensor_selection_table.puml` and `design/diagrams/esp32_pin_interface_table.puml`.
- MQTT over Wi-Fi telemetry upstream, alert topic downstream, per `design/diagrams/communication_protocol_comparison.puml` and `design/diagrams/system_architecture_diagram.puml`.
- Replaces the M1 dataset input with live telemetry; the rest of the pipeline (M2–M5) is unchanged.

## Suggested `src/` layout

```
src/
  ingestion/       # M1: dataset/telemetry loading, tagging
  preprocessing/    # M1: cleaning, normalization, windowing
  features/         # M1: feature extraction
  prediction/
    rule_based.py   # M2a
    ml_model.py      # M2b: loads deployed model artifact
    router.py        # M3: ML-first, rule-based fallback
  training/          # M2b: candidate model registry, training runner, evaluation/leaderboard, export
  root_cause/        # M2a/M2b: root cause logic
  alerts/            # M3: alert generation, duplicate suppression
  storage/           # M3: SQLite schema and access
  maintenance/        # M5: maintenance log entry and priority logic
  kpi/                # M4/M5: KPI calculations
  api/                # M4: FastAPI service
  dashboard/          # M4: web dashboard
```
