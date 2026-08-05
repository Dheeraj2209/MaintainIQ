# Fan Sensor & Dataset Catalog

Purpose: identify public datasets suitable for testing MaintainIQ against **fans**
(the equipment class called out in `SRS_Document.md`), catalog which physical
sensors produced that data, and give a user guide for choosing/wiring a real
sensor into the pipeline. Written 2026-08-04.

## 1. What MaintainIQ ingests today (baseline)

From `src/storage/db.py`, `src/features/vibration.py`, `src/features/temperature.py`:

| Field | Type | Source | Notes |
|---|---|---|---|
| `vibration_h_rms`, `_kurtosis`, `_high_band_energy_ratio` | REAL | accelerometer, horizontal axis | computed by `src/features/vibration.py` over a signal window |
| `temperature_c` | REAL | — | **currently synthetic**, derived from vibration RMS (`temperature.py`); no real thermal sensor is modeled yet |
| `rul_hours` | REAL | label | remaining-useful-life target |
| `features_json` | TEXT (sparse) | accelerometer | fuller feature set incl. vertical axis: mean/std/rms/peak/peak-to-peak/kurtosis/skewness/crest factor/shape factor/dominant freq/spectral centroid/spectral energy/low-mid-high band energy ratio, per axis (h/v) |

Ingestion paths: batch CSV loader shaped for the NASA IMS bearing format
(`src/ingestion/ims_bearing.py`) or single-row insert used by the demo fault
simulator (`src/prediction/live.py`). **There is no acoustic, current, RPM, or
pressure field anywhere in the schema today** — only vibration (2-axis) +
synthetic temperature. The SRS (`SRS_Document.md` L436-445) documents a wider
intended input contract (accel axis, temperature, timestamp, machine/sensor ID,
sampling rate, optional load/speed) that the code hasn't caught up to yet.

This means: datasets that are pure raw-accelerometer time series slot in with
the least new code (reuse `features/vibration.py`); anything acoustic, current,
or RPM-based requires a new feature-extraction module and a schema column
before it can reach the model.

## 2. Candidate datasets, ranked for "testing on fans"

