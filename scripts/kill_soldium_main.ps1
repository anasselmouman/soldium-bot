# Stops local python.exe / py.exe runs of this project's main.py (duplicate pollers -> TelegramConflictError).
#
# Matches CommandLine that contains this repo's full path (any slash style).
# If you start the bot as:  python main.py   (no folder in the command line), pass -IncludeBareMainPy
# to also match short lines like:  "...\python.exe" main.py
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\kill_soldium_main.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\kill_soldium_main.ps1 -IncludeBareMainPy
#   powershell -ExecutionPolicy Bypass -File scripts\kill_soldium_main.ps1 -DryRun
param(
    [switch]$DryRun,
    [switch]$IncludeBareMainPy
)

$ErrorActionPreference = 'Continue'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rootNorm = ($projectRoot.ToLowerInvariant() -replace '/', '\')

function Test-CommandLineMatch {
    param([string]$CommandLine)
    if (-not $CommandLine) { return $false }
    if ($CommandLine -notmatch 'main\.py') { return $false }

    $cl = $CommandLine.ToLowerInvariant() -replace '/', '\'
    if ($cl.Contains($rootNorm)) { return $true }

    if ($IncludeBareMainPy) {
        if ($CommandLine -match '(?i)python(\d+)?(\.exe)?"\s+main\.py(\s|$)') { return $true }
        if ($CommandLine -match '(?i)python(\d+)?(\.exe)?\s+main\.py(\s|$)') { return $true }
        if ($CommandLine -match '(?i)py(\.exe)?"\s+(-\d+\s+)?main\.py(\s|$)') { return $true }
        if ($CommandLine -match '(?i)py(\.exe)?\s+(-\d+\s+)?main\.py(\s|$)') { return $true }
    }
    return $false
}

$hits = @('python.exe', 'python3.exe', 'py.exe') | ForEach-Object {
    Get-CimInstance Win32_Process -Filter "Name = '$_'" -ErrorAction SilentlyContinue |
        Where-Object { Test-CommandLineMatch $_.CommandLine }
} | Sort-Object ProcessId -Unique
if (-not $hits) {
    Write-Host 'No matching python.exe / py.exe processes for this project.'
    Write-Host "Project root: $projectRoot"
    if (-not $IncludeBareMainPy) {
        Write-Host 'Tip: if you use `python main.py` (bare name), re-run with -IncludeBareMainPy'
    }
    exit 0
}

foreach ($p in $hits) {
    Write-Host "PID $($p.ProcessId): $($p.CommandLine)"
    if (-not $DryRun) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
if ($DryRun) {
    Write-Host 'DryRun: no processes were stopped. Omit -DryRun to stop them.'
} else {
    Write-Host "Stopped $($hits.Count) process(es). Start one bot:  cd `"$projectRoot`"; python main.py"
}
