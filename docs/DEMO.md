# Demo runbook

How to run and present the platform from a clean machine, using a self-contained **local**
stack (throwaway Postgres, TimescaleDB, Redis, MinIO in Docker). Nothing here touches the
managed databases in `infra/docker/.env`.

Everything below was run end to end on 2026-10-10; the output shown is real.

## 0. Prerequisites

- Docker Desktop running; Node 20+; the backend venv at `backend/.venv` (with dev deps).
- Real data already in the repo: `backend/data/raw/{ai4i2020.csv,cmapss/}` and
  `backend/data/processed/ims/`.

## 1. One-time setup (about 3-5 minutes, mostly model training)

```powershell
# from the repo root
cd infra\docker
docker compose -p twin-demo -f docker-compose.demo.yml up -d

cd ..\..\backend
Get-Content ..\infra\docker\demo.env.example | Where-Object { $_ -match '^[A-Z_]+=' } |
  ForEach-Object { $k,$v = $_ -split '=',2; Set-Item "env:$k" $v }
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"

python -m scripts.demo_setup
```

`demo_setup` is idempotent. It migrates both databases, trains the risk, RUL and vibration
models from the **real** datasets into MinIO (skipping any already published), seeds the demo
tenant/user/facility, creates 6 assets with a dependency chain, backfills telemetry and
waveforms, scores the facility, and runs the 5-agent pipeline once. It prints the login
and the map URL at the end. Flags: `--skip-migrate`, `--skip-models`, `--no-pipeline`.
Wipe everything with `docker compose -p twin-demo -f docker-compose.demo.yml down -v`.

## 2. Start the app (3 terminals, each with the env loaded as above)

```powershell
# API
python -m uvicorn app.main:app --port 8000
# Celery worker (only needed for the "recompute risk scores" button/endpoint)
python -m celery -A app.core.celery_app worker --loglevel=info -P solo -Q celery,sandbox-results
# Frontend
cd ..\frontend; npm install; npm run dev
```

Open the map URL printed by `demo_setup` (it goes through `/login?next=...` on purpose: the
access token is held in memory only, so a hard reload logs you out).

## 3. Demo script (about 10 minutes)

| # | Show | Say |
|---|---|---|
| 1 | Map page, assets, dependency lines | A private spatial map of the facility; assets are nodes with dependencies. The background is a placeholder: a real floor plan goes through the sandboxed upload worker (needs the extra sandbox container). |
| 2 | Click an asset: telemetry chart | Live telemetry per asset, stored in TimescaleDB under row-level security per tenant. |
| 3 | Click **Results** (top-left of the map) | One page for everything the models and agents produce. Everything the UI does is a tenant-scoped API (`localhost:8000/docs`). |
| 4 | Results > Asset health table | Three real-data models: risk (AI4I 2020), remaining useful life (NASA C-MAPSS), vibration anomaly (NASA IMS bearings). Assets without the risk model's sensors show "not scored", not a guess. |
| 5 | Click `Compressor Engine A (aged)`, then compare `(new)` | Aged engine: RUL 3.6 cycles and a vibration alarm on both channels. New engine: RUL 115.9, no alarm. The drawer shows which model version and dataset produced each number. The telemetry is a simulator calibrated from the real datasets. |
| 6 | Results > Agent analysis: type `What if Primary Feed Pump 7 fails?`, Run analysis (about 10 s) | Five agents: planner, risk assessment, maintenance + inventory, route optimization, simulation decision. Every agent output is schema-validated; validation errors are always shown. Then try `What if the Flux Capacitor fails?`: the run halts and says why instead of inventing an answer. |
| 7 | `pytest tests/integration/test_degradation_e2e.py -v` | The same story as an automated test on fresh containers: trains the models, simulates, scores, asserts. |

Asset health table (from `demo_setup`'s output, or re-print it by re-running with
`--skip-migrate --skip-models`):

```
asset                            risk  RUL (cycles)  vibration alarm
Primary Feed Pump 7              98.7             -                -
Compressor Engine A (aged)       95.8           3.6              YES
Turbine Gen 1                     0.8             -                -
Cooling Fan 2                     0.7             -                -
Compressor Engine B (new)         0.4         115.9               no
Heat Exchanger Alpha              0.2             -                -
```

Where the risk numbers come from: each asset's latest snapshot is a **real AI4I 2020 record that
the risk model never trained on** (the rows its own train/test split held out). Pump 7 and the aged
engine replay real failure records (overstrain); the rest replay healthy ones. The model scores
that snapshot. On all 2,000 held-out rows it reaches ROC-AUC 0.97.

