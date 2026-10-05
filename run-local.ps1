# Run the Investment Bot locally for free (Windows / PowerShell).
#
#   .\run-local.ps1              # live terminal dashboard (start here)
#   .\run-local.ps1 -Mode api    # HTTP API on :8000, for the web dashboard
#   .\run-local.ps1 -Mode once   # single cycle, then exit (for Task Scheduler)
#   .\run-local.ps1 -Mode backtest
#   .\run-local.ps1 -Mode lab -For 8h -Round 1h   # find the clearest indicator package
#   .\run-local.ps1 -Mode package                  # trade it on Alpaca paper, every 10 min
#   .\run-local.ps1 -Mode research                 # watch news (and X) about the symbols
#   .\run-local.ps1 -Mode research-trade           # ...and trade it on Alpaca paper
#
# Add -Research watch (or trade) to any other mode to run news research
# alongside it, e.g.  .\run-local.ps1 -Mode package -Research trade
#
# First run creates a .venv and installs dependencies; later runs reuse it.

param(
    [ValidateSet('terminal', 'api', 'once', 'backtest', 'learn', 'lab', 'package', 'research', 'research-trade')]
    [string]$Mode = 'terminal',
    [int]$Port = 8000,
    [string]$For = '1h',      # learn: how long, e.g. 10m, 8h, 2d
    [string]$Round = '30m',   # learn/lab: longest a round may take (lab default 1h)
    [string]$Goal = '',       # learn: what to get better at
    [ValidateSet('off', 'watch', 'trade')]
    [string]$Research = 'off' # news research alongside the mode: watch, or trade it too
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
$news = @('--research', $Research)

if ($Research -ne 'off' -and $Mode -notlike 'research*') {
    Write-Host "=== News research runs alongside ($Research); its lines start with [news] ===" -ForegroundColor DarkCyan
}

switch ($Mode) {
    'terminal' {
        Write-Host '=== Paper trading (Ctrl-C to stop; state is saved) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot trade -c $config @news
    }
    'once' {
        & $venvPython -m investment_bot trade --once -c $config @news
    }
    'backtest' {
        Write-Host '=== Backtesting; report will open in your browser ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot backtest -c $config --html report.html @news
        if (Test-Path 'report.html') { Start-Process 'report.html' }
    }
    'learn' {
        Write-Host "=== Learning for $For (Ctrl-C stops; what it learns is kept) ===" -ForegroundColor Cyan
        $learnArgs = @('-m', 'investment_bot', 'learn', '-c', $config, '--for', $For, '--round', $Round) + $news
        # Windows PowerShell drops empty arguments, so only pass a goal there is.
        # Windows PowerShell also mangles double quotes inside native arguments.
        if ($Goal.Trim()) { $learnArgs += @('--goal', ($Goal -replace '"', "'")) }
        & $venvPython @learnArgs
    }
    'lab' {
        $labRound = if ($PSBoundParameters.ContainsKey('Round')) { $Round } else { '1h' }
        Write-Host "=== Indicator lab for $For, rounds up to $labRound (Ctrl-C stops; results kept) ===" -ForegroundColor Cyan
        & $venvPython -m investment_bot lab -c $config --for $For --round $labRound @news
    }
    'package' {
        Write-Host '=== Trading the champion package on Alpaca (Ctrl-C stops) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot trade-package -c $config @news
    }
    'research' {
        Write-Host '=== Watching the news (Ctrl-C stops; everything collected is kept) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot research -c $config
    }
    'research-trade' {
        Write-Host '=== Watching and trading the news on Alpaca paper (Ctrl-C stops) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot research --trade -c $config
    }
    'api' {
        Write-Host "=== API on http://localhost:$Port ===" -ForegroundColor Cyan
        Write-Host 'Point your dashboard at it with INVESTMENT_BOT_URL.' -ForegroundColor DarkGray
        Write-Host 'For phone access, run this in a second window:' -ForegroundColor DarkGray
        Write-Host "  cloudflared tunnel --url http://localhost:$Port" -ForegroundColor DarkGray
        & $venvPython -m investment_bot serve -c $config --port $Port @news
    }
}
