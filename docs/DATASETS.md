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

## Data profile (fill in after Phase 3, task 1)

> Update this section once the dataset is downloaded and explored — row count, class
> balance, feature ranges. This is what task 4 (simulator calibration) reads from.

- **Dataset used:**
- **Version / access date:**
- **Row count:**
- **Failure class balance:**
- **Feature ranges (for simulator calibration):**

## What NOT to do

- Don't hand-invent failure rates, sensor ranges, or noise levels for the simulated IoT
  generator without grounding them in the data profile above.
- Don't train the model on the simulated generator's own output — that's circular and
  proves nothing about real-world generalization. The generator is calibrated *from* the
  real dataset; it is not a substitute *for* it in training.
- Don't mix multiple real datasets into one training run without documenting the merge
  logic — if you do use a secondary dataset (e.g., CWRU alongside AI4I 2020), keep them
  as separate, clearly labeled experiments rather than silently combined.