Agent run through the API (PowerShell):

```powershell
$tenant = "<tenant_id from demo_setup>"; $fac = "<facility_id>"
$t = (Invoke-RestMethod -Method Post http://localhost:8000/login -ContentType application/json `
  -Body (@{tenant_id=$tenant;email="demo@twinlabs.com";password="demo-password-123"}|ConvertTo-Json)).access_token
$h = @{Authorization="Bearer $t"}
Invoke-RestMethod -Method Post "http://localhost:8000/facilities/$fac/agent-runs" -Headers $h `
  -ContentType application/json -Body (@{trigger="user_query";user_query="What if Primary Feed Pump 7 fails?"}|ConvertTo-Json) |
  Select-Object status, confidence, decision_report
```

The pipeline needs no external API key: the agents run deterministic tools (no LLM calls).

Real output on the demo plant (verified through the HTTP API, asset ids mapped back to names):

```
query: What if Primary Feed Pump 7 fails?    status=completed
  maintenance: Primary Feed Pump 7 -> critical today; Compressor Engine A (aged) -> critical today;
               the other four -> low (in 14 days)
  cascade root: Primary Feed Pump 7 -> Heat Exchanger Alpha, Cooling Fan 2, Turbine Gen 1; 6.5 h downtime
query: What if the Flux Capacitor fails?     status=halted_for_escalation
  reason: asset 'Flux Capacitor' was not found in this facility (known assets: ...)
```

The run reads the facility's real assets and dependencies (a parent depends on its child, so a
failure of the child cascades to the parent) and scores it with the real models. If it cannot answer
(unknown asset, empty facility, scoring failure) it halts and records why - it never falls back to
sample data. Two things are still built-in demo choices: the technician roster (one technician at
the map origin; there is no technician data model) and the 6.5 h downtime formula (a fixed heuristic
over the cascade, not a prediction).

## 4. What to say honestly (do not skip this)

- The models are trained on real, cited datasets (AI4I 2020, NASA C-MAPSS, NASA IMS). They
  classify risk, estimate remaining life and flag anomalies - they do **not** forecast sensor values.
- They accept specific sensor schemas, not arbitrary data. Risk needs the five AI4I channels; RUL
  needs exactly the 24 C-MAPSS channels; vibration needs 20 kHz accelerometer waveforms. All three
  decline (no result) otherwise.
- The datasets are turbofan engines, a lab bearing rig and a synthetic-but-published machine
  dataset - not factory equipment. Nothing here shows accuracy on a real facility.
- The live feed is calibrated simulation, because no real facility feed exists.
- The risk model scores one simultaneous snapshot of an asset's five AI4I sensors (that is what
  it was trained on). An asset without those sensors, or with an inconsistent snapshot, gets **no
  score** (grey on the map) instead of a guess. The demo's snapshots are replayed real records, so
  they show the pipeline works - not accuracy on your own equipment. (A training/serving
  mismatch that made live risk scores meaningless was found and fixed while building this demo;
  details in `docs/PROGRESS_LOG.md`.)
- The agents are deterministic tools, not an LLM: scheduling, routing and the cascade are rule-based
  / solver-based (OR-Tools, NetworkX). Downtime is a heuristic and the technician roster is a built-in
  demo roster.
- The Results page shows model outputs and the agent run; "About these models" carries the
  headline metrics as static text copied from `docs/DATASETS.md` (dated), not live numbers. The
  "why" text beside each risk priority is a rule-based explanation over recent telemetry, not the
  model's own reasoning.
- The vibration detector does not clearly beat a plain RMS threshold, and that comparison rests
  on four failure events (`docs/DATASETS.md`).

## 5. If something breaks

| Symptom | Fix |
|---|---|
| `demo_setup` cannot connect | `docker compose -p twin-demo ... ps`; wait for `healthy`; confirm the env is loaded in *this* shell (`$env:POSTGRES_PORT` should be `5433`). |
| Login says "Invalid email or password" | Use the `tenant_id` printed by the latest `demo_setup`; seed users must use a real-shaped domain (`.com`). |
| Map page stuck on "Processing floor plan..." | The upload row must be `tiled`; re-run `demo_setup` on a fresh stack. Tiles 404 by design with the placeholder map. |
| Anything else | Fall back to step 7: `pytest tests/integration/test_degradation_e2e.py -v` needs only Docker. |
