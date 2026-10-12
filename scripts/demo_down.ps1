<#
.SYNOPSIS
  Stops the processes started by scripts\demo_up.ps1 (and only those). Leaves the Docker containers running.
#>
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root ".demo-logs\pids.json"

if (-not (Test-Path $pidFile)) {
    Write-Host "demo_down: nothing to stop (no .demo-logs\pids.json)."
    exit 0
}

$pids = Get-Content $pidFile -Raw | ConvertFrom-Json
foreach ($entry in $pids.PSObject.Properties) {
    $id = [int]$entry.Value
    $process = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($process) {
        # /T also stops the children (npm -> node, celery -> its pool), /F because they run hidden.
        taskkill /PID $id /T /F *> $null
        Write-Host ("  stopped {0,-10} pid {1}" -f $entry.Name, $id)
    } else {
        Write-Host ("  {0,-10} pid {1} was already gone" -f $entry.Name, $id)
    }
}
Remove-Item $pidFile -Force
Write-Host "Demo processes stopped. Docker containers are still up (docker compose -p twin-demo -f infra\docker\docker-compose.demo.yml down to stop them)."
