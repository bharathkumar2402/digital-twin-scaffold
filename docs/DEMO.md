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
tenant/user/facility, draws and tiles three floor plans, creates the **Meridian Components Plant**
(44 machines on 3 floors, 60 dependency links, many crossing floors; the spec is
`backend/scripts/demo_factory.py`), backfills telemetry and vibration waveforms, scores all 44
machines, and runs the 5-agent pipeline once (about 3 minutes in total on a fresh stack). It prints the login
and the map URL at the end. Flags: `--skip-migrate`, `--skip-models`, `--no-pipeline`.
Wipe everything with `docker compose -p twin-demo -f docker-compose.demo.yml down -v`. Do not use
`--skip-models` on a freshly wiped stack (the models would not exist yet).

## 2. Start the app (3 terminals, each with the env loaded as above)

```powershell
# API
python -m uvicorn app.main:app --port 8000
# Celery worker (only needed for the "recompute risk scores" button/endpoint)
python -m celery -A app.core.celery_app worker --loglevel=info -P solo -Q celery,sandbox-results
# Frontend
cd ..\frontend; npm install; npm run dev
```

Open **http://localhost:5173** and click **Use demo account** - one click signs in and lands on the
facility picker, so there is no tenant id or URL to copy. The demo's tenant, facility and map ids are
fixed (tenant `...0001`, facility `...0002`, floor maps `...0003`/`...0004`/`...0005` for ground/first/roof, all prefixed `d3500000-0000-4000-8000-00000000`), so bookmarks and the login keep working after you
wipe and rebuild the stack. The button exists only in the dev server (`frontend/.env.development`).
The facility card's **Open map** goes to the ground-floor plan of the Meridian plant with its machines on
their equipment pads, coloured by risk (red = high), with the dependency lines, a legend and
**Fit view**. The floor switcher (top right) moves between the three floors; the sidebar lists all 44
machines grouped by floor with search and High risk / Vibration alarm filters; picking a machine on another
floor opens that floor with it selected (`?asset=<id>` in the URL); an asset's detail panel tags links to
other floors (`L1`, `L2`) and jumps there. **Results** is the model/agent page.

## 3. Demo script (about 10 minutes)

| # | Show | Say |
|---|---|---|
| 1 | Facility picker, then **Open map** | A private spatial map of the facility; assets are nodes coloured by risk, with their dependencies. The floor plan is a generated demo drawing (`scripts/demo_floorplan.py`); a real upload goes through the sandboxed upload worker (needs the extra sandbox container). |
| 2 | Click an asset: telemetry chart | Live telemetry per asset, stored in TimescaleDB under row-level security per tenant. |
| 3 | Click the **Results** tab in the top bar | One page for everything the models and agents produce. Everything the UI does is a tenant-scoped API (`localhost:8000/docs`). |
| 4 | Results > Asset health table | Three real-data models: risk (AI4I 2020), remaining useful life (NASA C-MAPSS), vibration anomaly (NASA IMS bearings). Assets without the risk model's sensors show "not scored", not a guess. |
| 5 | Click `Air Compressor AC-1`, then compare `Air Compressor AC-2` | AC-1: risk 95.8, RUL 3.1 cycles, vibration alarm. AC-2: has an RUL but no alarm. `Cooling Water Pump CWP-2` shows an *early* vibration warning while its risk is still low. The drawer shows which model version and dataset produced each number. The telemetry is a simulator calibrated from the real datasets. |
| 6 | Results > Agent analysis: type `What if Air Compressor AC-1 fails?`, Run analysis (about 10 s) | Five agents: planner, risk assessment, maintenance + inventory, route optimization, simulation decision. Every agent output is schema-validated; validation errors are always shown. Then try `What if the Flux Capacitor fails?`: the run halts and says why instead of inventing an answer. |
| 7 | `pytest tests/integration/test_degradation_e2e.py -v` | The same story as an automated test on fresh containers: trains the models, simulates, scores, asserts. |

