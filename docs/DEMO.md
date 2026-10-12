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
and the map URL at the end. Flags: `--skip-migrate`, `--skip-models`, `--no-pipeline`, `--history-days N`.
Wipe everything with `docker compose -p twin-demo -f docker-compose.demo.yml down -v`. Do not use
`--skip-models` on a freshly wiped stack (the models would not exist yet).

## 2. Start the app (3 terminals, each with the env loaded as above)

```powershell
# API
python -m uvicorn app.main:app --port 8000
# Celery worker (the "recompute risk scores" button, and the scheduled scoring below)
python -m celery -A app.core.celery_app worker --loglevel=info -P solo -Q celery,sandbox-results
# Frontend
cd ..\frontend; npm install; npm run dev
```

Optional, for live scores (Phase L task L.4): in a fourth terminal run Celery beat. Every 60 s it scores each facility
that has received new readings since its last score (so it does nothing until the live simulator below is running), never
two runs at once, and pushes a `scores_updated` event on the tenant's WebSocket channel.

```powershell
python -m celery -A app.core.celery_app beat --loglevel=info
```

Automatic analysis (Phase L task L.7): with beat and the worker running, a scoring pass that finds a machine newly at
high risk (score >= 67, previously below), or a facility with no analysis yet, queues one agent run on the worker. At most
one run per facility per 10 minutes (`AUTO_ANALYSIS_MIN_INTERVAL_SECONDS`), never overlapping, and a failed run is
recorded and not retried inside the window. These runs show in the Analysis tab's run list with trigger `auto_high_risk`
or `auto_startup`. It needs the LLM key like any manual run.

Open **http://localhost:5173** and click **Use demo account** - one click signs in and lands on the
facility picker, so there is no tenant id or URL to copy. The demo's tenant, facility and map ids are
fixed (tenant `...0001`, facility `...0002`, floor maps `...0003`/`...0004`/`...0005` for ground/first/roof, all prefixed `d3500000-0000-4000-8000-00000000`), so bookmarks and the login keep working after you
wipe and rebuild the stack. The button exists only in the dev server (`frontend/.env.development`).
The facility card's **Open map** goes to the ground-floor plan of the Meridian plant with its machines on
their equipment pads, coloured by risk (red = high), with the dependency lines, a legend and
**Fit view**. The floor switcher (top right) moves between the three floors; the sidebar lists all 44
machines grouped by floor with search and High risk / Vibration alarm filters; picking a machine on another
floor opens that floor with it selected (`?asset=<id>` in the URL); an asset's detail panel tags links to
other floors (`L1`, `L2`) and jumps there. The **Overview**, **Assets** and **Analysis** tabs hold the model and agent results (the old single Results page was split; `/results` URLs redirect).

### Demo accounts and what each role may do

`python -m scripts.seed_demo` (run by `demo_setup`, safe to repeat) creates these accounts in the demo tenant
(`d3500000-0000-4000-8000-000000000001`). The **Use demo account** button signs in as the admin.

| Role | Email | Password | May write telemetry (`POST /telemetry`)? |
|---|---|---|---|
| tenant_admin | `demo@twinlabs.com` | `demo-password-123` | yes |
| technician | `technician@twinlabs.com` | `demo-password-123` | yes |
| viewer | `viewer@twinlabs.com` | `demo-password-123` | **no** (403); reads everything else |
| technician (for the live simulator, Phase L task L.2) | `simulator@twinlabs.com` | `demo-password-123` | yes |

Sensor data can only be written by technician, facility_manager, tenant_admin and superadmin. Self-registration
creates viewers, so signing up does not grant write access. Real devices will need per-device API keys (not built).

Check it yourself (API on :8000; replace `ASSET_ID` with any UUID):

