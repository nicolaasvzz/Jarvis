# Run the Investment Bot locally for free (Windows / PowerShell).
#
#   .\run-local.ps1              # live terminal dashboard (start here)
#   .\run-local.ps1 -Mode api    # HTTP API on :8000, for the web dashboard
#   .\run-local.ps1 -Mode once   # single cycle, then exit (for Task Scheduler)
#   .\run-local.ps1 -Mode backtest
#
# First run creates a .venv and installs dependencies; later runs reuse it.

param(
    [ValidateSet('terminal', 'api', 'once', 'backtest')]
    [string]$Mode = 'terminal',
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# --- find Python -----------------------------------------------------------
$py = $null
foreach ($candidate in @('py', 'python', 'python3')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $py = $candidate; break }
}
if (-not $py) {
    Write-Host "Python not found. Install it from https://python.org (tick 'Add to PATH')." -ForegroundColor Red
    exit 1
}

# --- set up the virtual environment ---------------------------------------
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host '=== First run: creating virtual environment ===' -ForegroundColor Cyan
    & $py -m venv .venv
    Write-Host '=== Installing dependencies (one time, ~1 min) ===' -ForegroundColor Cyan
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --quiet -r requirements.txt
    Write-Host 'Done.' -ForegroundColor Green
}

$config = 'config.local.yaml'

switch ($Mode) {
    'terminal' {
        Write-Host '=== Paper trading (Ctrl-C to stop; state is saved) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot trade -c $config
    }
    'once' {
        & $venvPython -m investment_bot trade --once -c $config
    }
    'backtest' {
        Write-Host '=== Backtesting; report will open in your browser ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot backtest -c $config --html report.html
        if (Test-Path 'report.html') { Start-Process 'report.html' }
    }
    'api' {
        Write-Host "=== API on http://localhost:$Port ===" -ForegroundColor Cyan
        Write-Host 'Point your dashboard at it with INVESTMENT_BOT_URL.' -ForegroundColor DarkGray
        Write-Host 'For phone access, run this in a second window:' -ForegroundColor DarkGray
        Write-Host "  cloudflared tunnel --url http://localhost:$Port" -ForegroundColor DarkGray
        & $venvPython -m investment_bot serve -c $config --port $Port
    }
}
