# FEMTO/PRONOSTIA -> XJTU-SY feature alignment (v3)

Goal: put the 17 FEMTO run-to-failure bearings into a table with **exactly** the columns, column order
and per-row semantics of `outputs/xjtu_features.csv`, so the pooled 32-bearing table can go through
`src.training.xjtu_rul.add_past_context` and the frozen v2 evaluator/harness unchanged.

Code (all under `experiments/xjtu/v3/`; nothing in `src/`, `models/`, `tests/`, `outputs/` or the frozen
v2 files was modified):

| File | Purpose |
|---|---|
| `femto_features.py` | Extracts features with the production `src.ingestion.xjtu_sy.extract_snapshot_features`, aggregates to 1 row per minute, writes `cache/femto_features.csv` and `cache/combined_features.csv` |
| `snapshot_length_study.py` | Quantifies the snapshot-length effect **on XJTU itself** (32768-sample snapshots vs FEMTO-like 2560-sample emulations) |
| `pooled_validate.py` | Schema, `add_past_context`, onset-GT and v2-harness checks on the pooled table; per-bearing stats; plot |
| `results/pooled_validation.json`, `results/pooled_bearing_summary.csv`, `results/rms_ratio_trajectories.png` | Validation output |
| `results/snapshot_length_study.{json,csv}` | Snapshot-length study output |

Run order (repo root): `python experiments/xjtu/v3/femto_features.py` (about 8.5 min, 3 workers; stage 1 is cached),
`python experiments/xjtu/v3/snapshot_length_study.py` (about 6 min), `python experiments/xjtu/v3/pooled_validate.py` (about 30 s).

## 1. What is the same

* **Sampling rate.** Both datasets sample at 25.6 kHz, so `extract_snapshot_features` is called with its
  default `sample_rate_hz` and every fixed-Hz band is meaningful. The bands are 0-500 ... 10-15 kHz
  (both effectively stop at the 12.8 kHz Nyquist), the 2-10 kHz envelope band-pass, and the 0-50 ... 500-1000 Hz
  envelope bands.
* **Channels.** FEMTO column 5 is horizontal acceleration and maps to XJTU `h_*`; column 6 is vertical
  and maps to `v_*`. The `m_*` (magnitude) and `cross_axis_*` features are built the same way.
* **Units / sensor gains.** Both datasets are in g. FEMTO uses DYTRAN 3035B (100 mV/g, 50 g range; it clips
  at about 48.1 g, see section 6). XJTU uses PCB 352C33 (100 mV/g, +-50 g). No gain correction is applied.
  Healthy RMS levels are similar: FEMTO 0.22-0.69 g in the first 20 minutes, XJTU about 0.4-1.7 g. Absolute
  levels still depend on mounting and test rig, so **only per-bearing-normalised features
  (`*_baseline_ratio`, the r5/z5/p5 indicators) should be compared across datasets.**
* **DC.** Both have negligible DC (|mean| < 0.01 g).

## 2. Snapshot length (2560 vs 32768 samples)

FEMTO records 0.1 s (2560 samples) every 10 s. XJTU records 1.28 s (32768 samples) every 60 s.
Consequences for the production features:

