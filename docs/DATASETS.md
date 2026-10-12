# Datasets — ML Risk Model

This is the source of truth for what real data trains the Risk Assessment Agent's model,
and how the simulated live telemetry is calibrated. Referenced from `PHASE_PLAN.md`
Phase 3. Update the "Data profile" section once task 1 (dataset acquisition) is done.

## Policy

The model behind the Risk Assessment Agent is trained on a **real, published, cited
dataset** — never on numbers invented for this project. The live telemetry ingest
pipeline (simulated IoT generator, `PROJECT_PLAN.md` §7) stays simulated, since no real
facility feed is available to a student project, but its statistical parameters are
calibrated from the same real dataset rather than picked arbitrarily. State this
distinction explicitly in the final documentation — it's a defensible, honest design
choice, not a shortcut, and should be presented as such.

## Primary dataset — pick one as the default

| Dataset | Source | License / access | Why it fits |
|---|---|---|---|
| **AI4I 2020 Predictive Maintenance** *(default recommendation)* | [UCI ML Repository](https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset) | Free, public, standard academic use | 10,000 rows, tabular, 5 labeled failure modes (tool wear, heat dissipation, power, overload, random) plus a binary failure label. Closest structural match to this project's `risk_scores` / `sensor_readings` schema — minimal mapping work in task 2 |
| **Microsoft Azure Predictive Maintenance** | Kaggle / Microsoft sample data | Free, public | Multi-table: hourly telemetry (voltage, rotation, pressure, vibration) for 100 machines, failure log, non-fatal error events, maintenance records. Best match if you want to also exercise the `maintenance_records` / `alerts` tables against real-shaped data, not just `risk_scores` |

## Secondary / optional datasets — use if extending beyond tabular risk scoring