Headline numbers on the Meridian plant (44/44 machines scored): `Air Compressor AC-1` risk 95.8 / RUL 3.1 /
vibration ALARM; `CNC Machine CNC-2` 59.4 with a vibration alarm; `Cooling Tower CT-2` 58.0; `Cooling Water
Pump CWP-2` risk 0.8 but a vibration alarm (early warning); `AC-2`, `CH-2`, `AC-3`, `GEN-1` have an RUL and no
alarm. Re-print the table with `python -m scripts.demo_setup --skip-migrate --skip-models`.

Where the risk numbers come from: each asset's latest snapshot is a **real AI4I 2020 record that
the risk model never trained on** (the rows its own train/test split held out). AC-1, CNC-2 and CT-2 replay
real failure records (overstrain, tool wear, power failure); the rest replay healthy ones. The model scores
that snapshot. On all 2,000 held-out rows it reaches ROC-AUC 0.97.

Agent run through the API (PowerShell):

```powershell
$tenant = "<tenant_id from demo_setup>"; $fac = "<facility_id>"
$t = (Invoke-RestMethod -Method Post http://localhost:8000/login -ContentType application/json `
  -Body (@{tenant_id=$tenant;email="demo@twinlabs.com";password="demo-password-123"}|ConvertTo-Json)).access_token
$h = @{Authorization="Bearer $t"}
Invoke-RestMethod -Method Post "http://localhost:8000/facilities/$fac/agent-runs" -Headers $h `
  -ContentType application/json -Body (@{trigger="user_query";user_query="What if Air Compressor AC-1 fails?"}|ConvertTo-Json) |
  Select-Object status, confidence, decision_report
```

The pipeline needs no external API key: the agents run deterministic tools (no LLM calls).

Real output on the demo plant (tests assert these against the plant spec):

```
query: What if Air Compressor AC-1 fails?    status=completed
  cascade root: Air Compressor AC-1 -> exactly the machines downstream_of("AC-1") in demo_factory.py,
                all on the first floor (a ground-floor cause, a first-floor effect)
query: What if Main Switchgear SG-1 fails?   status=completed
  cascade: plant-wide, machines on all three floors
query: What if the Flux Capacitor fails?     status=halted_for_escalation
  reason: asset 'Flux Capacitor' was not found in this facility (known assets: ...)
```

Technician routes are floor-aware: changing floor costs lift/stairs time (3 min per floor, plus climb
height), so a route that visits the roof pays for it. The run reads the facility's real assets and dependencies (a parent depends on its child, so a
failure of the child cascades to the parent) and scores it with the real models. If it cannot answer
(unknown asset, empty facility, scoring failure) it halts and records why - it never falls back to
sample data. Two things are still built-in demo choices: the technician roster (one technician at
the ground-floor map origin; there is no technician data model) and the downtime formula (a fixed
heuristic over the cascade, not a prediction).

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
| Login fails | Use the **Use demo account** button (fixed demo ids, so nothing to copy). Read the message: "Invalid tenant ID, email or password" is a real 401 (only possible if you typed the form by hand or the demo has not been seeded: run `demo_setup`). A message about the server (HTTP 500) or "could not reach the server" is not a credentials problem: usually a stale API process still holds port 8000 (`Get-NetTCPConnection -LocalPort 8000`; kill the old `uvicorn`, then start it again) or the API terminal did not load `demo.env.example`, so it is talking to the wrong database. Seed users must use a real-shaped domain (`.com`). |
| Map page stuck on "Processing floor plan..." | The upload row must be `tiled`; re-run `demo_setup` on a fresh stack. |
| Map looks blank/blurry or screenshots time out in browser automation | The tab is hidden: Chrome throttles MapLibre's animation frames and the map never fires `load`. Use a visible tab; not a product bug. |
| Anything else | Fall back to step 7: `pytest tests/integration/test_degradation_e2e.py -v` needs only Docker. |
