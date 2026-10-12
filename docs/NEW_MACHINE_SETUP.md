# New machine setup: get the live demo running and present it

**Audience: a coding agent (or a person) setting this project up on a fresh Windows laptop, to demo it to a mentor.**
Follow the steps in order. Every step has a check; do not continue past a failed check, fix it first.

Status of this guide: written from the repo and from runs on the original development machine. It has **not** been
executed on a clean second machine, so treat the "Expected" lines as the target and report anything that differs.

---

## 0. Rules for the agent

1. **Use only the local demo stack** (Docker project `twin-demo`, ports below). Never read, edit or use
   `infra/docker/.env` (managed databases) and never put credentials in any file.
2. **Do not run `docker compose ... down -v`, `docker system prune`, or delete `.demo-logs/` mid-demo.** Wiping volumes
   erases the seeded demo and costs 5+ minutes to rebuild.
3. **No API keys are needed.** The agents are deterministic tools (OR-Tools, NetworkX, scikit-learn/XGBoost); there are no
   LLM calls. If something asks for an Anthropic/Claude key, you are looking at the wrong thing.
4. This repo is a single-task-at-a-time project governed by `CLAUDE.md` (plan first, human approval before every commit).
   For *demoing* you change nothing. If you are asked to continue development, read `CLAUDE.md` first.
5. Do not edit files just to make the demo pass. If a check fails, report the real error.
6. Never claim the demo works because a process started. Use the verification commands in section 5.

---

## 1. Requirements

| Need | Detail |
|---|---|
| OS | Windows 10 or 11. The launcher scripts are PowerShell. macOS/Linux are not supported by the scripts (untested). |
| RAM | 16 GB recommended. Docker Desktop needs at least 8 GB allocated (Settings > Resources). |
| Disk | About 15 GB free. TimescaleDB image alone is 2.3 GB. |
| Git | Any recent version. |
| Docker Desktop | Installed **and running** (whale icon steady). WSL2 backend is the default and fine. |
| Python | 3.11 or 3.12 recommended (`requires-python >=3.11`; the original machine used 3.14.3). |
| Node.js | 20 or newer (original machine: 24). |
| Google Chrome | Only for the browser tests and for presenting. |
| Internet | Needed once: Docker images, pip, npm, and the NASA C-MAPSS dataset. After setup the demo should run offline (not tested). |
| GitHub access | Read access to `https://github.com/bharathkumar2402/digital-twin-scaffold` (collaborator, fork, or public). |
| Free ports | `5173` (UI), `8000` (API), `5433` (Postgres), `5434` (TimescaleDB), `6380` (Redis), `9000`/`9001` (MinIO). |

### Preflight checks (run all, all must pass)

```powershell
git --version
docker version --format '{{.Server.Version}}'     # must print a version, not an error
node --version                                      # v20 or higher
py -0p                                              # lists installed Pythons; need 3.11 or 3.12 (or python --version)
# ports must be free: this should print nothing
Get-NetTCPConnection -State Listen -LocalPort 5173,8000,5433,5434,6380,9000,9001 -ErrorAction SilentlyContinue
```

If a port is taken by something else, stop that program. Do not change the repo's ports.

---

## 2. One-time setup (about 15-25 minutes, mostly downloads and model training)

Run from a normal (non-admin) PowerShell. Replace nothing; the paths are relative to the clone.

```powershell
# 2.1 clone
git clone https://github.com/bharathkumar2402/digital-twin-scaffold.git
cd digital-twin-scaffold

# 2.2 backend virtualenv. The launcher REQUIRES it at backend\.venv
cd backend
py -3.12 -m venv .venv            # or: py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"

# 2.3 real datasets that are not in git (NASA C-MAPSS, ~45 MB download, needed to train the remaining-life model)
.\.venv\Scripts\python -m scripts.acquire_cmapss
cd ..

# 2.4 frontend dependencies
cd frontend
npm install
cd ..
```

Checks:

```powershell
Test-Path backend\.venv\Scripts\python.exe                          # True
backend\.venv\Scripts\python -c "import fastapi, celery, xgboost, ortools, langgraph; print('deps ok')"   # deps ok
Get-ChildItem backend\data\raw\cmapss | Measure-Object | % Count    # > 0 (the FD001-FD004 files)
Test-Path backend\data\raw\ai4i2020.csv                             # True (this one is in git)
Test-Path frontend\node_modules                                     # True
```

Notes:
- The huge IMS bearing archive (about 6 GB) is **not** needed: the processed features are already in git
  (`backend/data/processed/ims/`).
- If `pip install` fails on a compiled package, you are probably on a Python version without wheels: use 3.11 or 3.12.

### 2.5 Seed the demo and start everything (first run only: add `-Setup`)

Docker Desktop must be running before this.

```powershell
.\scripts\demo_up.ps1 -Setup
```

If PowerShell refuses to run scripts: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo_up.ps1 -Setup`.

What it does: starts the four demo containers (waits until healthy), runs `demo_setup` (migrates both databases, trains
the three models from the real datasets into MinIO, seeds the demo tenant and the 44-machine, 3-floor "Meridian Components
Plant", draws floor plans, backfills telemetry, scores everything, runs the 5-agent pipeline once; about 3-5 minutes), then
starts API, Celery worker, Celery beat, the live simulator and the frontend in the background.

Expected end of output:

```
Live demo is starting.
  App:      http://localhost:5173  (click 'Use demo account')
  API docs: http://127.0.0.1:8000/docs
  Stop:     .\scripts\demo_down.ps1   (leaves Docker running)
```

Optional but recommended for the mentor demo, so the trend charts are not flat lines on day one (adds 6 days of scripted
risk history built from real model scores; idempotent, safe to repeat; run in a shell with the demo env loaded, see below):

```powershell
cd backend
Get-Content ..\infra\docker\demo.env.example | Where-Object { $_ -match '^[A-Z_]+=' } |
  ForEach-Object { $k,$v = $_ -split '=',2; Set-Item "env:$k" $v }
.\.venv\Scripts\python -m scripts.demo_setup --skip-migrate --skip-models --no-pipeline --history-days 6
cd ..
```

---

## 3. Daily start and stop

```powershell
.\scripts\demo_up.ps1                      # start (containers are reused; no -Setup)
.\scripts\demo_down.ps1                    # stop the five processes; Docker keeps running
```

Useful options for `demo_up.ps1`:

| Option | Meaning |
|---|---|
| `-StoryDelayMin 1 -StoryMinutes 5` | Short storyline for a quick demo (default: AC-2 starts degrading after 3 min and reaches high risk about 15 min later) |
| `-Storyline off` | Steady state only, nothing degrades |
| `-NoSimulator` | Start everything except the simulator (run it yourself) |
| `-NoFrontend` | Skip the Vite dev server |

The script refuses to start if the demo is already running or port 8000 is taken. Logs: `.demo-logs\` (`api`,
`worker`, `beat`, `simulator`, `frontend`, each as `<name>.log` and `<name>.err.log`; most output, including the simulator's, lands in the `.err.log` files; `pids.json` holds the process ids).

---

## 4. Wait for the data to be live

Right after start, the backfilled telemetry is hours old, so machines briefly show **No recent data** (the app never shows a
silent sensor as healthy). The simulator posts a reading for all 44 machines every 15 s and beat scores every 60 s, so
give it **about 1-2 minutes** before presenting. The header shows **Live**.

---

## 5. Verify it works (do all of these before telling anyone it is ready)

```powershell
# 1. containers healthy (4 lines, all "healthy" or "Up")
docker compose -p twin-demo -f infra\docker\docker-compose.demo.yml ps