```powershell
$t = "d3500000-0000-4000-8000-000000000001"
foreach ($e in "viewer","technician") {
  $tok = (Invoke-RestMethod -Method Post localhost:8000/login -ContentType application/json `
    -Body (@{tenant_id=$t; email="$e@twinlabs.com"; password="demo-password-123"} | ConvertTo-Json)).access_token
  try { (Invoke-WebRequest -Method Post localhost:8000/telemetry -ContentType application/json `
      -Headers @{Authorization="Bearer $tok"} `
      -Body '{"readings":[{"asset_id":"ASSET_ID","sensor_type":"probe","value":1,"unit":"x","timestamp":"2026-01-01T00:00:00Z"}]}'
    ).StatusCode } catch { $_.Exception.Response.StatusCode.value__ }
}   # prints 403 for the viewer, 201 for the technician
```

(The API process must be restarted to pick up the L.1 code. The probe row is harmless; delete it afterwards.)

### Live simulator (Phase L task L.2)

Without it the demo is a static snapshot, and since L.3 the snapshot's backfilled readings age past 5 minutes, so most machines show
**No recent data** (a machine that stopped reporting is never shown as Healthy). Start the simulator to see them live. The simulator makes it behave like a running plant: every 15 s it posts
the five coherent AI4I sensors of all 44 machines to `POST /telemetry`, signed in as `simulator@twinlabs.com`
(a technician; see the accounts table above). Start it in its own terminal with the demo env loaded and the API running:

```powershell
cd backend
$env:MINIO_ENDPOINT = "127.0.0.1:9000"   # only if localhost:9000 stalls on your machine
python -m scripts.live_simulator                      # storyline "once": AC-2 degrades after 3 min over 15 min
python -m scripts.live_simulator --storyline off      # steady state only
python -m scripts.live_simulator --seed 7 --tick-seconds 5
```

While it runs, from another terminal (takes effect on the next tick):

```powershell
python -m scripts.live_simulator inject AC-2 overstrain    # or tool_wear | overheat | power
python -m scripts.live_simulator inject PB-1 sensor_dropout # the machine goes silent (for "No recent data")
python -m scripts.live_simulator clear PB-1
```

Stop with Ctrl+C (it finishes the current post and exits). If the API goes down it backs off (15, 30, 60 s) and
carries on when the API returns; a rejected login or a 403/422 stops it with a clear message. It never touches the
databases. Scores update when scoring runs (Recompute risk scores today; every minute once Phase L task L.4 lands).
It restarts every machine from its demo starting record, so restarting it also restores AC-2 to healthy.
Parameters and what is and is not calibrated from the real dataset: `docs/DATASETS.md` "Live simulator calibration".

## 3. Demo script (about 10 minutes)

| # | Show | Say |
|---|---|---|
| 1 | Facility picker, then **Open map** | A private spatial map of the facility; assets are nodes coloured by risk, with their dependencies. The floor plan is a generated demo drawing (`scripts/demo_floorplan.py`); a real upload goes through the sandboxed upload worker (needs the extra sandbox container). |
| 2 | Click an asset: telemetry chart | Live telemetry per asset, stored in TimescaleDB under row-level security per tenant. |
| 3 | Click the **Overview** tab, then **Assets** | One page for everything the models and agents produce. Everything the UI does is a tenant-scoped API (`localhost:8000/docs`). |
| 4 | Assets tab > health table (status column; search and quick filters) | Three real-data models: risk (AI4I 2020), remaining useful life (NASA C-MAPSS), vibration anomaly (NASA IMS bearings). Assets without the risk model's sensors show "not scored", not a guess. On a multi-floor plant, the floor chips above the table filter the table and KPI tiles (and set `?floor=N` in the URL); each chip also shows that floor's high-risk and alarm counts. Agent analysis below it stays facility-wide. |
| 5 | Click `Air Compressor AC-1`, then compare `Air Compressor AC-2` | AC-1: risk 95.8, RUL 3.1 cycles, vibration alarm. AC-2: has an RUL but no alarm. `Cooling Water Pump CWP-2` shows an *early* vibration warning while its risk is still low. The drawer shows which model version and dataset produced each number. The telemetry is a simulator calibrated from the real datasets. |
| 6 | Analysis tab: type `What if Air Compressor AC-1 fails?`, Run analysis (about 10 s) | Five agents: planner, risk assessment, maintenance + inventory, route optimization, simulation decision. Every agent output is schema-validated; validation errors are always shown. Then try `What if the Flux Capacitor fails?`: the run halts and says why instead of inventing an answer. |
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
- The Overview, Assets and Analysis tabs show model outputs and the agent run; the "About the models" page (top bar) carries the
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

## Risk history (trend charts)

The demo scores each machine once, so a trend would be a flat line. `--history-days N` adds N past
days of risk scores per machine (it is idempotent and works on an existing demo, for example
`python -m scripts.demo_setup --skip-migrate --skip-models --no-pipeline --history-days 6`).
It is a **scripted storyline built from real parts**: every point is the real risk model scoring a
real AI4I 2020 held-out record, and only the *choice* of which record stands for which day is
scripted (a machine that needs attention gets a rising path toward its real latest score, a
healthy one gets records that score near it). Those rows are marked `factors_json.demo_history`;
the latest score is the real one and is never touched. This is demo data, not telemetry history,
and nothing in it is used for training (`scripts/demo_history.py`).

## Automated browser check

With the stack running as above (and the demo seeded), `cd frontend; npm run e2e` drives a real Chrome
through the whole product: sign in as the demo manager, the "needs attention" list, opening a machine and
its risk trend, today's work, the analysis, filters, the keyboard (skip link, the trend chart's arrow
keys), every tab at phone width (390 x 844, no sideways scroll, all controls reachable) and an axe
accessibility audit of all seven screens including colour contrast. It uses the Chrome installed on the
machine; set `E2E_BROWSER=chromium` (after `npx playwright install chromium`) to use Playwright's own build.
These specs are not part of `npm test`, which needs no services.

## Known limits

- A manual analysis ("Run analysis") runs in the API process (`POST /facilities/{id}/agent-runs`, about 10 s); only
  the automatic ones run in the worker. The compose file's old `agent-runner` service pointed at a module that never existed and was removed.
- Floor plans are generated drawings; filler map tiles only cover zoom 0-5.
- The technician roster is a single built-in technician and downtime is a heuristic.
- The "why" text is rule-based, and the model cards are static copies of `docs/DATASETS.md`.
- `/facilities` is read-only (no create or edit).
