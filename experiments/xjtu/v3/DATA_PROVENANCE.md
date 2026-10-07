# FEMTO / PRONOSTIA (IEEE PHM 2012) — data provenance

Status as of 2026-10-07: **all 17 bearings were obtained with complete run-to-failure records.**
That is 6 in Learning_set and 11 in Full_Test_Set. Every per-bearing acc-file count matches the
published counts. Nothing is missing.

## Sources

| Role | URL | Notes |
|---|---|---|
| Primary download (used) | https://phm-datasets.s3.amazonaws.com/NASA/10.+FEMTO+Bearing.zip | NASA PCoE Prognostics Data Repository mirror ("10. FEMTO Bearing"). S3 Last-Modified 2022-09-18, ETag `66bd15a6704e6b6317e04a7bdeb28a6e-68`, 1,157,035,288 bytes |
| Independent cross-check | https://github.com/wkzs111/phm-ieee-2012-data-challenge-dataset (branch `master`) | GitHub fork of the original FEMTO mirror. Its README says the original FEMTO-ST page (femto-st.fr/.../IEEE-PHM-2012-Data-challenge.php) is offline. I checked the last acc file of 5 bearings byte-for-byte (see below) |
| Challenge description PDF | https://raw.githubusercontent.com/wkzs111/phm-ieee-2012-data-challenge-dataset/master/IEEEPHM2012-Challenge-Details.pdf | Saved to `local_data/femto/docs/` |

Citation: Nectoux P., Gouriveau R., Medjaher K., Ramasso E., Chebel-Morello B., Zerhouni N.,
Varnier C. (2012). *PRONOSTIA: An experimental platform for bearings accelerated degradation
tests.* IEEE Int. Conf. on PHM, Denver. No explicit license is stated. It is redistributed
publicly by NASA PCoE.

## Archive layout and checksums (SHA-256)

All files are under `local_data/femto/` (gitignored via `/local_data/`).

```
raw/FEMTO_Bearing.zip          e21bb22bd8d54fd18ebe98b4b4e094c0c40469bda19811a2a642d5cc84ebd81f  (MD5 0feedeab9584a68199f1f72fe45e3d19)
 └ 10. FEMTO Bearing/FEMTOBearingDataSet.zip
raw/FEMTOBearingDataSet.zip    49a289fe9426fe8280a391b964f556221051d9fa8b294e6518860095ba721a48
 ├ raw/Training_set.zip        c210eac98b7849484d135709d4464217685ef39f5a68af2c3e35452fb25e7b88  -> Learning_set/      (8,391 entries)
 ├ raw/Validation_Set.zip      4eafa12a2addab742502bf36bda2a50cc9d4d51bba5d4151410f3ae091863552  -> Full_Test_Set/     (19,535 entries)
 └ raw/Test_set.zip            5cddf80adab1c25e2c83bd1c5a08dbfab6ae68b19b5b07c7db88dc9373de5258  -> Test_set/ (truncated, 15,699 entries)
docs/IEEEPHM2012-Challenge-Details.pdf  13bd43eb2dc4c8455e36584094552ccbd8101b9a0b99bd10ca82103453585ba5
```

- **The NASA name "Validation_Set" is misleading.** It contains `Full_Test_Set/`, which holds the
  complete trajectories of the 11 test bearings.
- `Test_set.zip` contains another copy of `Training_set.zip` and `Validation_Set.zip`. Both
  copies are byte-identical to the top-level ones (same SHA-256). Extracting it left these two
  zips in `extracted/`. They are redundant and can be deleted.
- Extracted tree: `local_data/femto/extracted/{Learning_set,Full_Test_Set,Test_set}/BearingX_Y/{acc,temp}_NNNNN.csv`.
- The per-bearing SHA-256 values (all acc files, name+bytes concatenated in order) are in
  `results/femto_inventory.json`.

## Verification

Command: `python experiments/xjtu/v3/verify_femto.py`. It writes `results/femto_inventory.json`
and takes about 2 minutes with 3 workers. It parses every file.

