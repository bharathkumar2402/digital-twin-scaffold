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
| 3 | API docs at `localhost:8000/docs` | Everything the UI does is a tenant-scoped API. |
| 4 | Terminal: the health table below | Three real-data models: risk (AI4I 2020), remaining useful life (NASA C-MAPSS), vibration anomaly (NASA IMS bearings). |
| 5 | `Compressor Engine A (aged)` vs `(new)` | Aged engine: RUL 3.6 cycles and a vibration alarm on both channels. New engine: RUL 115.9, no alarm. The telemetry is a simulator calibrated from the real datasets. |
| 6 | Agent run (below) | Five agents: planner, risk assessment, maintenance + inventory, route optimization, simulation decision. Every agent output is schema-validated before it is used. |
| 7 | `pytest tests/integration/test_degradation_e2e.py -v` | The same story as an automated test on fresh containers: trains the models, simulates, scores, asserts. |

Asset health table (from `demo_setup`'s output, or re-print it by re-running with
`--skip-migrate --skip-models`):

```
asset                            risk  RUL (cycles)  vibration alarm
Compressor Engine A (aged)       99.8           3.6              YES
Compressor Engine B (new)        99.8         115.9               no
...
```

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

## 4. What to say honestly (do not skip this)

- The models are trained on real, cited datasets (AI4I 2020, NASA C-MAPSS, NASA IMS). They
  classify risk, estimate remaining life and flag anomalies - they do **not** forecast sensor values.
- They accept specific sensor schemas, not arbitrary data. RUL needs exactly the 24 C-MAPSS
  channels; vibration needs 20 kHz accelerometer waveforms; both decline (no result) otherwise.
- The datasets are turbofan engines, a lab bearing rig and a synthetic-but-published machine
  dataset - not factory equipment. Nothing here shows accuracy on a real facility.
- The live feed is calibrated simulation, because no real facility feed exists.
- **Known issue: the risk score is not trustworthy in the live demo.** The risk model was
  trained on single-snapshot AI4I rows but is served on windows of many readings (a
  training/serving mismatch), and assets without the AI4I sensors are zero-filled. That is why
  both engines show a risk of 99.8 and the plant assets show about 0.3 regardless of their
  telemetry. RUL and vibration are not affected. See `docs/PROGRESS_LOG.md`.
- The frontend has a map, a telemetry chart and risk colors only; RUL, vibration and the
  agent output are shown through the API and tests (a results page is planned).
- The vibration detector does not clearly beat a plain RMS threshold, and that comparison rests
  on four failure events (`docs/DATASETS.md`).

## 5. If something breaks

| Symptom | Fix |
|---|---|
| `demo_setup` cannot connect | `docker compose -p twin-demo ... ps`; wait for `healthy`; confirm the env is loaded in *this* shell (`$env:POSTGRES_PORT` should be `5433`). |
| Login says "Invalid email or password" | Use the `tenant_id` printed by the latest `demo_setup`; seed users must use a real-shaped domain (`.com`). |
| Map page stuck on "Processing floor plan..." | The upload row must be `tiled`; re-run `demo_setup` on a fresh stack. Tiles 404 by design with the placeholder map. |
| Anything else | Fall back to step 7: `pytest tests/integration/test_degradation_e2e.py -v` needs only Docker. |
