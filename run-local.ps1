# Run the Investment Bot locally for free (Windows / PowerShell).
#
#   .\run-local.ps1              # live terminal dashboard (start here)
#   .\run-local.ps1 -Mode api    # HTTP API on :8000, for the web dashboard
#   .\run-local.ps1 -Mode once   # single cycle, then exit (for Task Scheduler)
#   .\run-local.ps1 -Mode backtest
#   .\run-local.ps1 -Mode lab -For 8h -Round 1h   # find the clearest indicator package
#   .\run-local.ps1 -Mode champ -For 3d            # champ-set builder: until every indicator is tried
#   .\run-local.ps1 -Mode package                  # trade it on Alpaca paper, every 10 min
#   .\run-local.ps1 -Mode check                    # test mode: check every part, no trading
#
# Backtest, learn, lab, champ and package also take the strategy to try and the
# money to use: -Style refine|fewer|more|custom [-Custom "your words"] -Money 10000
#
# Every run of backtest, learn, lab, champ, package and check keeps a report
# in reports\ (Jarvis's TradeBot page lists them all).
#
# First run creates a .venv and installs dependencies; later runs reuse it.

param(
    [ValidateSet('terminal', 'api', 'once', 'backtest', 'learn', 'lab', 'champ', 'package', 'check')]
    [string]$Mode = 'terminal',
    [int]$Port = 8000,
    [string]$For = '1h',      # learn/lab: how long, e.g. 10m, 8h, 2d (champ: the most, default 3d)
    [string]$Round = '30m',   # learn/lab: longest a round may take (lab default 1h)
    [string]$Goal = '',       # learn: what to get better at
    [ValidateSet('refine', 'fewer', 'more', 'custom')]
    [string]$Style = 'refine', # the strategy to try (see investment_bot/style.py)
    [string]$Custom = '',     # -Style custom: the strategy in your own words
    [string]$Money = '0'      # money to trade with; 0 = the whole account
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

# The strategy and money, for the modes that take them. Windows PowerShell drops
# empty arguments and mangles double quotes, so custom words go in only if given.
$styleArgs = @('--style', $Style, '--money', ($Money -replace '[^0-9.]', ''))
if ($Money -notmatch '[0-9]') { $styleArgs[3] = '0' }
if ($Custom.Trim()) { $styleArgs += @('--custom', ($Custom -replace '"', "'")) }

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
        & $venvPython -m investment_bot backtest -c $config --html report.html @styleArgs
        if (Test-Path 'report.html') { Start-Process 'report.html' }
    }
    'learn' {
        Write-Host "=== Learning for $For (Ctrl-C stops; what it learns is kept) ===" -ForegroundColor Cyan
        $learnArgs = @('-m', 'investment_bot', 'learn', '-c', $config, '--for', $For, '--round', $Round) + $styleArgs
        # Windows PowerShell drops empty arguments, so only pass a goal there is.
        # Windows PowerShell also mangles double quotes inside native arguments.
        if ($Goal.Trim()) { $learnArgs += @('--goal', ($Goal -replace '"', "'")) }
        & $venvPython @learnArgs
    }
    'lab' {
        $labRound = if ($PSBoundParameters.ContainsKey('Round')) { $Round } else { '1h' }
        Write-Host "=== Indicator lab for $For, rounds up to $labRound (Ctrl-C stops; results kept) ===" -ForegroundColor Cyan
        & $venvPython -m investment_bot lab -c $config --for $For --round $labRound @styleArgs
    }
    'champ' {
        $champFor = if ($PSBoundParameters.ContainsKey('For')) { $For } else { '3d' }
        $labRound = if ($PSBoundParameters.ContainsKey('Round')) { $Round } else { '1h' }
        Write-Host "=== Champ-set builder: until every indicator is tried (at most $champFor; Ctrl-C stops; results kept) ===" -ForegroundColor Cyan
        & $venvPython -m investment_bot lab -c $config --for $champFor --round $labRound --until-done @styleArgs
    }
    'check' {
        Write-Host '=== Test mode: checking every part of the bot (no trading) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot check -c $config
    }
    'package' {
        Write-Host '=== Trading the champion package on Alpaca (Ctrl-C stops) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot trade-package -c $config @styleArgs
    }
    'api' {
        Write-Host "=== API on http://localhost:$Port ===" -ForegroundColor Cyan
        Write-Host 'Point your dashboard at it with INVESTMENT_BOT_URL.' -ForegroundColor DarkGray
        Write-Host 'For phone access, run this in a second window:' -ForegroundColor DarkGray
        Write-Host "  cloudflared tunnel --url http://localhost:$Port" -ForegroundColor DarkGray
        & $venvPython -m investment_bot serve -c $config --port $Port
    }
}