| Split | Bearing | Condition | acc files (published) | temp files | Duration (acc*10 s) | max abs accel (g) |
|---|---|---|---|---|---|---|
| Learning | 1_1 | 1800rpm/4000N | 2803 (2803) | 466 | 7h47m | 48.1 |
| Learning | 1_2 | 1800rpm/4000N | 871 (871) | 144 | 2h25m | 36.7 |
| Learning | 2_1 | 1650rpm/4200N | 911 (911) | 151 | 2h32m | 25.2 |
| Learning | 2_2 | 1650rpm/4200N | 797 (797) | 0 | 2h13m | 38.1 |
| Learning | 3_1 | 1500rpm/5000N | 515 (515) | 89 | 1h26m | 41.3 |
| Learning | 3_2 | 1500rpm/5000N | 1637 (1637) | 0 | 4h33m | 23.7 |
| Full_Test | 1_3 | 1800rpm/4000N | 2375 (2375) | 0 | 6h36m | 48.1 |
| Full_Test | 1_4 | 1800rpm/4000N | 1428 (1428) | 237 | 3h58m | 48.1 |
| Full_Test | 1_5 | 1800rpm/4000N | 2463 (2463) | 410 | 6h51m | 14.1 |
| Full_Test | 1_6 | 1800rpm/4000N | 2448 (2448) | 408 | 6h48m | 20.2 |
| Full_Test | 1_7 | 1800rpm/4000N | 2259 (2259) | 376 | 6h16m | 28.4 |
| Full_Test | 2_3 | 1650rpm/4200N | 1955 (1955) | 0 | 5h26m | 48.1 |
| Full_Test | 2_4 | 1650rpm/4200N | 751 (751) | 125 | 2h05m | 8.8 |
| Full_Test | 2_5 | 1650rpm/4200N | 2311 (2311) | 386 | 6h25m | 26.9 |
| Full_Test | 2_6 | 1650rpm/4200N | 701 (701) | 116 | 1h57m | 11.5 |
| Full_Test | 2_7 | 1650rpm/4200N | 230 (230) | 38 | 0h38m | 38.9 |
| Full_Test | 3_3 | 1500rpm/5000N | 434 (434) | 72 | 1h12m | 16.0 |

Checks that passed for all 17 bearings (and also for the 11 truncated Test_set bearings):
- File indices are contiguous from 00001 to N.
- Every acc file is 2560 rows x 6 columns (h, m, s, us, horiz g, vert g).
- No NaN or parse failures.
- Snapshot spacing is exactly 10 s everywhere except the Bearing1_1 timestamp defect below.

Where the published counts come from:
- Learning_set and the truncated Test_set: Appendix A.4/A.5 of the challenge PDF. Those counts
  include temp files: acc+temp equals the PDF number for every bearing. Examples: 1_1 2803+466=3269,
  1_5 2302+383=2685, 2_7 172+28=200.
- Full_Test_Set: the PDF does not list counts. I checked them two ways:
  - The widely cited literature counts (1_3 2375, 1_4 1428, 1_5 2463, 1_6 2448, 1_7 2259,
    2_3 1955, 2_4 751, 2_5 2311, 2_6 701, 2_7 230, 3_3 434) all match.
  - (Full - Test) x 10 s equals the PDF Table 3 "Actual RUL" for 10 of 11 bearings
    (5730, 1610, 1460, 7570, 7530, 1390, 3090, 1290, 580, 820 s). Bearing1_4 is the exception;
    see below.

Cross-mirror check: these files are byte-identical (SHA-256) between the NASA copy and the
wkzs111 GitHub mirror:
- Learning_set/Bearing1_1/acc_02803
- Full_Test_Set/Bearing1_3/acc_02375
- Full_Test_Set/Bearing1_4/acc_01428
- Full_Test_Set/Bearing2_7/acc_00230
- Full_Test_Set/Bearing3_3/acc_00434

`Bearing3_3/acc_00435` returns 404 on GitHub, which confirms 434 is the end.