| Feature family | Effect of 2560 samples | Handling |
|---|---|---|
| FFT resolution | 10 Hz bins, vs 0.78 Hz | `dominant_freq` is quantised to 10 Hz and noisy. Band-energy ratios are unaffected because the bands are hundreds of Hz wide |
| `spectral_entropy` | Normalised by log(#bins); #bins = 1281 vs 16385 | Absolute values are not comparable across datasets; the within-bearing ratio is |
| Envelope spectrum | 10 Hz resolution: the 0-50 Hz band holds 5 bins, the 50-100 Hz band 5 bins, the 100-200 Hz band 10 bins. Filtfilt/Hilbert edge transients take a larger share of the window | The envelope-band ratios are noisier, but each defect-frequency band still holds several bins |
| Extreme-value stats (`peak`, `crest/impulse/clearance_factor`, `kurtosis`, `envelope_kurtosis`) | Fewer samples, so the expected maximum is lower and the estimator variance is higher | Handled by the aggregation choice below |

**Aggregation choice (one row = one minute).** I compared three ways of turning the 6 snapshots of a
minute into one feature row. The comparison used **XJTU raw data**: 1902 snapshots, stratified across all
15 bearings and covering every first-20 and last-20 row. I cut each 32768-sample snapshot into 6 evenly
spread 2560-sample chunks, which have non-contiguous joins just like FEMTO. I then checked how well each
emulation reproduces the production full-snapshot feature. The table shows within-bearing Spearman rho
(median over bearings) / median ratio emulation:full.

| Feature | **concat6** (6 chunks concatenated, 15360 samples) | median6 (median of 6 per-chunk vectors) | last1 (one 2560 chunk) |
|---|---|---|---|
| h_rms | 0.995 / 1.00 | 0.993 / 1.00 | 0.971 / 1.00 |
| h_peak | 0.929 / 0.99 | 0.876 / 0.82 | 0.834 / 0.82 |
| h_kurtosis | 0.884 / 0.98 | 0.866 / 0.83 | 0.668 / 0.88 |
| h_crest_factor | 0.849 / 0.99 | 0.686 / 0.82 | 0.478 / 0.81 |
| h_spectral_entropy | 0.963 / 1.03 | 0.931 / 1.00 | 0.854 / 1.00 |
| h_spectral_centroid | 0.991 / 1.00 | 0.986 / 1.00 | 0.911 / 1.00 |
| h_dominant_freq | 0.685 / 1.00 | 0.489 / 1.00 | 0.474 / 1.00 |
| h_energy_5000_10000_hz_ratio | 0.986 / 1.00 | 0.984 / 1.00 | 0.906 / 0.99 |
| h_envelope_rms | 0.994 / 1.00 | 0.992 / 1.00 | 0.969 / 1.00 |
| h_envelope_kurtosis | 0.829 / 0.99 | 0.754 / 0.76 | 0.540 / 0.78 |
| h_envelope_energy_0_50_hz_ratio | 0.891 / 0.96 | 0.829 / 0.76 | 0.547 / 0.77 |
| h_envelope_energy_100_200_hz_ratio | 0.877 / 0.99 | 0.850 / 0.98 | 0.587 / 0.97 |
| cross_axis_correlation | 0.972 / 1.00 | 0.971 / 1.01 | 0.903 / 1.00 |
| share of all 119 features with rho >= 0.9 | **70 %** | 62 % | 39 % |

**Decision: `concat6` is the primary mode.** It is the only mode whose extreme-value features keep the XJTU
scale (ratio about 0.98-0.99, vs about 0.8 for the other two). It also tracks the full-snapshot
trajectory best for every feature family. The joins in the concatenated signal add a little broadband
leakage, but it is measurable only in `spectral_entropy` (+3 %).

On FEMTO itself, `concat6` also gives the quietest healthy phase:

| Mode | Median robust CV of h_rms, rows 0-19 | Median minute-to-minute log-ratio noise |
|---|---|---|
| concat6 | 0.041 | 0.0196 |
| median6 | 0.048 | 0.0200 |
| last1 | 0.052 | 0.0307 |
| XJTU reference | 0.033 | 0.0187 |

`median6` and `last1` tables are kept as sensitivity variants: `cache/femto_features_{median6,last1}.csv`.
A further sanity check: the maximum `|peak|` per bearing in the `concat6` table matches the raw
maxima in `DATA_PROVENANCE.md` exactly (48.1, 14.1, 20.2, 8.8 g, ...). `median6` hides them.

Features that stay unreliable however they are aggregated: `*_dominant_freq` (rho 0.49-0.69 even on XJTU)
and, to a lesser degree, the `*_crest/impulse/clearance_factor` family and the envelope-band ratios. None of
these is used by the frozen v2 indicators, which only use `rms` and `peak`. `envelope_kurtosis`,
`spectral_entropy` and `envelope_energy_100_200` are in `ROLLING_SOURCE_COLUMNS`, so a pooled model will
see noisier FEMTO versions of them.

## 3. Cadence (10 s vs 60 s): one row = one minute, causal, RUL in whole minutes

* Snapshots are grouped into **end-aligned** blocks of 6. The last row always contains the final snapshot,
  so `rul_minutes = (n_rows - 1 - cycle)` is exact and has the same meaning as in XJTU. Its value 0 is the
  last recorded minute.
* A leading incomplete block (`n_snap mod 6` snapshots, 0-5, at most 50 s of the healthy start) is dropped.
* Row `cycle` uses only snapshots recorded at or before its own last snapshot, so it is causal. Its
  timestamp is the end of that minute.
* `elapsed_minutes = cycle` and `cycle` run 0..n-1, as in XJTU. Time comes from the **file index**
  (index x 10 s), never from the in-file clock. This also handles the corrupted Bearing1_1 timestamps.
* `source_file` records the 6 files, for example `Learning_set/Bearing1_1/acc_00002.csv..acc_00007.csv`.
* Under this cadence the v2 constants keep their wall-clock meaning: COMMISSION = 20 rows = 20 min,
  k/m persistence in minutes, the 120 min horizon, and the 5/20/60-row context windows.
* Six 0.1 s snapshots per minute give 0.6 s of signal per row, vs 1.28 s for XJTU. That is about
  2x less data, hence the slightly higher healthy noise above.

## 4. Speed / load / condition columns

| Dataset | condition id | speed_rpm | load_kn | Bearing dynamic rating C | P/C |
|---|---|---|---|---|---|
| XJTU | 1 / 2 / 3 | 2100 / 2250 / 2400 | 12 / 11 / 10 | LDK UER204: 12.82 kN | 0.94 / 0.86 / 0.78 |
| FEMTO | **4 / 5 / 6** | 1800 / 1650 / 1500 | 4.0 / 4.2 / 5.0 | 4.0 kN (challenge PDF A.1) | 1.00 / 1.05 / 1.25 |

The condition ids are distinct, so the per-condition fleet comparison in the onset GT never mixes the
datasets. **Risk:** `classifier_feature_columns` keeps `speed_rpm` and `load_kn` as features. Their ranges
do not overlap between datasets (FEMTO load 4-5 kN vs XJTU 10-12 kN), so in a pooled model they act as a
perfect dataset indicator. For pooled training, drop them or replace them with a dimensionless load P/C
(an experiment-side change; the src code is untouched).

## 5. Bearing geometry and characteristic frequencies

| | Z | ball d (mm) | pitch Dm (mm) | fr (Hz) | FTF | BSF | **BPFO** | **BPFI** |
|---|---|---|---|---|---|---|---|---|
| XJTU LDK UER204 @2100/2250/2400 rpm | 8 | 7.92 | 34.55 | 35/37.5/40 | 13.5-15.4 | 72-83 | **108/116/123** | **172/184/197** |
| FEMTO (PDF A.1) @1800/1650/1500 rpm | 13 | 3.5 | 25.6 | 30/27.5/25 | 10.8-12.9 | 90-108 | **168/154/140** | **222/203/185** |

(Contact angle assumed 0.)

Features that depend on characteristic frequencies are only the **envelope-spectrum band ratios**:
`*_envelope_energy_{0_50,50_100,100_200,200_500,500_1000}_hz_ratio`.
* XJTU BPFO (108-123 Hz) **and** BPFI (172-197 Hz) both fall in the 100-200 Hz band.
* FEMTO BPFO (140-168 Hz) falls in 100-200 Hz, but FEMTO BPFI at condition 1/2 (222/203 Hz) falls in 200-500 Hz.
* BSF lands in 50-100 Hz for XJTU but in 50-100 / 100-200 Hz for FEMTO.
* Shaft frequency and FTF are in 0-50 Hz for both.

So `envelope_energy_100_200_hz_ratio`, the defect-band feature in `ROLLING_SOURCE_COLUMNS`, captures
inner-race faults for XJTU but only outer-race faults for FEMTO at conditions 4 and 5. A pooled model must
not assume this band means the same defect type in both datasets. A geometry-normalised alternative would
be envelope energy at orders of BPFO/BPFI, but that is a new feature and is not added here, to preserve
schema identity. The other features (time-domain, fixed-Hz spectral bands, envelope RMS/kurtosis) do not
depend on geometry, although resonance bands naturally differ between rigs.

## 6. Other FEMTO specifics carried into the table

* **Clipping.** Bearings 1_1, 1_3, 1_4 and 2_3 saturate at about 48.1 g near the end. `peak` and
  `peak_to_peak` hit a ceiling there; kurtosis and crest factor are distorted for the last few minutes.
* **End-of-life is the last snapshot**, not a 20 g crossing. Bearings 1_5, 2_4, 2_6 and 3_3 never reach
  20 g, and 1_6 only just does. `rul_minutes = 0` is "test stopped". This is the same convention as XJTU,
  where runs end at roughly 10x the healthy amplitude.
* **Temperature is not used.** XJTU has none, and 4 FEMTO bearings have none.
* Bearing1_4: the label uses the Full_Test_Set end (1428 snapshots), not the PDF's 339 s.

## 7. Validation results (`results/pooled_validation.json`)

| Check | Result |
|---|---|
| Schema | 127 columns, identical names, order and dtypes; 13,356 rows = 9,216 XJTU + 4,140 FEMTO; 32 bearings; conditions 1-6 |
| XJTU rows in `combined_features.csv` | Exactly equal to `outputs/xjtu_features.csv`. The XJTU rows are re-written with round-trip float parsing |
| `add_past_context(pooled)` | Runs; 347 columns, all finite; XJTU rows bit-identical to `add_past_context(XJTU only)`, so there is no cross-bearing leakage; 203 classifier columns |
| Onset GT on 32 bearings | Uses a v3 copy of the frozen `onset_gt.main` logic; the copy differs only in input frame and None-safety, and FEMTO has no two-phase candidate. The 15 XJTU rows equal the frozen `onset_gt.csv` exactly (values; dtype differs only because FEMTO introduces NaN) |
| Frozen-file hashes | `causal.py`, `run_dummies.py`, `onset_gt.csv` unchanged |
| v2 harness | `causal.dummy_states`, `harness2.evaluate` (all metrics) and `harness2.p1_baselines` run on 32 bearings. The XJTU-subset cost of every dummy equals the frozen-loader cost exactly (e.g. never 11.598, onset_eq_critical 5.371). **Not usable at n = 32:** `causal.signflip_p` enumerates 2^n sign patterns (fine for n <= 20), so `harness2.paired` needs a Monte-Carlo sign-flip for pooled comparisons |

### Per-bearing lifetimes and baseline quality (FEMTO, primary `concat6`)

life = n_snap x 10 s. rows = whole minutes. comm/life and comm/fleet are the frozen GT baseline-quality
statistics. clean = not contaminated (comm/life <= 1.15 and comm/fleet <= 1.5) and not short_healthy.
r5 = trailing-5 median of max(h,v) `rms_baseline_ratio`.

| Bearing | split | snaps | life (min) | rows | comm/life | comm/fleet | clean | GT early/late RUL | abrupt | r5 @ 50 % life | r5 @ RUL 60 | r5 @ RUL 30 | r5 max | max abs g |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_1 | Learning | 2803 | 467.2 | 467 | 1.33 | 1.08 | no | 248/64 | no | 1.02 | 2.72 | 3.24 | 10.3 | 48.1 |
| 1_2 | Learning | 871 | 145.2 | 145 | 1.28 | 1.11 | no | 69/7 | yes | 1.00 | 1.06 | 1.17 | 3.95 | 36.7 |
| 1_3 | Full_Test | 2375 | 395.8 | 395 | 1.19 | 0.88 | no | 172/108 | no | 1.06 | 6.64 | 4.95 | 17.2 | 48.1 |
| 1_4 | Full_Test | 1428 | 238.0 | 238 | 1.27 | 1.29 | no | 57/56 | no | 0.89 | 0.84 | 13.4 | 23.0 | 48.1 |
| 1_5 | Full_Test | 2463 | 410.5 | 410 | 1.38 | 0.98 | no | 9/8 | yes | 0.87 | 0.83 | 0.82 | 2.76 | 14.1 |
| 1_6 | Full_Test | 2448 | 408.0 | 408 | 1.42 | 1.11 | no | 6/6 | yes | 0.79 | 0.86 | 0.80 | 2.86 | 20.2 |
| 1_7 | Full_Test | 2259 | 376.5 | 376 | 1.37 | 1.10 | no | 212/22 | yes | 0.94 | 1.08 | 1.03 | 3.99 | 28.4 |
| 2_1 | Learning | 911 | 151.8 | 151 | 0.77 | 1.11 | no* | 126/13 | no | 2.32 | 2.90 | 2.67 | 5.30 | 25.2 |
| 2_2 | Learning | 797 | 132.8 | 132 | 1.01 | 1.09 | **yes** | 101/8 | no | 3.67 | 4.02 | 2.62 | 4.49 | 38.1 |
| 2_3 | Full_Test | 1955 | 325.8 | 325 | 1.98 | 1.44 | no | 1/1 | yes | 1.83 | 0.95 | 0.96 | 4.82 | 48.1 |
| 2_4 | Full_Test | 751 | 125.2 | 125 | 1.22 | 0.99 | no | 1/1 | yes | 1.04 | 1.09 | 0.94 | 1.14 | 8.8 |
| 2_5 | Full_Test | 2311 | 385.2 | 385 | 2.58 | 2.00 | no | 8/1 | yes | 1.20 | 1.19 | 1.18 | 1.93 | 26.9 |
| 2_6 | Full_Test | 701 | 116.8 | 116 | 1.43 | 1.18 | no | 2/2 | yes | 0.88 | 0.89 | 0.87 | 2.05 | 11.5 |
| 2_7 | Full_Test | 230 | 38.3 | 38 | 1.06 | 1.24 | **yes** | 0/0 | yes | 1.00 | n/a | 1.00 | 1.21 | 38.9 |
| 3_1 | Learning | 515 | 85.8 | 85 | 1.15 | 0.95 | no** | 4/3 | yes | 1.06 | 1.02 | 1.03 | 4.77 | 41.3 |
| 3_2 | Learning | 1637 | 272.8 | 272 | 1.47 | 1.39 | no | 34/8 | yes | 0.89 | 0.87 | 1.65 | 3.66 | 23.7 |
| 3_3 | Full_Test | 434 | 72.3 | 72 | 1.22 | 1.08 | no | 21/18 | yes | 1.08 | 1.05 | 1.05 | 3.57 | 16.0 |

\* 2_1 has a clean commissioning level (0.77) but its GT early edge is RUL 126 at row 25, so it is flagged
short_healthy. \*\* 3_1 has comm/life 1.153 (contaminated in `concat6`; clean under `median6`) and a 1-row
GT, so it is a borderline case.

**Summary FEMTO vs XJTU** (primary mode):

| | FEMTO (17) | XJTU (15) |
|---|---|---|
| Lifetime, minutes (median, range) | 238 (38-467) | 161 (42-2538) |
| Lifetimes > 120 min | 13 | 12 |
| **Clean healthy baseline** | **2** (2_2, 2_7) | 7 |
| Baseline contaminated (comm/life > 1.15 or comm/fleet > 1.5) | 14 | 7 |
| short_healthy | 1 | 2 |
| Abrupt (GT onset RUL <= 30) | 12 | 4 |
| GT late edge >= 60 min before end | **2** (1_1, 1_3) | 9 |
| Median r5 at 50 % of life / RUL 60 / RUL 30 | 1.02 / 1.05 / 1.05 | 1.20 / 2.73 / 3.30 |
| Healthy robust CV of h_rms | 0.041 | 0.033 |

### What the trajectories show (`results/rms_ratio_trajectories.png`)

1. **Run-in drift contaminates the commissioning baseline, not early damage.** In 14 of 17 FEMTO bearings
   the first 20 minutes are 1.2-2.6x louder than the bearing's own healthy level (the lowest 30 % of life).
   RMS then falls slowly over the first 1-2 h (e.g. 1_1: 0.43, then 0.38, then 0.32 g over 0-20, 20-60 and
   60-120 min). With the frozen self-reference (median of rows 0-19), r5 therefore sits at about 0.8-0.95
   for most of life. That delays any ratio-1.3 trigger. The frozen `fallback` reference
   (min(med20, m60) when m60 < 0.9 med20) activates on 10 of 17 FEMTO bearings, vs 3 of 15 XJTU bearings.
   This is a protocol-level domain gap, deliberately **not** "fixed" in the features.
2. **FEMTO degradation is mostly abrupt on a minute scale.** Under the frozen GT definition, 12 of 17 bearings
   have their onset 30 minutes or less before the end, and only 2 have the late GT edge 60 minutes or more
   before the end. At RUL 60 and RUL 30 the median r5 is still about 1.05, whereas XJTU is already at 2.7-3.3.
   In the plot, most red curves stay flat until the last 10-20 minutes. The exceptions are 1_1, 1_3, 2_1
   and 2_2, which degrade gradually over 1-2 h.
3. Implication for the v2 conclusion: post-onset 60-120 min vs < 30 min discrimination was data-limited.
   **FEMTO adds very little to that regime**: about 2-4 bearings have a usable 60-120 min post-onset window.
   It adds mainly to the abrupt / short-warning and false-alarm (long healthy phase) regimes. That is useful for
   onset false-alarm calibration and for LC/MC (late or missed critical) costs, but the expected gain for
   graded RUL is small.

## 8. Known limitations / open items

* The concat6 joins are non-contiguous signal segments. Their effect was measured on XJTU (small) but
  cannot be removed without changing the extractor.
* `*_dominant_freq` and the envelope-band ratios are low-fidelity at 2560 samples. Prefer excluding them, or
  checking their importance, in pooled models.
* `speed_rpm` / `load_kn` identify the dataset perfectly (see section 4).
* `harness2.paired` / `causal.signflip_p` do not scale to 32 bearings. A Monte-Carlo sign-flip is needed;
  changing the frozen protocol would require a new pre-registration.
* The pooled onset GT uses a v3 copy of the frozen rules. FEMTO has no two-phase GT candidate, so its GT
  interval comes from three constructions instead of four.