# 2. API answers; frontend answers (both must print 200)
(Invoke-WebRequest http://127.0.0.1:8000/openapi.json -UseBasicParsing).StatusCode
(Invoke-WebRequest http://localhost:5173 -UseBasicParsing).StatusCode

# 3. all five processes alive (every line must say True)
(Get-Content .demo-logs\pids.json -Raw | ConvertFrom-Json).PSObject.Properties |
  ForEach-Object { "{0}: {1}" -f $_.Name, [bool](Get-Process -Id $_.Value -ErrorAction SilentlyContinue) }

# 4. no crash in the logs: these must not contain "Traceback" or "Error:" lines (warnings are fine)
Select-String -Path .demo-logs\*.err.log -Pattern "Traceback|Error:|Unable to" | Select-Object -First 10

# 5. beat is scoring (the scoring task should appear about once a minute)
Select-String -Path .demo-logseat.err.log -Pattern "score-all-facilities" | Select-Object -Last 2

# 6. the simulator is posting: every machine should have a very recent reading
$tenant = "d3500000-0000-4000-8000-000000000001"; $fac = "d3500000-0000-4000-8000-000000000002"
$tok = (Invoke-RestMethod -Method Post http://127.0.0.1:8000/login -ContentType application/json `
  -Body (@{tenant_id=$tenant; email="demo@twinlabs.com"; password="demo-password-123"} | ConvertTo-Json)).access_token
$f = Invoke-RestMethod "http://127.0.0.1:8000/facilities/$fac/telemetry-freshness" -Headers @{Authorization="Bearer $tok"}
$asOf = [datetime]$f.as_of
$ages = $f.assets | Where-Object { $_.last_reading_at } | ForEach-Object { ($asOf - [datetime]$_.last_reading_at).TotalSeconds }
"machines reporting: {0} of {1}; newest reading {2:N0}s ago; oldest {3:N0}s ago" -f $ages.Count, $f.assets.Count, ($ages | Measure-Object -Minimum).Minimum, ($ages | Measure-Object -Maximum).Maximum
# Expected: 44 of 44, oldest well under 60 s. (The simulator itself only logs on start and stop, so its log is almost empty.)
```

Then open **http://localhost:5173** in Chrome (a *visible* tab; a hidden tab never loads the map), click
**Use demo account**, open the **Meridian Components Plant** card > **Overview**. You should see KPI tiles, a
**Needs attention** list, and the **Live** indicator.

Demo accounts (fixed, demo-only, already in the repo; all passwords `demo-password-123`):
`demo@twinlabs.com` (tenant admin, the one-click button), `technician@twinlabs.com`, `viewer@twinlabs.com` (read-only).
Demo tenant id: `d3500000-0000-4000-8000-000000000001`.

---

## 6. Presenting to the mentor

Allow 10-15 minutes. The full talk track is in `docs/DEMO.md`: section 3 "Demo script" and section 4 "What to say
honestly". **Read section 4 and say it**: the models are trained on real public datasets (not factory equipment), the live
feed is a calibrated simulation, and the agents are deterministic tools, not an LLM.

Suggested flow for the live version:

1. **Overview**: facility summary, **Needs attention** list (highest risk first, with the reason and the recommended action).
2. **Open map**: three floors, machines coloured by risk, dependency lines; click a machine for its telemetry chart.
3. **Leave it alone for a couple of minutes** and point out that nothing is reloaded: "Updated N min ago" advances, the
   chart extends, scores change.
4. **The storyline**: Air Compressor **AC-2** degrades by itself. When it reaches high risk it enters Needs attention, a toast
   appears, and one automatic analysis runs on the worker (Analysis tab, run trigger `auto_high_risk`). Start with
   `-StoryDelayMin 1 -StoryMinutes 5` if time is short.
5. **No recent data**: in a second PowerShell (from `backend\`, with the demo env loaded as in section 2.5):
   `.\.venv\Scripts\python -m scripts.live_simulator inject PB-1 sensor_dropout --duration-s 900`. After 5 minutes PB-1 shows
   **No recent data**; `... live_simulator clear PB-1` brings it back within a minute. Never present a silent sensor as healthy.
6. **Work tab** (today's technician job list, floor-aware) and **Analysis tab**: type `What if Air Compressor AC-1 fails?`
   and run it (about 10 s). Then `What if the Flux Capacitor fails?`: it halts and says why instead of inventing an answer.
7. **Roles**: sign in as `viewer@twinlabs.com` to show the read-only view (a viewer cannot write telemetry: HTTP 403).

Do not quote the exact numbers in `docs/DEMO.md` section 3 (for example "AC-1 risk 95.8") during the live version: those are from
the static snapshot, and the live simulator changes scores. Read the numbers off the screen.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `demo_up.ps1` says Docker is not running | Start Docker Desktop, wait until it is idle, retry. |
| "already running" / "port 8000 is already in use" | `.\scripts\demo_down.ps1`; if something else owns the port, stop that program. |
| Containers unhealthy or restart-looping | Give Docker 8 GB+ RAM; `docker compose -p twin-demo -f infra\docker\docker-compose.demo.yml logs --tail 50`. |
| Everything shows **No recent data** for more than 2 minutes | The simulator is not posting: `Get-Content .demo-logs\simulator.err.log -Tail 20`, and re-run the freshness check in section 5. A common cause is the API not being up yet; restart with `demo_down.ps1` then `demo_up.ps1`. |
| Scores never change | Beat or worker is down: check `.demo-logs\beat.err.log` and `worker.err.log` (both must be running per section 5). |
| Scoring or model loading hangs (MinIO) | Set `$env:MINIO_ENDPOINT = "127.0.0.1:9000"` in the shell, then restart. `localhost:9000` stalls on some Windows machines. |
| Map is blank or blurry | The Chrome tab is hidden or in the background. Bring it to the front. |
| Login fails with "Invalid tenant ID, email or password" | The demo is not seeded: run section 2.5 with `-Setup`. |
| Login fails with a server error | A stale API process holds the port: `demo_down.ps1`, then `demo_up.ps1`. |
| `npm install` or `pip install` fails | Check the Python/Node versions in section 1; retry on a stable connection. |
| Stack seems corrupted | Last resort, **after asking the user**: `docker compose -p twin-demo -f infra\docker\docker-compose.demo.yml down -v` then redo section 2.5. This erases the demo data. |

Known, harmless: two `npm run e2e` smoke tests (`AC-1 first in Needs attention`, `3 rows on the ground floor`) fail on a stack
where the simulator has run, because AC-2 stays degraded in the database. They pass on a freshly seeded stack with the
simulator never started. They are not needed for the demo.

---

## 8. Optional: run the automated checks

Only if asked. Backend (needs Docker; uses throwaway containers):

```powershell
cd backend
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy app
.\.venv\Scripts\python -m pytest tests/unit -q          # ~7 min
.\.venv\Scripts\python -m pytest tests/cross_tenant -q  # ~5 min, tenant isolation
.\.venv\Scripts\python -m pytest tests/integration -q   # ~6 min
```

Frontend: `cd frontend; npm run lint; npm test; npm run build`. Browser tests against the running stack: `npm run e2e`
(real Chrome). The real-stack live test (about 10 minutes, needs `demo_up.ps1` running):
`$env:E2E_LIVE = "1"; npx playwright test live-stack`.

---

## 9. Definition of "the demo is ready"

All of these are true:

- [ ] Section 5 checks 1-6 pass (containers healthy, API and UI return 200, five processes alive, no crashes in logs, beat scoring, 44 of 44 machines reporting).
- [ ] After 2 minutes the Overview shows **Live**, no machine is stuck on **No recent data**, and "Updated N min ago" advances without a reload.
- [ ] The map shows the three floors with coloured machines.
- [ ] The Analysis tab answers `What if Air Compressor AC-1 fails?` with status `completed`.
- [ ] Docker Desktop, the laptop power, and sleep settings are set so nothing stops during the presentation.

If any box is unchecked, report exactly which and the log lines, rather than presenting a half-working demo.
