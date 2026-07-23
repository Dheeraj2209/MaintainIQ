# Predictive Maintenance — NASA IMS Bearing Dataset

End-to-end predictive maintenance pipeline built on the IMS Bearing Dataset
(NASA Prognostics Data Repository). Covers all three available runs:
**Test 1**, **Test 2**, and **Test 3** (documented failure: Bearing 3 outer
race, packaged by NASA under the folder name `4th_test` — a known quirk of
the source archive, not a real fourth test).

## Contents

```
predictive_maintenance_ims_bearing.ipynb   <- main notebook, fully executed with outputs
src/
  features.py                              <- feature extraction (handles 1- or 2-channel-per-bearing tests)
  pipeline.py                              <- labeling logic + all 3 model training functions, multi-test aware
outputs/
  features_test1.csv                       <- cached feature table, Test 1 (1,643 x 120)
  features_test2.csv                       <- cached feature table, Test 2 (984 x 62)
  features_test3.csv                       <- cached feature table, Test 3 (3,230 x 62)
data/
  1st_test/                                <- put Test 1 raw snapshot files here (not included)
  2nd_test/                                <- put Test 2 raw snapshot files here (not included)
  4th_test/txt/                            <- Test 3 raw snapshot files (not included; IMS packages it under this folder name)
```

## Running it

The notebook works out of the box using the cached feature CSVs — no need to
re-supply raw data to re-run it. To regenerate a feature table from scratch,
delete its CSV under `outputs/` and place the matching raw files under `data/`;
the notebook detects the missing cache and re-extracts automatically.

```bash
pip install pandas numpy scipy scikit-learn xgboost matplotlib jupyter
jupyter notebook predictive_maintenance_ims_bearing.ipynb
```

## Dataset summary

| Test | Bearings/channels | Documented failure | Data coverage |
|---|---|---|---|
| Test 1 | 4 bearings x 2 channels | B3 inner race, B4 roller element | Truncated — stops before actual failure |
| Test 2 | 4 bearings x 1 channel | B1 outer race | Full — includes actual failure (rig shutdown visible in last 2 readings) |
| Test 3 | 4 bearings x 1 channel | B3 outer race | Full — includes actual failure (rig shutdown visible in last reading) |

## What's implemented

1. **Feature extraction** — 15 time/frequency-domain features per channel
   per bearing (RMS, kurtosis, crest factor, FFT-based spectral features,
   etc.), handling both the 2-channel (Test 1) and 1-channel (Test 2/3)
   layouts via a per-test channel map.
2. **Labeling** — baseline z-score health index → `Normal`/`Degrading`/`Critical`
   stage (fixed thresholds, deliberately not re-tuned per test); RUL labels
   (true RUL for Test 2/3, proxy RUL for the truncated Test 1 — see the RUL
   caveat below, which applies to Test 1 and Test 3 both).
3. **Model 1 — Stage classifier** (RandomForest, time-based split), trained
   separately per test.
4. **Model 2 — RUL regressor** (XGBoost). Two validation modes: naive
   same-trajectory split (optimistic, shown only for contrast) and
   leave-one-trajectory-out across all **4** known failures from all three
   tests combined (rigorous — the model never sees the trajectory it's
   tested on, even across different physical rigs).
5. **Model 3 — Anomaly detector** (IsolationForest, trained only on each
   bearing's own healthy baseline period).

## Honest findings so far (see the notebook for full detail)

- Anomaly detection is the most reliable of the three approaches overall —
  but not perfectly: Test 3 shows a non-failing bearing (B2, 41.0% flagged)
  flagged *more* than the documented failure (B3, 23.4% flagged), a genuine
  discrepancy left unresolved rather than explained away.
- The same absolute health-score thresholds, calibrated once on Test 1,
  transfer imperfectly to both Test 2 and Test 3: moderate (non-failure)
  wear in other bearings crosses the same "Critical" cutoff as the genuine
  failure on each rig, though at clearly lower peak severity. Flagged rather
  than silently tuned away.
- A fixed time-based train/test split interacts badly with rapid-onset
  failures: Test 3's degradation is so late and fast that the stage
  classifier's training split contains almost no `Degrading`/`Critical`
  examples, and it collapses to predicting the majority class (67% accuracy,
  ~0% recall on the two failure stages) — a different, more fundamental
  problem than the label-noise story that explains Test 2's lower accuracy.
- Cross-test RUL transfer doesn't improve uniformly as trajectories are
  added: going from 3 to 4 trajectories helped Test 2's B1 fold a lot (MAE
  332.7h → 82.1h) and Test 1's B3 fold slightly, but made Test 1's B4 fold
  clearly worse (112.8h → 211.9h). The likely cause, found while adding
  Test 3, is below.
- **Data-quality finding:** checking Test 3 for acquisition gaps revealed
  that Test 1 has the same issue, worse. Test 1's wall-clock span is 67.6%
  data-collection downtime (not real operating time); Test 3's is 50.0%;
  Test 2 has none. RUL labels for Test 1 and Test 3 are wall-clock hours, so
  both include this inflation — a likely explanation for the uneven RUL
  transfer results above. Not fixed here (consistent with this project's
  practice of flagging problems rather than quietly tuning them away), but
  an important caveat for reading any cross-test RUL number.

## Known limitations (read before presenting results as final)

- **RUL semantics are inconsistent across tests.** Test 1's RUL is a
  *truncation proxy* (data stops before the real failure) and is also
  ~68% wall-clock downtime. Test 2's RUL is true, downtime-free operating
  hours. Test 3's RUL is true-failure but ~50% wall-clock downtime. The
  leave-one-trajectory-out MAE/RMSE numbers don't surface this on their
  own — an operating-hours-based RUL redefinition (dropping large gaps, or
  measuring in file-count instead of wall-clock time) is a clear next step.
- RUL regression has only **4** failure trajectories total (Test 1 B3/B4,
  Test 2 B1, Test 3 B3) — still a small sample for a production-grade
  cross-machine RUL model.
- Degradation-stage thresholds were derived from Test 1's own distribution,
  not an independent physical standard, and are known to over-flag moderate
  wear in both Test 2's and Test 3's non-failing bearings (see above).
- Cross-test RUL and anomaly models currently only use the primary (`h_`)
  channel features, since Test 1 has a second channel that Tests 2/3 don't —
  a design choice to keep trajectories comparable, at the cost of some
  information from Test 1's second channel.
- No per-bearing feature normalization across tests — raw feature scales
  differ between rigs, which likely limits cross-test RUL and anomaly
  transfer more than the modeling choices do.
- No physically-grounded fault-frequency features (BPFO/BPFI/BSF/FTF from
  the Rexnord ZA-2115 bearing geometry) — the current frequency features
  are generic band-energy proxies, not defect-frequency-specific.

## Next steps

- An operating-hours-based RUL redefinition for Test 1 and Test 3 (removing
  acquisition-gap inflation) before trusting cross-test RUL numbers further.
- Add bearing-specific fault-frequency features (BPFO/BPFI/BSF/FTF) for
  physically-grounded frequency features instead of the generic band-energy
  proxies used here.
- Per-bearing feature normalization before combining tests, to improve
  cross-test RUL and anomaly transfer.
- A different split strategy or minority-class oversampling for the stage
  classifier, to handle rapid-onset failures like Test 3's.
- Wrap the trained models behind a small FastAPI/Streamlit app that replays
  a dataset snapshot-by-snapshot to simulate real-time monitoring — a
  lightweight, local stand-in for the Raspberry Pi + Azure IoT Hub streaming
  setup used in the original reference project.