| # | Dataset | Machine | Sensors | Sampling | Labels | License/Access | Fit |
|---|---|---|---|---|---|---|---|
| 1 | [FAN-COIL-I](https://figshare.com/articles/dataset/_i_Vibration_Sensor_Dataset_for_Estimating_Fan_Coil_Motor_Health_i_/25959403) ([paper](https://arxiv.org/abs/2408.14448)) | real HVAC fan-coil unit, 2-week continuous production deployment | 2x [Wilcoxon 786A](https://wilcoxon.com/wp-content/uploads/2022/11/786A_spec_98692E.2.pdf) piezoelectric accelerometer, forward + rear of motor | 32 kHz, 10s snapshot every 2 min, 5246 samples | unlabeled health trend (RMS velocity degradation), not discrete fault classes | Figshare, open | **Best direct match** — real fan, named/spec'd industrial sensor (100 mV/g, ±80g, 0.5Hz–14kHz, API 670 compliant), raw dual-channel waveform drops straight into `features/vibration.py` |
| 2 | [MAFAULDA](https://www02.smt.ufrj.br/~offshore/mfs/page_01.html) | rotating-machinery rig (motor+shaft+bearing simulator), not a fan but same rotating-machine physics fans share | tachometer (RPM) + 2x triaxial accelerometer (IMI 601A01 x3 + 604B31) on under/overhang bearings + Shure SM81 mic | 50 kHz, 5s segments, 1951 sequences, RPM 737–3686 | 6 classes: normal, imbalance, horiz./vert. misalignment, inner/outer bearing fault | free, academic (UFRJ) | Best for **validating feature-extraction correctness** against ground-truth fault labels the app doesn't currently have for any dataset |
| 3 | [MIMII Dataset](https://zenodo.org/records/3384388) ([paper](https://arxiv.org/abs/1909.09347)) | valves/pumps/**fans**/slide rails, real factory fans, 7 units/type | TAMAGO-03 8-ch circular mic array, 68mm diameter, 50cm from machine | 16 kHz/16-bit, 10s clips, 26k+ normal segments + anomalies mixed at +6/0/-6dB SNR | normal / anomalous per machine, per SNR | CC BY-SA 4.0 (Hitachi) | Real fans, but **acoustic only** — no vibration channel. Useful for a future "listen to the fan" feature, not the current pipeline. DCASE 2020+ Task 2 reuses this dataset as the standard ASD benchmark. |
| 4 | [IoT-Integrated Predictive Maintenance Dataset](https://www.kaggle.com/datasets/ziya07/iot-integrated-predictive-maintenance-dataset) | synthetic, generic production-line machines | simulated vibration + acoustic + temperature + current, plus decomposed IMF1-3 features | synthetic timestamps | fault class label per row | Kaggle (login), synthetic — not real hardware specs | Not a real fan or real sensor, but its **column shape (timestamp, machine_id, vibration, acoustic, temperature, current)** is the closest tabular match to the SRS's full intended input contract — good for schema/API smoke-testing before real multi-sensor hardware exists |
| 5 | [AI4I 2020 Predictive Maintenance](https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset) | synthetic milling machine (not a fan) | air/process temperature, rotational speed, torque, tool wear (no vibration) | synthetic | binary + 5-class failure mode | CC BY 4.0 | Not fan-relevant sensor-wise; only useful as a generic "does our classifier pipeline work on tabular PdM data" sanity check |
| — | NASA IMS Bearing (already integrated, `src/ingestion/ims_bearing.py`) | bearing test rig | accelerometers | — | run-to-failure | public (NASA PCoE) | Already in the codebase; bearing-only, not a fan, but it's the reason the current schema is vibration-shaped the way it is |

## 3. Sensor catalog — what to actually buy/wire for a real fan deployment

| Signal | Physical sensor examples | Typical spec range (from datasets above) | Maps to MaintainIQ field | Supported today? |
|---|---|---|---|---|
| Vibration (accel.) | MEMS: ADXL335/1002; industrial: IMI 601A01, 603C01, [Wilcoxon 786A](https://wilcoxon.com/wp-content/uploads/2022/11/786A_spec_98692E.2.pdf) | 1 kHz–50 kHz sampling, ±50–80g range, triaxial preferred (axial/radial/tangential) | `vibration_h_rms/_kurtosis/_high_band_energy_ratio`, `features_json` (h/v axes) | Yes — this is the app's core signal |
| Temperature | thermocouple, RTD, or digital (DS18B20), IR spot sensor | contact or non-contact, °C | `temperature_c` | Schema exists but **field is synthetic-only today** — first real sensor to wire up |
| RPM / speed | optical or magnetic tachometer | pulse-per-rev | none yet | **Gap** — needed to normalize vibration frequency features by shaft order; MAFAULDA shows why (fault signatures shift with RPM) |
| Acoustic | measurement mic (Shure SM81) or MEMS mic array (TAMAGO-03) | 16–48 kHz | none yet | **Gap** — would need a new feature-extraction module (spectrogram/MFCC) and `features_json` key |
| Current | CT clamp / Hall-effect current sensor | per-phase, synced to vibration | none yet | **Gap** — motor current signature analysis is a well-established fault channel (see the induction-motor dataset below) but nothing in the schema supports it |

Reference for current+vibration+voltage synchronized capture done right: [Comprehensive Fault Diagnosis of Three-Phase Induction Motors Using Synchronized Multi-Sensor Data Collection](https://www.nature.com/articles/s41597-025-05437-3) (Sci Data, 2025) — ADXL335 accelerometer + isolated per-phase voltage/current sensors + dSPACE synchronization, 50 kHz. Motor (not fan) but the sensor-fusion approach is the template if MaintainIQ ever adds current sensing.

## 4. Recommended test plan

1. **Start with FAN-COIL-I** — it's a real fan, raw accelerometer waveform, and requires zero schema changes: window the 32 kHz signal and run it through the existing `features/vibration.py` extraction, then through `prediction/ml_model.py`. This validates the pipeline end-to-end on genuine fan data.
2. **Cross-check feature correctness with MAFAULDA** — since FAN-COIL-I has no discrete fault labels, use MAFAULDA's labeled imbalance/misalignment/bearing-fault classes (same accelerometer-feature shape) to confirm the extracted features actually separate fault classes before trusting them on unlabeled fan data.
3. **Treat MIMII/acoustic as a roadmap item, not a near-term test** — it validates a real "fan" only acoustically; wiring it in means adding a new ingestion path and feature module, not just loading new CSVs.
4. **Use the synthetic IoT-Integrated dataset only for API/schema smoke-testing** (e.g., does an endpoint accept `{vibration, acoustic, temperature, current}` payloads) — not for model accuracy claims, since it's fully synthetic.
5. Before buying real hardware for a pilot fan: vibration (triaxial accelerometer) + RPM (tachometer) is the minimum upgrade over today's setup: RPM lets you normalize vibration order-tracking the way MAFAULDA's labeled data implies is necessary, and it's the cheapest gap to close of the three (RPM/acoustic/current).

## 5. Open gaps flagged for later

- `temperature_c` is synthetic everywhere in the app — no dataset above needs it, but a real fan pilot will need a real sensor before the field means anything.
- No RPM/tachometer field in the schema despite every labeled fault dataset (MAFAULDA, induction-motor paper) treating shaft speed as a required normalization input.
- No acoustic or current fields — both are established fault channels but are net-new engineering work (ingestion + feature extraction + schema), not just "load a new dataset."
