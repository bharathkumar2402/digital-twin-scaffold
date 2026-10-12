<#
.SYNOPSIS
  One command for the live demo (Phase L task L.9): API, Celery worker, Celery beat, live simulator, frontend.

.DESCRIPTION
  Starts the five processes in the background, each writing its own log to .demo-logs\, and records their
  process ids in .demo-logs\pids.json so scripts\demo_down.ps1 can stop exactly these and nothing else.
  Uses the local throwaway demo stack only (Docker containers from infra\docker\docker-compose.demo.yml).
  It never wipes volumes and never reads infra\docker\.env (the managed databases).

.PARAMETER Setup
  Run `python -m scripts.demo_setup` first (needed once on a fresh stack; takes a few minutes).
.PARAMETER NoSimulator
  Start everything except the live simulator (to run it yourself in a terminal).
.PARAMETER Storyline
  off | once | loop. Default once: AC-2 degrades after StoryDelayMin over StoryMinutes.
.PARAMETER StoryDelayMin / StoryMinutes
  Timing of the AC-2 storyline (defaults 3 and 15 minutes).
.PARAMETER NoFrontend
  Do not start the Vite dev server.

.EXAMPLE
  .\scripts\demo_up.ps1
  .\scripts\demo_up.ps1 -Setup
  .\scripts\demo_up.ps1 -StoryDelayMin 0.5 -StoryMinutes 3   # a short storyline, for the live e2e spec
#>
[CmdletBinding()]
param(
    [switch]$Setup,
    [switch]$NoSimulator,
    [switch]$NoFrontend,
    [ValidateSet("off", "once", "loop")][string]$Storyline = "once",
    [double]$StoryDelayMin = 3,
    [double]$StoryMinutes = 15,
    [int]$ApiPort = 8000,
    [int]$StartupTimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$logs = Join-Path $root ".demo-logs"
$pidFile = Join-Path $logs "pids.json"
$python = Join-Path $backend ".venv\Scripts\python.exe"

function Fail([string]$message) { Write-Host "demo_up: $message" -ForegroundColor Red; exit 1 }

# ---- checks ---------------------------------------------------------------------------------------

if (-not (Test-Path $python)) { Fail "no virtualenv at $python (see docs/DEMO.md, Prerequisites)" }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop and try again." }
if (Test-Path $pidFile) {
    $alive = @((Get-Content $pidFile -Raw | ConvertFrom-Json).PSObject.Properties |
        Where-Object { Get-Process -Id $_.Value -ErrorAction SilentlyContinue })
    if ($alive.Count -gt 0) { Fail "the demo is already running ($($alive.Name -join ', ')). Run scripts\demo_down.ps1 first." }
}
$busy = Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue
if ($busy) { Fail "port $ApiPort is already in use (another API?). Stop it or pass -ApiPort." }

# ---- local demo containers + environment (never infra\docker\.env) ---------------------------------

Write-Host "Starting the demo containers..."
Push-Location (Join-Path $root "infra\docker")
docker compose -p twin-demo -f docker-compose.demo.yml up -d --wait
$composeExit = $LASTEXITCODE
Pop-Location
if ($composeExit -ne 0) { Fail "docker compose could not start the demo containers" }

Get-Content (Join-Path $root "infra\docker\demo.env.example") |
    Where-Object { $_ -match '^[A-Z_]+=' } |
    ForEach-Object { $k, $v = $_ -split '=', 2; Set-Item "env:$k" $v }
$env:PATH = "$(Join-Path $backend '.venv\Scripts');$env:PATH"

New-Item -ItemType Directory -Force $logs | Out-Null

if ($Setup) {
    Write-Host "Running demo_setup (a few minutes on a fresh stack)..."
    Push-Location $backend
    & $python -m scripts.demo_setup
    $setupExit = $LASTEXITCODE
    Pop-Location
    if ($setupExit -ne 0) { Fail "demo_setup failed" }
}

# ---- processes ------------------------------------------------------------------------------------

$pids = [ordered]@{}

function Start-Demo([string]$name, [string]$file, [string[]]$arguments, [string]$dir) {
    # Start-Process joins the arguments with spaces and does not quote them, and this repo's path
    # contains spaces, so quote every argument that needs it.
    $arguments = @($arguments | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } })
    $out = Join-Path $logs "$name.log"
    $err = Join-Path $logs "$name.err.log"
    $p = Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $dir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    $pids[$name] = $p.Id
    ($pids | ConvertTo-Json) | Set-Content -Path $pidFile -Encoding utf8
    Write-Host ("  {0,-10} pid {1}   log: .demo-logs\{0}.log (+ .err.log)" -f $name, $p.Id)
}

Write-Host "Starting the app..."
Start-Demo "api" $python @("-m", "uvicorn", "app.main:app", "--port", "$ApiPort") $backend

$ready = $false
$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/openapi.json" -UseBasicParsing -TimeoutSec 3 | Out-Null
        $ready = $true
        break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) {
    & (Join-Path $PSScriptRoot "demo_down.ps1")
    Fail "the API did not answer within $StartupTimeoutSeconds s. See .demo-logs\api.err.log"
}

Start-Demo "worker" $python @("-m", "celery", "-A", "app.core.celery_app", "worker", "--loglevel=info", "-P", "solo", "-Q", "celery,sandbox-results") $backend
Start-Demo "beat" $python @("-m", "celery", "-A", "app.core.celery_app", "beat", "--loglevel=info", "-s", (Join-Path $logs "celerybeat-schedule")) $backend

if (-not $NoSimulator) {
    Start-Demo "simulator" $python @(
        "-m", "scripts.live_simulator",
        "--api", "http://127.0.0.1:$ApiPort",
        "--storyline", $Storyline,
        "--story-delay-min", "$StoryDelayMin",
        "--story-minutes", "$StoryMinutes"
    ) $backend
}
if (-not $NoFrontend) {
    Start-Demo "frontend" "npm.cmd" @("run", "dev") $frontend
}

Write-Host ""
Write-Host "Live demo is starting." -ForegroundColor Green
Write-Host "  App:      http://localhost:5173  (click 'Use demo account')"
Write-Host "  API docs: http://127.0.0.1:$ApiPort/docs"
if (-not $NoSimulator -and $Storyline -ne "off") {
    Write-Host "  Watch AC-2: it starts degrading after $StoryDelayMin min and reaches high risk about $StoryMinutes min later."
}
Write-Host "  Stop:     .\scripts\demo_down.ps1   (leaves Docker running)"