| Dataset | Source | License / access | Use case |
|---|---|---|---|
| **NASA C-MAPSS** (Turbofan Degradation) | NASA Prognostics Data Repository | Public domain | Run-to-failure time series with realistic sensor noise — use if you want the risk score to become a remaining-useful-life (RUL) regression instead of a severity classification |
| **NASA IMS Bearing Dataset** | NASA / Univ. of Cincinnati IMS Center (mirrored on Kaggle) | Public domain | Real long-term vibration data showing a bearing's full degradation from normal to failed state — use for a vibration-specific anomaly detection sub-model |
| **CWRU Bearing Dataset** | [Case Western Reserve Bearing Data Center](https://engineering.case.edu/bearingdatacenter/download-data-file) | Free for research/academic use | The most-cited fault-diagnosis benchmark — vibration signals for normal and induced-fault bearings. Good if you want a second, independent validation set for a vibration model |
| **MetroPT** | Published dataset (Veloso et al., 2022, Scientific Data) | Open access | Real sensor data (pressure, temperature, current, GPS) from an actual Porto metro transit system — the most "real facility" feeling dataset on this list, useful as a sanity-check dataset or a second domain to demonstrate generalization |
| **SCANIA APS Failure Prediction** | [UCI ML Repository](https://archive.ics.uci.edu/dataset/421/scania+aps+failure+prediction) | Free, public | Real truck component failure data — good secondary validation set for a different asset type |

## Mapping guidance (feeds Phase 3, task 2)

When mapping an external dataset's columns onto this project's schema (`PROJECT_PLAN.md`
§5), be explicit and keep the mapping in one reviewable file
(`backend/app/services/ml/dataset_mapping.py`):

- External sensor/telemetry columns → `sensor_readings` (`asset_id`, `sensor_type`,
  `value`, `unit`, `timestamp`)
- External failure/label columns → `risk_scores` (`factors_json` should record which
  original dataset columns contributed to the label) and, where applicable,
  `maintenance_records`
- Record the **dataset name and version** used for training directly on the stored model
  artifact in MinIO and in the `risk_scores.model_version` field, so every risk score is
  traceable back to what it was trained on.

## Data profile (populated from Phase 3, task 1)

> Empirical profile computed from 10,000 rows of the official AI4I 2020 dataset.
> Task 4 (simulator calibration) and Task 5 (model retraining) derive their parameters from this table.

- **Dataset used:** AI4I 2020 Predictive Maintenance Dataset (UCI Machine Learning Repository (ID: 601))
- **Citation:** Matzka, S. (2020). Explainable Artificial Intelligence for Predictive Maintenance Applications. Third International Conference on Industrial Cyber-Physical Systems (ICPS).
- **License / access:** Creative Commons Attribution 4.0 International (CC BY 4.0)
- **Version / access date:** September 2026
- **Row count:** 10,000 rows across 14 columns (0 missing values across all columns)
- **Failure class balance:**
  - **Total Failures (`Machine failure == 1`):** 339 (3.39%)
  - **Normal Operation (`Machine failure == 0`):** 9,661 (96.61%)
  - **Individual Failure Modes:**
    - `TWF` (Tool Wear Failure): 46 incidents (0.46%)
    - `HDF` (Heat Dissipation Failure): 115 incidents (1.15%)
    - `PWF` (Power Failure): 95 incidents (0.95%)
    - `OSF` (Overstrain Failure): 98 incidents (0.98%)
    - `RNF` (Random Failure): 19 incidents (0.19%)
  - **Overlapping / Compound Failures:** 24 records exhibit >1 concurrent failure mode
  - **Unspecified Failures:** 9 records failed without meeting the 5 specific sub-mode thresholds
- **Product Variant Distribution:**
  - `L` (Low quality variant (60% target allocation)): 6,000 units (60.00%)
  - `M` (Medium quality variant (30% target allocation)): 2,997 units (29.97%)
  - `H` (High quality variant (10% target allocation)): 1,003 units (10.03%)
- **Feature ranges & operational statistics (for simulator calibration in task 3.4):**
  | Feature | Unit | Min | 25% | Median | 75% | Max | Mean | Std Dev |
  |---|---|---|---|---|---|---|---|---|
  | Air temperature | K | 295.30 | 298.30 | 300.10 | 301.50 | 304.50 | 300.00 | 2.00 |
  | Process temperature | K | 305.70 | 308.80 | 310.10 | 311.10 | 313.80 | 310.01 | 1.48 |
  | Rotational speed | rpm | 1168.00 | 1423.00 | 1503.00 | 1612.00 | 2886.00 | 1538.78 | 179.28 |
  | Torque | Nm | 3.80 | 33.20 | 40.10 | 46.80 | 76.60 | 39.99 | 9.97 |
  | Tool wear | min | 0.00 | 53.00 | 108.00 | 162.00 | 253.00 | 107.95 | 63.65 |
  | Temperature difference (Process - Air) | K | 7.60 | 9.30 | 9.80 | 11.00 | 12.10 | 10.00 | 1.00 |
  | Mechanical power (Torque x Speed) | W | 1148.44 | 5561.18 | 6271.03 | 7003.00 | 10469.92 | 6279.74 | 1067.36 |

## Telemetry Generator Calibration (Phase 3, Task 4)

In accordance with Phase 3 Task 4 and project engineering rules, the simulated IoT telemetry
generator (`backend/scripts/iot_data_generator.py`) is calibrated directly against the empirical
statistics above rather than using hand-invented numbers:

1. **Failure / Anomaly Injection Rate:**
   - **Calibrated Default:** `--anomaly-rate 0.0339` ($3.39\%$).
   - **Derivation:** Exactly reflects the real ground-truth failure prevalence observed in the
     AI4I 2020 dataset ($339$ machine failures / $10,000$ operational samples).

2. **Sensor Operating Baselines and Noise Calibration:**
   - **`air_temperature` (K):** Baseline set to $300.00\text{ K}$, thermal variation amplitude
     $2.00\text{ K}$, Gaussian noise $\sigma = 0.50\text{ K}$, diurnal cycle period $3600\text{ s}$,
     anomaly multiplier $4.0$.
   - **`process_temperature` (K):** Baseline set to $310.01\text{ K}$, operational amplitude
     $1.50\text{ K}$, Gaussian noise $\sigma = 0.40\text{ K}$, cycle period $1800\text{ s}$,
     anomaly multiplier $4.0$ (simulating heat dissipation failures).
   - **`rotational_speed` (rpm):** Baseline set to $1538.78\text{ rpm}$, operational swing
     $100.00\text{ rpm}$, noise $\sigma = 25.00\text{ rpm}$, cycle period $300\text{ s}$,
     anomaly multiplier $4.0$ (simulating motor stall/overspeed).
   - **`torque` (Nm):** Baseline set to $39.99\text{ Nm}$, cyclic load amplitude $6.00\text{ Nm}$,
     noise $\sigma = 1.50\text{ Nm}$, cycle period $300\text{ s}$, anomaly multiplier $4.0$
     (simulating tool overstrain).
   - **`tool_wear` (min):** Baseline set to $107.95\text{ min}$, wear progression amplitude
     $30.00\text{ min}$, noise $\sigma = 5.00\text{ min}$, cycle period $7200\text{ s}$,
     anomaly multiplier $3.5$ (simulating rapid tool wear failures).
   - **`temperature_difference` (K):** Baseline set to $10.00\text{ K}$, amplitude $1.00\text{ K}$,
     noise $\sigma = 0.25\text{ K}$, anomaly multiplier $4.0$.
   - **`mechanical_power` (W):** Baseline set to $6279.74\text{ W}$, amplitude $600.00\text{ W}$,
     noise $\sigma = 150.00\text{ W}$, anomaly multiplier $4.0$ (simulating electrical power failures).

3. **Separation of Training and Simulation (Non-Circularity):**
   - The XGBoost risk model is trained **only** on the mapped real dataset (`backend/data/raw/ai4i2020.csv`),
     never on the generator's synthetic readings.
   - The generator is used strictly for runtime streaming into the TimescaleDB hypertable
     during local dev and live demonstration, ensuring the live demo accurately mirrors
     real factory conditions without training-serving circularity.

## C-MAPSS data profile (Phase 3b, task 1)

> Empirical profile of the NASA C-MAPSS turbofan run-to-failure dataset, used by Phase 3b
> to train the Remaining Useful Life (RUL) model (tasks 3b.2-3b.3). Regenerate with
> `python -m scripts.acquire_cmapss --profile`.

- **Dataset used:** NASA C-MAPSS Turbofan Engine Degradation Simulation (FD001-FD004)
- **Source:** NASA Ames Prognostics Data Repository; downloaded from the PHM dataset mirror
  `phm-datasets.s3.amazonaws.com/NASA/` (original NASA portal links redirect and were not
  reachable during acquisition).
- **Citation:** Saxena, A., Goebel, K., Simon, D., & Eklund, N. (2008). Damage Propagation
  Modeling for Aircraft Engine Run-to-Failure Simulation. PHM08.
- **License / access:** NASA open data (US Government work, public domain per the
  repository). *Exact license wording was not re-verified against the NASA portal during
  acquisition; confirm before external publication.*
- **Version / access date:** October 2026 (archive dated 2022-09-17 on the mirror). Per-file
  SHA-256 checksums are stored in the profile JSON for change detection.
- **Integrity check:** the 12 downloaded files were compared byte-for-byte against an
  official NASA archive copy supplied by the project owner (includes NASA's `readme.txt` and
  the PHM08 paper PDF); all 12 are identical.
- **Known readme typo:** NASA's `readme.txt` lists FD004 as 248 train / 249 test trajectories.
  The data files contain 249 train / 248 test engines (the table below reports counts computed
  from the files, not the readme; FD002's 260/259 is also train > test).
- **Files:** 12 whitespace-delimited text files, 26 columns each: `unit`, `cycle`,
  3 operating settings, 21 sensors. Not committed (43 MB, gitignored); re-created by the CLI.

| Subset | Train engines | Train rows | Test engines | Op. conditions | Fault modes | Train life min/median/max (cycles) | Test RUL min/median/max |
|---|---|---|---|---|---|---|---|
| FD001 | 100 | 20,631 | 100 | 1 | 1 (HPC) | 128 / 199 / 362 | 7 / 86 / 145 |
| FD002 | 260 | 53,759 | 259 | 6 | 1 (HPC) | 128 / 199 / 378 | 6 / 80 / 194 |
| FD003 | 100 | 24,720 | 100 | 1 | 2 (HPC, fan) | 145 / 220.5 / 525 | 6 / 77.5 / 145 |
| FD004 | 249 | 61,249 | 248 | 6 | 2 (HPC, fan) | 128 / 234 / 543 | 6 / 88 / 195 |

(Fault-mode counts are from the dataset documentation, not computed from the files.)

- **Constant (zero-variance) sensors in training data:** FD001: sensor_1, 5, 10, 16, 18, 19;
  FD003: sensor_1, 5, 16, 18, 19; FD002/FD004: none (operating-condition variation makes
  every sensor vary). Constant sensors carry no signal and should be dropped in 3b.2.
- **Data quality:** zero NaNs and zero cycle-numbering gaps in train and test files.
- **Operating-condition counting:** operating setting 2 (Mach) is excluded when clustering
  conditions, since it is determined by setting 1 and its noise straddles rounding boundaries.
- **Limitations:** these are simulated turbofan engines, not factory equipment. The RUL
  model is a demonstration of run-to-failure learning on a real, cited benchmark; mapping
  to facility asset types is handled in task 3b.2 and should be presented as such.

### C-MAPSS to project schema mapping and RUL features (Phase 3b, task 2)

Mapping lives in `backend/app/services/ml/dataset_mapping.py` (`CMAPSS_SENSOR_MAP`,
`map_cmapss_rows_to_readings`); features in `backend/app/ml/rul_features.py` and
`rul_dataset.py`. Sensor definitions were checked against Table 2 of the PHM08 paper
(`Damage Propagation Modeling.pdf`).

- **Sensor mapping:** each `sensor_N` column becomes `sensor_readings.sensor_type = cmapss_sN`
  with its physical unit (degR, psia, rpm, pps/psi, lbm/s, ratio); e.g. `sensor_2` = T24,
  total temperature at LPC outlet (degR). Channels keep C-MAPSS identity rather than being
  renamed to facility-style names, because these are simulated turbofan engines. One engine =
  one asset (`uuid5` of `FDxxx:unit`); one cycle = one snapshot, `cycle_interval` apart.
- **Separate from the risk model:** RUL has its own feature layout. `feature_vector.py` (238
  columns) is frozen by the trained risk model and is untouched.
- **Features (per kept sensor):** latest value, short-window mean (5 cycles), long-window mean,
  std and least-squares slope (30 cycles), plus `cycle` (asset age) and `window_len`. All
  computed by one function, `window_features`, for training and serving, using only rows up
  to the current cycle.
- **Sensor selection and scaling:** constant sensors are dropped (FD001: 6 dropped, 15 kept;
  FD002/FD004 keep all 21). Sensors are z-scored per operating regime (regime = setting 1 and
  3 rounded; 1 regime for FD001/FD003, 6 for FD002/FD004), with parameters fitted on training
  data only and stored in `RulFeatureConfig` (saved alongside the model in task 3b.3). A
  serving row whose regime is not within 2.0 of a known one is rejected.
- **Labels:** training label at cycle t = engine's last cycle minus t, capped at 125 cycles
  (`RUL_CAP`; standard practice, since degradation is not observable early in life). Both the
  capped and uncapped values are kept. Test labels come from `RUL_FDxxx.txt` (the test
  trajectories end before failure), taken at each engine's final window.
- **Splitting:** every row carries its engine id; task 3b.3 must split by engine, never by row.

### RUL model training and results (Phase 3b, task 3)

Code: `backend/app/ml/rul_train.py`, `backend/scripts/train_rul_model.py`
(`python -m scripts.train_rul_model --subset FD001`). XGBoost regressor on the 3b.2 features,
target = RUL capped at 125 cycles.

**Protocol.** (1) 5-fold group CV *by engine* on the training engines to estimate error and
choose the boosting rounds (an engine never appears on both sides of a fold); (2) refit on all
training engines; (3) one evaluation on the official test set (each test engine's final
window, true RUL from `RUL_FDxxx.txt`). Nothing is tuned on the test set (a unit test proves
the model bytes do not change when the test labels do). Two baselines are reported with it:
the training-median predictor and a cycle-count-only model.

| Subset | CV RMSE (capped) | Test RMSE (true RUL) | Test MAE | Mean error (+ = late) | NASA score | Baseline RMSE: median / cycle-only |
|---|---|---|---|---|---|---|
| **FD001** (primary, seed 0) | 11.65 +/- 0.55 | **13.10** | 9.64 | +0.29 | 236 | 49.8 / 32.2 |
| FD001 seed 1 / seed 2 | 11.68 / 11.63 | 12.90 / 13.00 | 9.55 / 9.67 | +0.18 / +0.23 | 228 / 235 | - |
| FD002 (6 regimes) | 13.48 +/- 0.35 | 25.41 | 16.48 | -7.96 | 7,112 | 58.0 / 38.2 |
| FD003 | 10.16 +/- 1.43 | 14.23 | 10.25 | -0.11 | 325 | 63.1 / 35.3 |
| FD004 (6 regimes) | 13.14 +/- 1.13 | 26.44 | 18.08 | -8.01 | 5,062 | 65.0 / 47.7 |

- FD001 results are stable across seeds (test RMSE 12.9-13.1). FD002-FD004 are separate,
  labeled experiments, stored under their own `rul_model/<subset>/` prefix; they are never
  merged with FD001.
- **Publish gates** (CLI defaults, set against FD001 with margin; baselines are far outside
  them): test RMSE <= 20 and NASA score <= 1500. The six-regime subsets (FD002, FD004) do
  **not** pass these default gates (test RMSE ~25-26) and need explicit `--max-test-*`
  overrides to publish; that is a deliberate signal, not an oversight.
- **Open observation:** on FD002/FD004 the CV error (~13) is much lower than the test error
  (~25) and the model predicts slightly early (bias about -8). CV scores every cycle of an
  engine, many of them easy; the test set scores only each engine's truncated final window.
  The gap was not diagnosed further; improving the multi-regime models is not part of this
  task.
- **Limitations:** simulated turbofan engines, one fault-mode/operating profile per subset;
  see "C-MAPSS data profile". Each stored model records dataset name/citation/license/version,
  the SHA-256 of its three data files, hyperparameters, seed, library versions and the
  metrics above in `metadata.json`.

## IMS bearing data profile (Phase 3b, task 4)

> Empirical profile of the NASA/IMS bearing run-to-failure dataset, used by Phase 3b to train
> the vibration anomaly detector (tasks 3b.5-3b.6). Regenerate with
> `python -m scripts.acquire_ims --profile --json-output backend/data/processed/ims/profile.json
> --summaries-dir backend/data/processed/ims`.

- **Dataset used:** NASA/IMS Bearing Run-to-Failure Dataset ("4. Bearings" in the NASA PCoE
  repository)
- **Source:** NASA Prognostics Center of Excellence data repository; downloaded from the PHM
  mirror `phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip`
- **Citation:** Lee, J., Qiu, H., Yu, G., Lin, J., & Rexnord Technical Services (2007). IMS,
  University of Cincinnati. Bearing Data Set. NASA Ames Prognostics Data Repository. Method
  reference (from NASA's readme): Qiu, H., Lee, J., Lin, J. (2006). Wavelet filter-based weak
  signature detection method and its application on roller bearing prognostics. Journal of
  Sound and Vibration 289, 1066-1090.
- **License / access:** NASA open data repository. *Neither the readme nor the archive states a
  license; confirm the terms on the NASA portal page before external publication.*
- **Version / access date:** October 2026. SHA-256 of the downloaded zip:
  `21001ac266c465f5d345ec42d7b508c6a6328487fd9d4d7774422dd5ea10ad83`.
- **Layout:** the zip contains `IMS.7z`, which contains `1st_test.rar`, `2nd_test.rar`,
  `3rd_test.rar` and NASA's readme PDF (kept as `ims_readme.pdf`). The 3rd test is stored in a
  `4th_test/txt/` folder inside its RAR; the CLI normalises it to `3rd_test/`. Raw data is
  ~7 GB extracted and gitignored. Small per-file summaries (RMS, std, mean, peak, kurtosis per
  channel, ~2.8 MB) are committed in `backend/data/processed/ims/`.
- **Test rig (from NASA's readme):** four Rexnord ZA-2115 double-row bearings on one shaft,
  constant 2000 RPM, 6000 lbs radial load, force lubricated; PCB 353B33 accelerometers. Each
  file is a 1-second snapshot, 20,480 samples at 20 kHz, tab-separated text, one column per
  channel. All failures occurred after the bearing's designed life (> 100 million revolutions).

| Test set | Files | Channels | Window | Span (days) | Gaps (> 3x median) | Documented failure |
|---|---|---|---|---|---|---|
| 1st_test | 2,156 | 8 (2 per bearing) | 2003-10-22 12:06:24 to 2003-11-25 23:39:56 | 34.5 | 24 | Bearing 3 inner race (ch 5-6), bearing 4 roller element (ch 7-8) |
| 2nd_test | 984 | 4 (1 per bearing) | 2004-02-12 10:32:39 to 2004-02-19 06:22:39 | 6.8 | 0 | Bearing 1 outer race (ch 1) |
| 3rd_test | 6,324 | 4 (1 per bearing) | 2004-03-04 09:27:46 to 2004-04-18 02:42:55 | 44.7 | 4 | Bearing 3 outer race (ch 3) |

- **Sampling interval:** every 10 minutes (the first 43 files of test 1 are 5 minutes apart).
  The "gaps" are the experiment pausing overnight/between working days, as NASA's readme
  describes; the largest is ~6 days in test 1 (8,906 min).
- **Important discrepancy - the 3rd test.** NASA's readme says test 3 has **4,448 files ending
  2004-04-04 19:01:57**. The archive holds **6,324 files running to 2004-04-18**. File 4,448
  has exactly the readme's end timestamp, and **the bearing-3 failure signature (channel 3
  kurtosis up to 19.7, RMS rising) appears only in the extra files after it**; the first 4,448
  files are flat and healthy-looking (max kurtosis < 6). Use all 6,324 files as the
  run-to-failure record. Task 3b.6 must not treat "the first 4,448 files" as a complete run.
- **Data quality:** all 9,464 snapshot files parsed (0 unreadable, 0 NaN, every file exactly
  20,480 rows, consistent channel counts per test).
- **Degradation signatures in the summaries** (start = median of the first 10% of files, end =
  last 10%): test 1 channels 5, 7, 8 reach kurtosis 59-75 (bearings 3 and 4); test 2 channel 1
  RMS grows 2.3x with kurtosis up to 17 (bearing 1); test 3 channel 3 reaches kurtosis 19.7
  (bearing 3). Healthy channels stay near Gaussian kurtosis (~3-4).
- **Limitations:** a single lab rig at fixed speed and load, and only four documented failure
  events (test 1 has two, tests 2 and 3 one each). Any lead-time claim in 3b.6 rests on a
  handful of events and must be stated that way. The bearings are not factory assets
  specifically; mapping to facility vibration sensors is handled in later tasks.

### Vibration features and what they show on IMS (Phase 3b, task 5)

Code: `backend/app/ml/vibration_features.py` (`vibration_features`, `fit_baseline`,
`normalize`), batch builder `backend/scripts/build_vibration_features.py`. Output (committed,
gzip CSV, one row per file and channel, **raw un-normalised** features):
`backend/data/processed/ims/<test>_vibration_features.csv.gz` (3.9 MB total; 17,248 + 3,936 +
25,296 rows).

**Features (17 per channel, one 1-second / 20,480-sample snapshot):** RMS, peak, crest factor,
std, skewness, kurtosis; fraction of spectral power in 8 log-spaced bands (20 Hz - 10 kHz);
and the fraction of envelope-spectrum energy at each of three bearing defect frequencies and
their 2nd harmonics (+/-3 %): `env_bpfo` (outer race, 236.4 Hz), `env_bpfi` (inner race,
296.9 Hz), `env_bsf` (roller, 139.9 Hz). Envelope = analytic signal of the 2-8 kHz band-passed
signal (the demodulation band is a modelling assumption). Defect frequencies are computed from
the Rexnord ZA-2115 geometry as cited in the IMS literature (16 rollers, 2.815 in pitch, 0.331
in roller, 15.17 deg) at 2000 RPM; the geometry is **not** in NASA's readme and was not
re-verified against the paper here (the resulting 236/297/140 Hz match the values commonly
quoted for this rig). A constant (dead-sensor) signal returns all zeros, not NaNs.

**Per-channel baseline.** Each channel is z-scored against its own first 15 % of snapshots
(`baseline_length`, min 10); the std is floored at 0.1 % of |mean|. The same
function serves training (batch) and serving; a test checks the batch CSV equals the serving
function on the same file.

**What the features do and do not show (measured on the real data, baseline = first 15 %):**

| Result | Detail |
|---|---|
| Set 1, kurtosis | Max kurtosis z on the four failing channels (ch5-8) is 112-721; on the healthy bearings (ch1-4) at most 5.6. Clean separation. |
| Sets 2 and 3, outer race | The failing bearing has the largest late RMS z (set 2 ch1 ~257, set 3 ch3 ~98) and the largest late BPFO-envelope z (~66 and ~64). |
| **Cross-talk** | In sets 2 and 3 the *healthy* channels also rise late in the run (RMS z 11-100, BPFO envelope z 10-49): the failing bearing's vibration reaches neighbouring sensors. Late in those runs "healthy channel" labels are not clean. |
| Late kurtosis | Kurtosis is not monotonic: in set 3 ch3 its late median falls back to ~1 even though RMS and BPFO energy are very high (dense impacts). RMS and the BPFO ratio carry the late-stage signal; kurtosis the early one. |
| Weak features | `env_bpfi` and `env_bsf` do not discriminate in set 1: inner-race ch5-6 reach z of only 3.5-5.7 versus 6.3 on healthy ch4; the roller defect (ch7-8) is not visible at its BSF line (z ~0-1). Do not rely on them. |
| Early-life stability | Sets 1 and 2: no channel exceeds |z| = 6 on RMS between the baseline window and 40 % of the run. **Set 3 does not**: healthy ch2 and ch4 reach RMS z of 10-16 in that period (baseline drift, unexplained here). A fixed early baseline will raise false alarms there. |

Consequences for task 3b.6: false-alarm rates must be measured only on the pre-failure period
and the set 3 drift reported rather than hidden; detection on "healthy" channels late in a run
is not a false alarm; and lead-time results rest on four failure events.

### Vibration anomaly model training and results (Phase 3b, task 6)

Code: `backend/app/ml/vibration_train.py`, `backend/scripts/train_vibration_model.py`
(`python -m scripts.train_vibration_model`). Isolation Forest (200 trees) fitted on healthy,
per-channel baseline-normalised features (task 3b.5 features, **all 17, fixed up front and not
selected on failure outcomes**). The forest is stored as `.npz` tree arrays and scored by a numpy
re-implementation (`ForestScorer`) that is tested to match scikit-learn's `score_samples` to
1e-12 - no pickle is deserialised from the model store.

**Protocol (leave-one-test-out).** For each of the three IMS tests, the forest and alarm
threshold are fitted on the healthy baseline windows (first 15 % of each channel) of the *other
two* tests - 70 % of each window fits the forest, the remaining 30 % calibrates the threshold
(99.5th percentile) - and the held-out test is judged. A channel alarms after 5 consecutive
snapshots (50 min) above threshold. Everything after the baseline window is used only for
evaluation. The deployed model is refitted on all three tests' baseline windows. Two baselines run
with the identical rule: a threshold on RMS z-score and on kurtosis z-score.

**Metrics.** *False alarm*: a sustained alarm between the end of the baseline window and 40 % of
the run (the "early-life" window). *Detection*: the first sustained alarm on any channel of a
documented failing bearing after the baseline window (flagged if it falls inside the early-life
window, in which case it is a false alarm, not a detection). *Lead time*: from that alarm to the end
of the recording, which is when the test was stopped, **not** a labelled failure instant.

| Detector (seed 0) | Failing bearings detected | Median lead | Early-life false-alarm channels | Median alarm persistence |
|---|---|---|---|---|
| **Isolation Forest** | 4 / 4 | 115 h (39 % of run left) | 2 / 16 | 0.98 |
| RMS threshold | 4 / 4 | 101 h | 3 / 16 | 0.98 |
| Kurtosis threshold | 2 / 4 outside early life (set 1's two alarms fall inside it) | 41 h | 3 / 16 | 0.63 |

Per bearing, lead hours (Isolation Forest / RMS threshold): set 1 bearing 3: 276 / 49; set 1
bearing 4: 156 / 127; set 2 bearing 1: 74 / 74; set 3 bearing 3: 59 / 511.

**Read these numbers carefully.**
- **The forest does not clearly beat a plain RMS threshold.** It wins on set 1 bearing 3, ties on
  set 2, and loses on set 3 on the face of it. With four events this is not a statistically
  meaningful comparison.
- **Lead time can be inflated by spurious alarms.** The RMS threshold's 511 h on set 3 comes from
  a brief blip (z ~2.5, 27 March) that falls back to ~1 until the real rise in the last days; it is
  not early warning. Lead time alone cannot tell a true early detection from a mid-run false alarm,
  because there is no ground-truth degradation onset in this dataset. The *persistence* column (share
  of snapshots after the first alarm that stay above threshold) was added after seeing this as a
  post-hoc diagnostic; it is not used for any threshold or gate.
- **Seed sensitivity.** Over 12 seeds: all four bearings detected in 11 of 12 (seed 4 detected 3);
  early-life false-alarm channels 2-4 of 16; median lead 74-174 h. The swing is almost entirely
  set 1 bearing 4 (about 157 h or about 270 h depending on the seed, because the 99.5th-percentile
  threshold is estimated from a few hundred healthy points). Sets 2 (74 h) and 3 (~58 h) are stable.
  Treat the headline lead time as a range, not a number.
- **Cross-talk:** on sets 2 and 3 the non-failing channels also alarm late in the run (the failing
  bearing's vibration reaches them); those alarms are not counted as false alarms.
- **Set 3 baseline drift** (healthy channels 2 and 4) is inside the false-alarm accounting only up to
  40 % of the run.

**Publish gates (CLI defaults):** at least 3 of 4 failing bearings detected and at most 25 % of
channels with an early-life false alarm (held-out evaluation). Seed 0 and the 11 seeds that
detected 4 satisfy them with margin on detection; false-alarm fraction sits at 0.12-0.25.

**Limitations:** one lab rig at fixed speed and load; four documented failure events; lead time
measured against the end of the recording; threshold and lead times are seed-dependent as above.

### Degradation simulator calibration (Phase 3b, task 8)

Code: `backend/app/ml/sim_calibration.py` (+ `scripts/calibrate_simulator.py`), `vibration_sim.py`,
`engine_sim.py`, `degradation_sim.py`; enabled in the generator with `--degradation` (off by
default). `python -m scripts.calibrate_simulator` reads only the real IMS feature files and the
C-MAPSS training files and writes `backend/data/processed/sim_calibration.json`, which records the
source of every block. Nothing in it is hand-picked.

**Vibration (calibrated from IMS, 16 channels, 6 on the documented failing bearings):**
- healthy RMS level (median 0.115 across channel baselines, in the dataset's own units), its log-spread across channels
  (0.35, used to give each simulated channel its own level) and its within-channel CV (0.054);
- the median RMS and kurtosis growth, relative to each channel's own healthy baseline, at fixed
  fractions of the run for the failing channels: RMS x1.63 and kurtosis x1.20 at the end of the run.
  Growth is late: RMS is only ~x1.24 at 80 % of the run.
- A snapshot is Gaussian noise at the healthy RMS plus a fault component (noise confined to the
  2-8 kHz envelope band, amplitude-modulated at the BPFO frequency). Total RMS follows the
  calibrated growth curve; the modulation depth is solved so kurtosis follows its curve.

**Engine telemetry (calibrated from C-MAPSS FD001 training engines):** engine life distribution
(128-362 cycles, median 199), per-channel healthy level, between-engine and within-engine spread,
end-of-life shift, and the shared progress curve of the drift (14 informative sensors). One
simulated asset ages one cycle per tick, so its vibration and engine channels degrade together.

**Checks in `tests/unit/test_sim_calibration.py`:** simulated healthy RMS is within 3 % of the
calibrated level, simulated end-of-run RMS and kurtosis ratios are within 5 % / 8 % of the
calibrated ratios, the BPFO envelope feature rises with degradation, and a late-stage snapshot is
> 4 sigma from its own channel baseline while healthy snapshots are not.

**Known limits.** Healthy simulated vibration is Gaussian (kurtosis 3.0, real median 3.43); only
an outer-race defect is simulated; the real growth curve rests on four failure events, so the
simulated curve inherits that uncertainty; engine channels are independent (real sensors are
correlated) and only the FD001 single operating regime is simulated. Not validated against the
trained Isolation Forest in a live run in this session.

**Non-circularity.** `test_no_training_code_imports_the_simulator` fails if any module in
`app/ml` (other than the simulator modules) or any `scripts/train_*.py` imports simulator code,
and a second test fails if the simulator modules import training or model libraries. Waveforms
live in the `vibration-waveforms` bucket under `<tenant_id>/<asset_id>/<channel>/`, a prefix that
is always built from the caller's tenant, never read by training code.

### Risk model serving input (Phase 3 fix, found during the demo build)

Each AI4I 2020 row is one simultaneous snapshot of one machine, so the risk model was trained on
feature sets where every sensor window has count 1, stddev 0 and anomaly count 0. It is therefore
**served on the same form**: the asset's latest simultaneous reading of the five raw sensors (air and
process temperature, rotational speed, torque, tool wear), with temperature difference and mechanical
power recomputed from them as in training. Windowed 30/90/365-day statistics are not valid model input
(the model never saw a window with more than one reading) and are used only for explainability. An asset
missing a raw sensor, with readings more than 5 minutes apart, or with a non-finite value is not scored.
Check: held-out ROC-AUC computed through the serving path is 0.9738 (stored test AUC 0.974).
`app/ml/ai4i_replay.py` replays the held-out rows (the exact split `train_risk_model` uses) as live
snapshots for demos and end-to-end tests; it is demo-feed code and never an input to training.

### Live simulator calibration (Phase L, task L.2)

`backend/app/ml/live_machine_sim.py` (pure model) and `backend/scripts/live_simulator.py` (posts to the API)
feed the live demo. Like the other simulators it is calibrated from the real AI4I 2020 file and is never an
input to training (import-scan tests enforce both directions). Where each parameter comes from:

| Parameter | Source |
|---|---|
| Value ranges of the five sensors, and of process minus air temperature (7.6-12.1 K) | min / max of the 10,000 real rows; every simulated value is clipped to them |
| Torque -> speed relation (slope -15.74 rpm per Nm, residual std 86.8 rpm) | least-squares fit on the real rows, so the strong negative torque/speed correlation is kept |
| Within-machine persistence of air temperature and temperature difference | lag-1 autocorrelation in file order (0.9994 and 0.9975), **capped at 0.7** (see below) |
| Failure targets of injected faults | the dataset's published rules (Matzka 2020): OSF wear x torque > 11000 (L variant), TWF wear 200-240 min, HDF difference < 8.6 K with speed < 1380 rpm, PWF power > 9000 W |
| Each machine's starting point | the same real held-out AI4I record the demo already assigns it (`app/ml/ai4i_replay.py`) |

Three parameters **cannot be identified from AI4I** (it is cross-sectional: one row per machine, no
machine observed over time) and are therefore demo parameters, not data-derived numbers:
- `WITHIN_MACHINE_FRACTION = 0.05`: how much of the dataset's spread a single machine shows over time. Picked by measuring the real risk model's stability: at 0.25 six machines crossed the 50-point line within 150 ticks (healthy ones jumping from ~1 to ~97), at 0.05 only the two machines the demo deliberately places at ~58-59 (CT-2, CNC-2) still flicker.
- Tool-wear rate, default 1 min of wear per real hour (`--wear-min-per-hour`), chosen so healthy machines do not drift to failure during a demo.
- `MAX_PERSISTENCE = 0.7` and noise clipped at 2.0 sigma: at one record per 15 s tick the dataset's 0.9994 persistence is a drift so slow that the live anomaly check (z-score against recent history) flags the drift itself, and unclipped Gaussian noise would raise a toast about every 25 s. Measured with the capped model: about one false alert per 35 simulated minutes across 44 machines
  (`tests/unit/test_live_machine_sim.py`, which replays the live check's rule over 24 simulated hours).

Time is real, not compressed; only the AC-2 degradation storyline is time-compressed (`STORY_MINUTES`).

## What NOT to do

- Don't hand-invent failure rates, sensor ranges, or noise levels for the simulated IoT
  generator without grounding them in the data profile above.
- Don't train the model on the simulated generator's own output — that's circular and
  proves nothing about real-world generalization. The generator is calibrated *from* the
  real dataset; it is not a substitute *for* it in training.
- Don't mix multiple real datasets into one training run without documenting the merge
  logic — if you do use a secondary dataset (e.g., CWRU alongside AI4I 2020), keep them
  as separate, clearly labeled experiments rather than silently combined.