Test_set vs Full_Test_Set: for every bearing, every Test_set acc file is byte-identical to the
Full_Test_Set file with the same name, so the truncated set is an exact prefix. Bearing1_4 is the
exception: its files differ in bytes only, not in values (see below). For temp files, only the
last Test_set temp file differs. It is a truncated partial copy (for example 417 rows vs 600),
and the overlapping rows are numerically equal.

## Anomalies and caveats for loaders

1. **Delimiter is not uniform.**
   - Full_Test_Set/Bearing1_4 acc *and* temp use `;`.
   - Test_set/Bearing1_4 acc and temp use `,`. Their values are numerically identical to the
     Full_Test_Set files (checked on files 1, 500 and 1139). The format differs: `4.2504e+005`
     vs `4.2504e+05`.
   - Temperature files use `;` in every bearing except Learning_set/Bearing1_1 and
     Test_set/Bearing1_4, which use `,`.
   - All other acc files use `,`.
   - The loader must sniff the delimiter per file.
2. **Temperature is missing for 4 bearings** in the complete data: Bearing2_2, Bearing3_2,
   Bearing1_3 and Bearing2_3. Bearing2_6 has 116 temp files in Full_Test_Set but **0 in
   Test_set**.
   - The last temp file of most bearings is short (for example 517, 37, 27 or 20 rows).
   - Bearing2_5 has one 20-row file in the middle of the series.
   - Temperature is sampled at 10 Hz, 600 rows per minute, so there is about one temp file per
     6 acc files.
3. **Bearing1_1 acc_02121 and acc_02122 have corrupted timestamps.**
   - Both start at 9:38:46 with microsecond field `8.6566e+005`, while their neighbours are at
     15:32:49 and 15:33:19.
   - The vibration content looks plausible (std similar to neighbours) and the two files are not
     duplicates.
   - Use the file index x 10 s as the time axis, not the in-file clock.
4. **Bearing1_4 ground-truth inconsistency.** The challenge PDF lists Actual RUL = 339 s.
   However, Full_Test_Set extends 1428 - 1139 = 289 snapshots, which is 2890 s, past the
   truncation point. This is a known discrepancy in the literature. Treat the Full_Test_Set end
   as end-of-life and do not use the 339 s figure.
5. **The end-of-life criterion is not consistent.**
   - The challenge says failure means vibration amplitude exceeds 20 g.
   - Five Full_Test_Set bearings never reach 20 g: 1_5 (14.1 g), 2_4 (8.8 g), 2_6 (11.5 g) and
     3_3 (16.0 g) stay below it, and 1_6 only reaches 20.2 g.
   - For these bearings the last file is when the experiment stopped, not a 20 g crossing.
   - Several bearings saturate at about 48.1 g near the end (the sensor clip level): 1_1, 1_3,
     1_4 and 2_3.
   - For RUL labels, define end-of-life as the last snapshot. Do not use the 20 g rule.
6. **Sampling differs from XJTU-SY.**
   - FEMTO: 2560 samples (0.1 s at 25.6 kHz) every 10 s.
   - XJTU-SY: 32768 samples (1.28 s at 25.6 kHz) every 60 s.
   - Lifetimes are much shorter here: 0.6 to 7.8 h, vs XJTU's 42 min to about 41 h.
   - Window-length-dependent features (kurtosis, spectral bins) will have different variance.
   - Any shared RUL horizon (for example "60-120 min") covers a very different fraction of life.
7. The column-4 sub-second field is the microsecond count. In some files it is written in
   scientific notation (`4.2504e+05`). Parse it as float.

## Files

- `experiments/xjtu/v3/verify_femto.py`: verification script (read-only on the data).
- `experiments/xjtu/v3/results/femto_inventory.json`: per-bearing inventory, hashes and the
  prefix-check results.
- `local_data/femto/raw/`: original archives.
- `local_data/femto/extracted/`: unpacked CSVs.
- `local_data/femto/docs/`: challenge PDF.
