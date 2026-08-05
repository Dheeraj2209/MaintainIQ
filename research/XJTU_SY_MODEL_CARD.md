# XJTU-SY RUL model card

## Decision

XJTU-SY is the primary research dataset for this project because its inputs
are real two-axis housing-vibration measurements from 15 bearings run until
failure under three shaft-speed/load conditions. C-MAPSS remains appropriate
for aircraft-engine prognostics, but its simulated channels do not match the
two accelerometers used by MaintainIQ.

This choice does not make the model universal. XJTU-SY contains accelerated
laboratory bearing failures, not field data from arbitrary fans, pumps, or
motors.

## Data preparation

The official CSV files are regular and documented, but raw vibration is not a
ready-made supervised table. The ingestion code:

1. validates two numeric, equal-length channels;
2. extracts time, spectrum, and band-pass envelope features from each 1.28 s
   snapshot;
3. orders snapshots by their numeric filename;
4. assigns RUL from the actual remaining one-minute snapshots; and
5. builds baseline and rolling statistics using only the current and earlier
   snapshots.

No synthetic temperature or rule-derived target is used by this RUL model.

## Model and validation

Early healthy vibration cannot reveal whether a bearing will last 41 or 2,537
minutes. The deployed model therefore uses two stages:

1. three Extra Trees classifiers vote on whether failure is within 120
   minutes, using 203 scale-robust features;
2. only after a probability of at least 0.60 persists for three snapshots,
   an Extra Trees regressor estimates exact RUL using all 342 features.

Outside the horizon, the API returns `RUL > 120 minutes` instead of inventing
an exact lifetime. Probability uses a causal three-snapshot median. The live
service retains the first 20 commissioning snapshots as its baseline and the
most recent 60 snapshots for rolling features.

Validation is leave-one-bearing-out: every one of the 15 folds tests one
complete bearing unseen during training. Randomly splitting adjacent windows
from the same bearing would leak machine identity and overstate accuracy.

Final unseen-bearing results over 9,216 snapshots:

- sustained-warning precision: 72.5%;
- sustained-warning recall: 63.4%;
- F1: 67.7%, average precision: 62.3%, ROC AUC: 81.4%;
- first warning before failure: 15/15 bearings;
- first warning inside the intended 120-minute horizon: 10/15 bearings;
- early first warning: 5/15 bearings, with no bearing receiving no warning;
- within-horizon RUL MAE: 30.8 minutes;
- within-horizon RUL RMSE: 37.8 minutes;
- 90th-percentile absolute RUL error: 62.8 minutes.

The 15-bearing sample is small, so these figures have substantial uncertainty.
The probability threshold and persistence rule were selected using the same
development dataset; independent target-machine validation is still required.

## Download and train

Download the dataset from the [XJTU-SY authors' page](https://biaowang.tech/xjtu-sy-bearing-datasets/),
extract it, and run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m src.training.xjtu_rul `
  --data-dir ".\data\rar_parts\XJTU-SY_Bearing_Datasets" `
  --rebuild-features
```

Outputs:

- `outputs/xjtu_features.csv`: cached feature table;
- `models/xjtu_rul_model.joblib`: deployable model and inference metadata;
- `models/xjtu_rul_evaluation.json`: leave-one-bearing-out evaluation.

A normal modern laptop can train this CPU model. Kaggle or Colab is optional
when local storage, RAM, or download speed is limited; a GPU is not required.

## Real-time API

Start the service:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.app:app --reload
```

POST `/api/predictions/rul` with `machine_id`, horizontal and vertical arrays,
`sample_rate_hz`, `speed_rpm`, and `load_kn`. To match training, send 32,768
points per axis sampled at 25.6 kHz once per minute. The response explicitly
flags warm-up, sample-rate mismatch, out-of-distribution input, lower-bound
estimates, and warnings that have not yet persisted for three snapshots.

Reset state after bearing replacement, maintenance, sensor movement, or a
machine identity change:

```text
DELETE /api/predictions/rul/{machine_id}/state
```

A replay of 123 real raw snapshots took 54.7 seconds on the development
laptop, approximately 0.44 seconds per snapshot. That demonstrates adequate
compute latency for one-minute input intervals; it is not an independent
accuracy test because the exported final model is trained on all 15 bearings.

## Safety and field deployment

This artifact is a research decision-support prototype, not a universal
machine-life oracle or a safety-certified shutdown system. Bearing life and
vibration depend on bearing type, mounting, lubrication, alignment, load,
speed, structure, environment, sensor, sensor location, and failure criteria.

Before field use:

1. mount the selected sensor consistently and record at the trained sampling
   configuration, or retrain for the actual sensor pipeline;
2. collect at least 20 known-healthy commissioning snapshots for every machine;
3. collect target-machine maintenance and failure history;
4. validate on complete machines and later time periods not used for tuning;
5. calibrate thresholds for the cost of false alarms versus missed failures;
6. monitor drift, missing data, sensor faults, and inference latency; and
7. keep manual inspection and conservative protection rules independent of the
   ML output.

Do not claim that the current XJTU-SY model works perfectly on real fans. A fan
dataset such as FAN-COIL-I can support fan-health or fault detection and sensor
procurement guidance, but it cannot supply true remaining-life labels unless
it contains documented run-to-failure trajectories.
