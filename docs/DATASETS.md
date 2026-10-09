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

## What NOT to do

- Don't hand-invent failure rates, sensor ranges, or noise levels for the simulated IoT
  generator without grounding them in the data profile above.
- Don't train the model on the simulated generator's own output — that's circular and
  proves nothing about real-world generalization. The generator is calibrated *from* the
  real dataset; it is not a substitute *for* it in training.
- Don't mix multiple real datasets into one training run without documenting the merge
  logic — if you do use a secondary dataset (e.g., CWRU alongside AI4I 2020), keep them
  as separate, clearly labeled experiments rather than silently combined.