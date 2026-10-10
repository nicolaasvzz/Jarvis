# Run the Investment Bot locally for free (Windows / PowerShell).
#
#   .\run-local.ps1              # live terminal dashboard (start here)
#   .\run-local.ps1 -Mode api    # HTTP API on :8000, for the web dashboard
#   .\run-local.ps1 -Mode once   # single cycle, then exit (for Task Scheduler)
#   .\run-local.ps1 -Mode backtest
#   .\run-local.ps1 -Mode champ                    # champ-set builder: 4h, 1h and 30m charts, one setup
#   .\run-local.ps1 -Mode package                  # trade its setups on Alpaca paper (1h decisions)
#   .\run-local.ps1 -Mode check                    # test mode: check every part, no trading
#   .\run-local.ps1 -Mode research                 # watch news (and X) about the symbols
#   .\run-local.ps1 -Mode research-trade           # ...and trade it on Alpaca paper
#   .\run-local.ps1 -Mode lab -For 8h -Round 1h   # the older indicator lab (10-minute packages)
#
# Backtest, learn, lab, champ and package also take the strategy to try and the
# money to use: -Style refine|fewer|more|custom [-Custom "your words"] -Money 10000
# [-Confidence 70] [-Bet 10] (percent; 0 keeps what the strategy says). Champ and
# package also take -Only stock|crypto (default: both).
#
# Every run of backtest, learn, lab, champ, package and check keeps a report
# in reports\ (Jarvis's TradeBot page lists them all).
#
# Add -Research watch (or trade) to any other mode to run news research
# alongside it, e.g.  .\run-local.ps1 -Mode package -Research trade
#
# First run creates a .venv and installs dependencies; later runs reuse it.

param(
    [ValidateSet('terminal', 'api', 'once', 'backtest', 'learn', 'lab', 'champ', 'package', 'check', 'research', 'research-trade')]
    [string]$Mode = 'terminal',
    [int]$Port = 8000,
    [string]$For = '1h',      # learn/lab: how long, e.g. 10m, 8h, 2d (champ: the most, default 3d)
    [string]$Round = '30m',   # learn/lab: longest a round may take (lab default 1h)
    [string]$Goal = '',       # learn: what to get better at
    [ValidateSet('refine', 'fewer', 'more', 'custom')]
    [string]$Style = 'refine', # the strategy to try (see investment_bot/style.py)
    [string]$Custom = '',     # -Style custom: the strategy in your own words
    [string]$Money = '0',     # money to trade with; 0 = the whole account
    [string]$Confidence = '0', # confidence needed, %; 0 = the strategy's own
    [string]$Bet = '0',       # share of the money a trade, %; 0 = the strategy's own
    [ValidateSet('off', 'watch', 'trade')]
    [string]$Research = 'off', # news research alongside the mode: watch, or trade it too
    [ValidateSet('both', 'stock', 'stocks', 'crypto')]
    [string]$Only = 'both'    # champ/package: stocks and crypto, or just one of them
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

# The strategy and money, for the modes that take them. Windows PowerShell drops
# empty arguments and mangles double quotes, so custom words go in only if given.
$styleArgs = @('--style', $Style, '--money', ($Money -replace '[^0-9.]', ''))
if ($Money -notmatch '[0-9]') { $styleArgs[3] = '0' }
foreach ($pair in @(@('--confidence', $Confidence), @('--bet', $Bet))) {
    $number = $pair[1] -replace '[^0-9.]', ''
    if ($number -and [double]$number -gt 0) { $styleArgs += @($pair[0], $number) }
}
if ($Custom.Trim()) { $styleArgs += @('--custom', ($Custom -replace '"', "'")) }
$onlyArgs = @()
if ($Only -ne 'both') { $onlyArgs = @('--only', $Only) }

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
        & $venvPython -m investment_bot backtest -c $config --html report.html @styleArgs @news
        if (Test-Path 'report.html') { Start-Process 'report.html' }
    }
    'learn' {
        Write-Host "=== Learning for $For (Ctrl-C stops; what it learns is kept) ===" -ForegroundColor Cyan
        $learnArgs = @('-m', 'investment_bot', 'learn', '-c', $config, '--for', $For, '--round', $Round) + $styleArgs + $news
        # Windows PowerShell drops empty arguments, so only pass a goal there is.
        # Windows PowerShell also mangles double quotes inside native arguments.
        if ($Goal.Trim()) { $learnArgs += @('--goal', ($Goal -replace '"', "'")) }
        & $venvPython @learnArgs
    }
    'lab' {
        $labRound = if ($PSBoundParameters.ContainsKey('Round')) { $Round } else { '1h' }
        Write-Host "=== Indicator lab for $For, rounds up to $labRound (Ctrl-C stops; results kept) ===" -ForegroundColor Cyan
        & $venvPython -m investment_bot lab -c $config --for $For --round $labRound @styleArgs @news
    }
    'champ' {
        # -Round is still accepted (older Jarvis buttons send it) but no longer used.
        $champFor = if ($PSBoundParameters.ContainsKey('For')) { $For } else { '3d' }
        Write-Host "=== Champ-set builder: 4h, 1h and 30m charts, one setup trading the 1h (at most $champFor; Ctrl-C stops) ===" -ForegroundColor Cyan
        & $venvPython -m investment_bot champ -c $config --for $champFor @onlyArgs @styleArgs @news
    }
    'check' {
        Write-Host '=== Test mode: checking every part of the bot (no trading) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot check -c $config @news
    }
    'package' {
        Write-Host '=== Trading the champion setups on Alpaca: 1h decisions, watched every 10 min (Ctrl-C stops) ===' -ForegroundColor Cyan
        & $venvPython -m investment_bot trade-setup -c $config @onlyArgs @styleArgs @news
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
