<#
.SYNOPSIS
    Install, update, and start Jarvis - one file, safe to re-run.

.DESCRIPTION
    Does everything in docs/COMMANDS.md sections 1-3:
      * clones Jarvis, or updates an existing clone (stashing local edits)
      * creates the virtual environment in backend\.venv
      * installs the skill packs you ask for, plus their non-Python parts
        (the Chromium download, the Tesseract OCR binary and its PATH entry)
      * creates backend\.env and backend\config\config.yaml, generates the
        API token, fills in the workspace folder, and asks for your free
        Gemini API key and any other missing secret
      * prints the skill inventory, then starts Jarvis

    Also works in a backend folder on its own, without the rest of the
    repository: it then skips the git steps and sets up what is there.

    Every step is idempotent: nothing already done is done twice, so this is
    also the right thing to run each morning.

.EXAMPLE
    .\jarvis.ps1
    Update, make sure everything is installed, start Jarvis and open the
    dashboard (the Telegram bridge runs too, once it is configured).

.EXAMPLE
    .\jarvis.ps1 -Skills all
    Install every skill pack, then start.

.EXAMPLE
    .\jarvis.ps1 -Skills browser,vision -Start none
    Add just those two packs and exit without starting.

.EXAMPLE
    .\jarvis.ps1 -Start run -Request "organize my workspace by file type"
    Run one task in the terminal.

.NOTES
    If PowerShell refuses to run it, launch it like this:
      powershell -ExecutionPolicy Bypass -File .\backend\scripts\jarvis.ps1
#>
[CmdletBinding()]
param(
    # Where the Jarvis repository lives. Defaults to the repo this script sits
    # in, or ~\jarvis.
    [string] $Path,

    # Branch to track.
    [string] $Branch = 'main',

    [string] $Repo = 'https://github.com/nicolaasvzz/Jarvis.',

    # Skill packs to install: all, core, browser, desktop, vision, api, phone,
    # dash, voice, listen, llm, dev. 'core' = the 15 file + memory skills.
    # Comma-separated or repeated; a quoted "browser,vision" works too, which
    # is what -File passes through.
    [string[]] $Skills = @('all'),

    # What to do when setup finishes. 'dash' runs everything (API, dashboard,
    # and the Telegram bridge if configured) and opens the dashboard.
    [ValidateSet('dash', 'phone', 'serve', 'run', 'none')]
    [string] $Start = 'dash',

    # The task text, for -Start run.
    [string] $Request,

    # Skip the git pull.
    [switch] $NoUpdate,

    # Run pip even when nothing has changed since the last install.
    [switch] $Reinstall,

    # Don't try to install the Tesseract OCR binary.
    [switch] $NoTesseract
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Native exit codes are checked explicitly below; don't let PS7 also throw.
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$IsWin = if (Test-Path variable:IsWindows) { $IsWindows } else { $true }

# Anything that goes wrong should read as one plain sentence, not a stack dump.
trap {
    Write-Host "`nSetup stopped: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- output ---

function Write-Step { param([string]$Message) Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Write-Info { param([string]$Message) Write-Host "    $Message" -ForegroundColor Gray }
function Write-Good { param([string]$Message) Write-Host "    $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "    ! $Message" -ForegroundColor Yellow }

# Every native call below runs with $ErrorActionPreference = 'Continue'.
# Windows PowerShell 5.1 turns a native command's stderr into a terminating
# error when the preference is 'Stop' - and git, pip, winget and python's own
# logging all write to stderr routinely. Exit codes are the truth here, so
# they are checked explicitly instead.

# Run a command, let its output go to the console, and return nothing.
# Throws if it fails, so the script stops where the problem is.
function Invoke-Native {
    param(
        [Parameter(Mandatory)] [string] $Exe,
        [string[]] $Arguments = @(),
        # Swallow the command's own output.
        [switch] $Quiet
    )
    $ErrorActionPreference = 'Continue'
    if ($Quiet) { & $Exe @Arguments 2>&1 | Out-Null }
    else { & $Exe @Arguments | Out-Host }
    if ($LASTEXITCODE -ne 0) {
        throw "``$Exe $($Arguments -join ' ')`` failed with exit code $LASTEXITCODE."
    }
}

# Same, but for commands whose failure is survivable: returns the exit code
# instead of throwing.
function Get-NativeExitCode {
    param(
        [Parameter(Mandatory)] [string] $Exe,
        [string[]] $Arguments = @(),
        [switch] $Quiet
    )
    $ErrorActionPreference = 'Continue'
    if ($Quiet) { & $Exe @Arguments 2>&1 | Out-Null }
    else { & $Exe @Arguments | Out-Host }
    return $LASTEXITCODE
}

# For a command whose failure text needs interpreting: returns the exit code
# and the combined output as plain strings.
function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory)] [string] $Exe,
        [string[]] $Arguments = @()
    )
    $ErrorActionPreference = 'Continue'
    $out = @(& $Exe @Arguments 2>&1 | ForEach-Object { "$_" })
    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = $out }
}

# For commands we need to read: returns stdout lines, discards stderr.
function Get-NativeOutput {
    param(
        [Parameter(Mandatory)] [string] $Exe,
        [string[]] $Arguments = @()
    )
    $ErrorActionPreference = 'Continue'
    return @(& $Exe @Arguments 2>$null)
}

function Test-Exe { param([string]$Name) return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

# ------------------------------------------------------------ file edits ---

# Jarvis reads .env and config.yaml as UTF-8. Write them as UTF-8 without a
# BOM so a non-ASCII path (C:\Users\Jose\...) survives and no stray BOM ends
# up on the first key.
function Write-TextLines {
    param([string]$File, [System.Collections.Generic.List[string]]$Lines)
    $full = (Resolve-Path -LiteralPath $File).Path
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($full, $Lines, $utf8NoBom)
}

function Get-EnvValue {
    param([string]$File, [string]$Key)
    if (-not (Test-Path -LiteralPath $File)) { return '' }
    foreach ($line in @(Get-Content -LiteralPath $File)) {
        if ($line -match "^\s*$Key\s*=\s*(.*)$") { return $Matches[1].Trim() }
    }
    return ''
}

function Set-EnvValue {
    param([string]$File, [string]$Key, [string]$Value)
    $lines = @(Get-Content -LiteralPath $File)
    $out = New-Object System.Collections.Generic.List[string]
    $written = $false
    foreach ($line in $lines) {
        if ($line -match "^\s*$Key\s*=") { $out.Add("$Key=$Value"); $written = $true }
        else { $out.Add($line) }
    }
    if (-not $written) { $out.Add("$Key=$Value") }
    Write-TextLines $File $out
}

function Read-Secret {
    param([string]$Prompt)
    # Only ask when there is a real console to ask on: an unattended run
    # (scheduled task, piped input) must not stall on a prompt.
    if (-not [Environment]::UserInteractive) { return '' }
    if ([Console]::IsInputRedirected) { return '' }
    try {
        $secure = Read-Host -Prompt $Prompt -AsSecureString
    }
    catch {
        return ''    # no console attached (scheduled task, piped input)
    }
    if (-not $secure) { return '' }
    $plain = [System.Net.NetworkCredential]::new('', $secure).Password
    return $plain.Trim()
}

# Set "key: value" inside a top-level YAML section, adding it if absent.
function Set-YamlSectionKey {
    param([string]$File, [string]$Section, [string]$Key, [string]$Value)
    $entry = "  ${Key}: $Value"
    $out = New-Object System.Collections.Generic.List[string]
    $inSection = $false
    $done = $false
    foreach ($line in @(Get-Content -LiteralPath $File)) {
        if ($line -match '^[A-Za-z_][A-Za-z0-9_]*\s*:') {
            if ($inSection -and -not $done) { $out.Add($entry); $done = $true }
            $inSection = ($line -match "^$Section\s*:")
        }
        elseif ($inSection -and -not $done -and $line -match "^\s+$Key\s*:") {
            $out.Add($entry); $done = $true
            continue
        }
        $out.Add($line)
    }
    if ($inSection -and -not $done) { $out.Add($entry); $done = $true }
    if (-not $done) { $out.Add("${Section}:"); $out.Add($entry) }
    Write-TextLines $File $out
}

function Get-YamlSectionKey {
    param([string]$File, [string]$Section, [string]$Key)
    $inSection = $false
    foreach ($line in @(Get-Content -LiteralPath $File)) {
        if ($line -match '^[A-Za-z_][A-Za-z0-9_]*\s*:') {
            $inSection = ($line -match "^$Section\s*:")
        }
        elseif ($inSection -and $line -match "^\s+$Key\s*:\s*(.*)$") {
            # Drop any trailing "# comment" so a commented-but-empty key
            # (owner_chat_id: # ONLY this chat...) reads as unset.
            return ($Matches[1] -replace '(^|\s)#.*$', '').Trim()
        }
    }
    return ''
}

# ------------------------------------------------------------ 0. where ----

# $Path is the repository (what git clones and updates); $Backend is the
# backend folder inside it, where the venv, .env and config live. A backend
# folder handed over on its own has no repository around it: $Path is then
# empty and the git steps are skipped.
if ($Path) {
    # .NET file writes resolve against their own working directory, so make
    # sure everything downstream is an absolute path.
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        $Path = Join-Path (Get-Location).Path $Path
    }
    $Backend = Join-Path $Path 'backend'
}
else {
    $here = Split-Path -Parent $PSScriptRoot          # backend\scripts -> backend
    if ($here -and (Test-Path -LiteralPath (Join-Path $here 'pyproject.toml'))) {
        $Backend = $here
        $parent = Split-Path -Parent $here
        $Path = if ($parent -and (Test-Path -LiteralPath (Join-Path $parent '.git'))) { $parent } else { '' }
    }
    else {
        $Path = Join-Path $HOME 'jarvis'
        $Backend = Join-Path $Path 'backend'
    }
}

Write-Host "Jarvis setup - $Backend" -ForegroundColor White

# ------------------------------------------------------ 1. prerequisites --

Write-Step 'Checking prerequisites'

if (-not (Test-Exe 'git')) { throw 'git is not installed. Get it from https://git-scm.com and re-run.' }

# Take the first name that actually reports 3.11+. Testing the name alone is
# not enough on Windows: python.exe may be the Store alias stub, which is on
# PATH but runs nothing. Note the -c argument carries no double quotes -
# Windows PowerShell 5.1 strips those before python sees them.
$python = $null
$pythonVersion = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    if (-not (Test-Exe $candidate)) { continue }
    $probe = @('-c', 'import sys; print(sys.version.split()[0])')
    $reported = (Get-NativeOutput $candidate $probe | Select-Object -First 1)
    if (-not $reported -or $reported -notmatch '^(\d+)\.(\d+)') { continue }
    if ([version]"$($Matches[1]).$($Matches[2])" -ge [version]'3.11') {
        $python = $candidate
        $pythonVersion = $reported
        break
    }
    if (-not $pythonVersion) { $pythonVersion = $reported }   # for the error below
}
if (-not $python) {
    if ($pythonVersion) {
        throw "Python $pythonVersion is installed, but Jarvis needs 3.11 or newer. Get it from https://python.org (tick 'Add Python to PATH') and re-run."
    }
    throw "Python 3.11+ is not installed. Get it from https://python.org (tick 'Add Python to PATH') and re-run."
}
Write-Good "git and Python $pythonVersion"

# --------------------------------------------------------- 2. get / pull --

if (-not $Path) {
    Write-Step 'A backend folder on its own - no repository to update, so git is skipped'
}
elseif (-not (Test-Path -LiteralPath (Join-Path $Path '.git'))) {
    Write-Step "Cloning Jarvis into $Path"
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Invoke-Native git @('clone', '--branch', $Branch, $Repo, $Path)
}
elseif ($NoUpdate) {
    Write-Step 'Skipping the update (-NoUpdate)'
}
else {
    Write-Step "Updating to the latest $Branch"
    Push-Location $Path
    try {
        $dirty = @(& git status --porcelain)
        if ($dirty.Count -gt 0) {
            Write-Warn "$($dirty.Count) local change(s) - stashing them first."
            Invoke-Native git @('stash', 'push', '-u', '-m', "jarvis.ps1 $(Get-Date -Format s)")
            $stashed = $true
        }
        else { $stashed = $false }

        Invoke-Native git @('fetch', 'origin', $Branch)
        Invoke-Native git @('checkout', $Branch)
        Invoke-Native git @('pull', '--ff-only', 'origin', $Branch)

        if ($stashed) {
            Write-Info 'Restoring your local changes.'
            Get-NativeExitCode git @('stash', 'pop') | Out-Null
        }
        Write-Good (& git log --oneline -1)
    }
    finally { Pop-Location }
}

# ---------------------------------------------------------- 3. the venv ---

$venvRoot = Join-Path $Backend '.venv'
$venvBin = if ($IsWin) { Join-Path $venvRoot 'Scripts' } else { Join-Path $venvRoot 'bin' }
$venvPy = if ($IsWin) { Join-Path $venvBin 'python.exe' } else { Join-Path $venvBin 'python' }

if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Step 'Creating the virtual environment'
    Invoke-Native $python @('-m', 'venv', $venvRoot)
}
if (-not (Test-Path -LiteralPath $venvPy)) { throw "The virtual environment is missing its interpreter: $venvPy" }

# Anything running out of the venv holds its files open, and Windows will not
# let pip replace jarvis.exe while it does.
function Get-VenvProcess {
    $ErrorActionPreference = 'Continue'
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $exe = $null
            try { $exe = $_.Path } catch { $exe = $null }
            $exe -and $exe.StartsWith($venvRoot, [StringComparison]::OrdinalIgnoreCase)
        })
}

$jarvisBootstrap = 'from jarvis.app.cli import main; raise SystemExit(main())'

# Run the jarvis CLI: the console script when pip installed it, otherwise
# the same entry point through the venv's python. Returns its exit code.
function Invoke-Jarvis {
    param([string[]] $Arguments)
    $exe = if ($IsWin) { Join-Path $venvBin 'jarvis.exe' } else { Join-Path $venvBin 'jarvis' }
    if (Test-Path -LiteralPath $exe) {
        return Get-NativeExitCode $exe $Arguments
    }
    return Get-NativeExitCode $venvPy (@('-c', $jarvisBootstrap) + $Arguments)
}

# ------------------------------------------------------ 4. the packs ------

# -Skills accepts an array or one comma-separated string, so both
#   .\jarvis.ps1 -Skills browser,vision
#   powershell -File .\scripts\jarvis.ps1 -Skills "browser,vision"
# mean the same thing.
$knownPacks = @('all', 'core', 'browser', 'desktop', 'vision', 'api', 'phone', 'dash', 'voice', 'listen', 'dev', 'llm')
$requested = @()
foreach ($item in $Skills) {
    foreach ($part in ($item -split '[,;\s]+')) {
        if ($part) { $requested += $part.ToLowerInvariant() }
    }
}
if ($requested.Count -eq 0) { $requested = @('all') }
$unknown = @($requested | Where-Object { $knownPacks -notcontains $_ })
if ($unknown.Count -gt 0) {
    throw "Unknown skill pack(s): $($unknown -join ', '). Valid: $($knownPacks -join ', ')"
}

# Not in 'all', on purpose: 'llm' installs the anthropic package, which is
# only for running paid Claude instead of the default Gemini, and 'listen' is
# local speech recognition - a large download most people can skip. The
# default provider needs nothing beyond the base install.
$wanted = [System.Collections.Generic.HashSet[string]]::new()
if ($requested -contains 'all') {
    foreach ($e in @('api', 'phone', 'dash', 'voice', 'browser', 'desktop', 'vision')) { [void]$wanted.Add($e) }
}
foreach ($s in $requested) {
    if ($s -notin @('all', 'core')) { [void]$wanted.Add($s) }
}
# The API, dashboard and Telegram are how you actually reach Jarvis; keep
# whichever the chosen start needs.
if ($Start -eq 'phone') { [void]$wanted.Add('phone') }
if ($Start -in @('serve', 'dash')) { [void]$wanted.Add('api'); [void]$wanted.Add('dash') }
$extras = (@($wanted) | Sort-Object) -join ','
# With no extras at all the core install is still a working Jarvis.
$pipTarget = if ($extras) { ".[$extras]" } else { '.' }

# pip rewrites jarvis.exe on every install, so re-running it needlessly is
# what turns "Jarvis is already running" into a failed setup. Skip the install
# when neither the requested packs nor pyproject.toml have changed since the
# last successful one; -Reinstall forces it.
$stampFile = Join-Path $venvRoot '.jarvis-install-stamp'
$stamp = "$extras|" + (Get-FileHash (Join-Path $Backend 'pyproject.toml') -Algorithm SHA256).Hash
$upToDate = (Test-Path -LiteralPath $stampFile) -and
            ((Get-Content -LiteralPath $stampFile -Raw).Trim() -eq $stamp)

if ($upToDate -and -not $Reinstall) {
    Write-Step ("Already installed: " + $(if ($extras) { $extras } else { 'core only' }))
    Write-Info 'Nothing changed since the last install, so pip is skipped. Force it with -Reinstall.'
}
else {
    Write-Step ("Installing: " + $(if ($extras) { $extras } else { 'core only' }))

    # Stop a Jarvis that is already running from this venv, or pip cannot
    # replace its files. It is this script's own app, and a new one starts at
    # the end, but the running one may be mid-task - so ask first.
    $running = @(Get-VenvProcess)
    if ($running.Count -gt 0) {
        $names = ($running | Select-Object -ExpandProperty Name -Unique) -join ', '
        Write-Warn "Jarvis is already running from this folder ($names) and its files are locked."
        $stop = $false
        if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
            $answer = Read-Host '    Stop it and continue? A fresh one starts when setup finishes. [Y/n]'
            $stop = ($answer.Trim() -eq '') -or ($answer.Trim().ToLowerInvariant() -in @('y', 'yes'))
        }
        if (-not $stop) {
            throw "Stop the running Jarvis (close its window, or Ctrl-C it) and run this again. Nothing was changed."
        }
        foreach ($proc in $running) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2      # let Windows release the file handles
        Write-Info "Stopped $names."
    }

    Push-Location $Backend
    try {
        Get-NativeExitCode $venvPy @('-m', 'pip', 'install', '--upgrade', '--quiet', 'pip') | Out-Null
        $pip = Invoke-NativeCapture $venvPy @('-m', 'pip', 'install', '--quiet', '-e', $pipTarget)
        if ($pip.Code -ne 0) {
            foreach ($line in $pip.Output) { Write-Host "    $line" -ForegroundColor DarkGray }
            # The lock is worth naming: pip has to replace jarvis.exe, which
            # Windows refuses while a Jarvis started earlier is still running.
            if (($pip.Output -join "`n") -match 'being used by another process|WinError 32|Access is denied') {
                throw 'pip could not replace files in .venv because Jarvis is still running from it. Close that window (or Ctrl-C it) and run this again.'
            }
            throw "pip install -e `"$pipTarget`" failed with exit code $($pip.Code)."
        }
        Write-Good "pip install -e `"$pipTarget`" done"
        Set-Content -LiteralPath $stampFile -Value $stamp
    }
    finally { Pop-Location }
}

if ($wanted.Contains('browser')) {
    Write-Step 'Browser pack: downloading Chromium'
    $code = Get-NativeExitCode $venvPy @('-m', 'playwright', 'install', 'chromium')
    if ($code -eq 0) { Write-Good 'Chromium ready' }
    else { Write-Warn "Chromium download failed (exit $code). Re-run later: .venv\Scripts\python -m playwright install chromium" }
}

if ($wanted.Contains('desktop')) {
    Write-Step 'Desktop pack: checking it can load'
    $code = Get-NativeExitCode $venvPy @('-c', 'import pyautogui, pygetwindow') -Quiet
    if ($code -eq 0) { Write-Good 'pyautogui and pygetwindow load' }
    elseif ($IsWin) { Write-Warn 'pyautogui will not load - it needs a real desktop session (not a remote/headless shell). The 8 desktop skills stay off until it does.' }
    else { Write-Warn 'The desktop pack is Windows-only; its 8 skills will not appear here.' }
}

if ($wanted.Contains('vision')) {
    Write-Step 'Vision pack: the Tesseract OCR binary'
    if (Test-Exe 'tesseract') {
        Write-Good ((Get-NativeOutput 'tesseract' @('--version') | Select-Object -First 1))
    }
    elseif ($NoTesseract) {
        Write-Warn 'Skipped (-NoTesseract). capture_screen works; read_screen_text and locate_text_on_screen do not.'
    }
    else {
        if (Test-Exe 'winget') {
            Write-Info 'Installing UB-Mannheim.TesseractOCR via winget...'
            $code = Get-NativeExitCode winget @(
                'install', '--id', 'UB-Mannheim.TesseractOCR', '--exact',
                '--accept-package-agreements', '--accept-source-agreements'
            )
            if ($code -ne 0) { Write-Warn "winget could not install it (exit $code). Run ``winget search tesseract`` to find the right id, or use the installer below." }
        }
        else { Write-Warn 'winget is not available on this machine.' }

        # Jarvis calls tesseract from PATH only, so make sure it is there.
        # winget installs machine-wide or per-user depending on the package.
        if (-not (Test-Exe 'tesseract')) {
            # Built from environment variables, so this does not assume C:.
            $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
            $candidates = @()
            foreach ($root in $roots) {
                if ($root) { $candidates += (Join-Path $root 'Tesseract-OCR') }
            }
            if ($env:LOCALAPPDATA) {
                $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR')
            }
            $tessDir = $candidates |
                Where-Object { Test-Path -LiteralPath (Join-Path $_ 'tesseract.exe') -ErrorAction SilentlyContinue } |
                Select-Object -First 1
            if ($tessDir) {
                Write-Info "Adding $tessDir to your user PATH."
                $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
                if (-not $userPath) { $userPath = '' }
                if ($userPath -notlike "*$tessDir*") {
                    $newPath = if ($userPath) { "$userPath;$tessDir" } else { $tessDir }
                    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
                }
                $env:Path = "$env:Path;$tessDir"
            }
        }
        if (Test-Exe 'tesseract') { Write-Good 'Tesseract ready' }
        else {
            Write-Warn 'Tesseract is still missing. Install it from https://github.com/UB-Mannheim/tesseract/wiki (tick "Add to PATH"), then open a new PowerShell.'
            Write-Warn 'Until then capture_screen works, but read_screen_text and locate_text_on_screen do not.'
        }
    }
}

# ------------------------------------------------------ 5. configuration --

Write-Step 'Configuration'

$envFile = Join-Path $Backend '.env'
$cfgDir = Join-Path $Backend 'config'
$cfgFile = Join-Path $cfgDir 'config.yaml'

# Before the backend/ split these lived at the top of the repository. Carry
# them over once, so an update does not lose a working setup.
if ($Path) {
    foreach ($pair in @(@('.env', $envFile), @('config\config.yaml', $cfgFile))) {
        $old = Join-Path $Path $pair[0]
        if ((Test-Path -LiteralPath $old) -and -not (Test-Path -LiteralPath $pair[1])) {
            Copy-Item -LiteralPath $old -Destination $pair[1]
            Write-Info "Moved your $($pair[0]) into backend\ (the old copy can be deleted)"
        }
    }
}

if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item (Join-Path $Backend '.env.example') $envFile
    Write-Info 'Created backend\.env'
}
if (-not (Test-Path -LiteralPath $cfgFile)) {
    Copy-Item (Join-Path $cfgDir 'config.example.yaml') $cfgFile
    Write-Info 'Created backend\config\config.yaml'
}

# The API token is machine-generated; never make the user invent one.
if (-not (Get-EnvValue $envFile 'JARVIS_API_TOKEN')) {
    $token = (& $venvPy -c 'import secrets; print(secrets.token_urlsafe(48))').Trim()
    Set-EnvValue $envFile 'JARVIS_API_TOKEN' $token
    Write-Good 'Generated JARVIS_API_TOKEN'
}

# The workspace is the only folder Jarvis may touch.
if (-not (Get-YamlSectionKey $cfgFile 'files' 'root')) {
    $workspace = Join-Path $HOME 'JarvisWorkspace'
    New-Item -ItemType Directory -Path $workspace -Force | Out-Null
    Set-YamlSectionKey $cfgFile 'files' 'root' ($workspace -replace '\\', '/')
    Write-Good "Workspace set to $workspace"
}
else {
    $existing = Get-YamlSectionKey $cfgFile 'files' 'root'
    if (-not (Test-Path -LiteralPath $existing)) { New-Item -ItemType Directory -Path $existing -Force | Out-Null }
    Write-Info "Workspace: $existing"
}

# Telegram: offer it, and only switch it on once there is a bot token.
$botToken = Get-EnvValue $envFile 'TELEGRAM_BOT_TOKEN'
if (-not $botToken -and $Start -eq 'phone') {
    Write-Info 'Telegram bot token (from @BotFather in Telegram). Input is hidden; Enter skips.'
    $botToken = Read-Secret 'TELEGRAM_BOT_TOKEN'
    if ($botToken) { Set-EnvValue $envFile 'TELEGRAM_BOT_TOKEN' $botToken }
}
if ($botToken) {
    if ((Get-YamlSectionKey $cfgFile 'telegram' 'enabled') -ne 'true') {
        Set-YamlSectionKey $cfgFile 'telegram' 'enabled' 'true'
        Write-Good 'Telegram bridge enabled in config.yaml'
    }
    if (-not (Get-YamlSectionKey $cfgFile 'telegram' 'owner_chat_id')) {
        Write-Warn 'owner_chat_id is not set yet - anyone who finds your bot could command it.'
        Write-Warn "Message your bot /whoami, then put the id it replies with into $cfgFile and re-run."
    }
}

# ------------------------------------------------------------ 6. the model --

Write-Step 'The model'

function Invoke-BrainCheck {
    $exe = if ($IsWin) { Join-Path $venvBin 'jarvis.exe' } else { Join-Path $venvBin 'jarvis' }
    if (Test-Path -LiteralPath $exe) { return Invoke-NativeCapture $exe @('brain') }
    return Invoke-NativeCapture $venvPy @('-c', $jarvisBootstrap, 'brain')
}

# Gemini is the default provider, and a free key is all it needs. Ask for it
# here rather than letting the first task fail on it.
$provider = Get-EnvValue $envFile 'LLM_PROVIDER'
if (-not $provider) { $provider = 'gemini' }
if ($provider -eq 'gemini' -and -not (Get-EnvValue $envFile 'GEMINI_API_KEY')) {
    Write-Info 'Jarvis thinks with Google Gemini. A free API key is enough:'
    Write-Info '  1. open https://aistudio.google.com/apikey and sign in with a Google account'
    Write-Info '  2. click "Create API key" and copy it'
    Write-Info 'Paste it here (input is hidden; Enter skips):'
    $geminiKey = Read-Secret 'GEMINI_API_KEY'
    if ($geminiKey) {
        Set-EnvValue $envFile 'GEMINI_API_KEY' $geminiKey
        Write-Good 'Saved GEMINI_API_KEY to .env'
    }
}

# `jarvis brain` is the authority here: it applies the config defaults, prints
# which provider and model are configured, and checks the key is accepted -
# by looking the model up, which spends none of the free tier's quota. Its
# exit code is the verdict.
Push-Location $Backend
try {
    $brain = Invoke-BrainCheck
    foreach ($line in $brain.Output) { Write-Info $line }
    foreach ($line in $brain.Output) {
        if ($line -match '^\s*provider:\s*(\S+)') { $provider = $Matches[1] }
    }
}
finally { Pop-Location }

# A rejected config key prints as a pydantic dump that buries the useful
# part. The field path is on the line above the message, so name it plainly.
if (($brain.Output -join "`n") -match 'Extra inputs are not permitted') {
    $badKeys = @()
    for ($i = 1; $i -lt $brain.Output.Count; $i++) {
        if ($brain.Output[$i] -match 'Extra inputs are not permitted') {
            $badKeys += $brain.Output[$i - 1].Trim()
        }
    }
    Write-Warn "config.yaml has setting(s) Jarvis does not recognise: $($badKeys -join ', ')"
    Write-Warn "Delete or comment out those lines, then run this again:  notepad $cfgFile"
}

$brainReady = ($brain.Code -eq 0)
if ($brainReady) { Write-Good "$provider is ready" }
else { Write-Warn 'The model is not usable yet - the lines above say why. Re-check any time with:  jarvis brain' }

# --------------------------------------------------- 7. what's installed --

Write-Step 'Skills available on this machine'

# From $Backend, so config/config.yaml is found the same way every other
# command finds it - run from elsewhere, discovery silently falls back to the
# coded defaults and reports a state that is not the real one.
Push-Location $Backend
try {
    $exe = if ($IsWin) { Join-Path $venvBin 'jarvis.exe' } else { Join-Path $venvBin 'jarvis' }
    if (Test-Path -LiteralPath $exe) { $tools = @(Get-NativeOutput $exe @('tools')) }
    else { $tools = @(Get-NativeOutput $venvPy @('-c', $jarvisBootstrap, 'tools')) }
}
finally { Pop-Location }
if ($tools.Count -gt 0) {
    $names = $tools | ForEach-Object { ($_ -split '\s+')[0] }
    $packs = [ordered]@{
        'files + memory' = @{ probe = 'read_file'; count = 15 }
        'browser'        = @{ probe = 'browser_open'; count = 9 }
        'desktop'        = @{ probe = 'open_application'; count = 8 }
        'vision'         = @{ probe = 'capture_screen'; count = 3 }
    }
    foreach ($name in $packs.Keys) {
        $pack = $packs[$name]
        if ($names -contains $pack.probe) { Write-Good ("{0,-15} {1} skills" -f $name, $pack.count) }
        else { Write-Info ("{0,-15} not installed" -f $name) }
    }
    Write-Host ("    {0,-15} {1} skills total" -f '', $tools.Count) -ForegroundColor White

    # The browser skills register as soon as playwright imports, but they only
    # work once the Chromium binary is actually on disk. Say so if it isn't.
    if ($names -contains 'browser_open') {
        $probe = 'import os, sys; from playwright.sync_api import sync_playwright; ' +
        'p = sync_playwright().start(); ' +
        'sys.exit(0 if os.path.exists(p.chromium.executable_path) else 3)'
        if ((Get-NativeExitCode $venvPy @('-c', $probe) -Quiet) -ne 0) {
            Write-Warn 'The 9 browser skills are registered but Chromium is missing - they will fail until you run:'
            Write-Warn '  .venv\Scripts\python -m playwright install chromium'
        }
    }
    # Same story for OCR: two of the three vision skills need the binary.
    if (($names -contains 'capture_screen') -and -not (Test-Exe 'tesseract')) {
        Write-Warn 'capture_screen works, but read_screen_text and locate_text_on_screen need Tesseract on PATH.'
    }
}
else {
    Write-Warn 'Could not list the skills. If a config key was rejected above, fix that first.'
    Write-Warn 'Otherwise run this to see the error:  jarvis tools'
}

# ------------------------------------------------------------ 8. start ----

Write-Step 'Ready'
Write-Info "Re-run any time - this script is safe to repeat:  .\backend\scripts\jarvis.ps1"
Write-Info "Everything else you can type is in docs\COMMANDS.md"

if (-not $brainReady -and $Start -ne 'none') {
    Write-Warn 'Not starting: Jarvis has no working model yet. Fix the above, then run this again.'
    exit 1
}

Push-Location $Backend
try {
    switch ($Start) {
        'dash' {
            Write-Host "`nStarting Jarvis and opening the dashboard. Ctrl-C to stop.`n" -ForegroundColor White
            Invoke-Jarvis @('dash') | Out-Null
        }
        'phone' {
            Write-Host "`nStarting the Telegram bridge. Ctrl-C to stop.`n" -ForegroundColor White
            Invoke-Jarvis @('phone') | Out-Null
        }
        'serve' {
            Write-Host "`nStarting the API server on http://127.0.0.1:8765 - Ctrl-C to stop.`n" -ForegroundColor White
            Invoke-Jarvis @('serve') | Out-Null
        }
        'run' {
            if (-not $Request) { throw 'Pass the task text too: -Start run -Request "what to do"' }
            Write-Host ''
            $script:taskExit = Invoke-Jarvis @('run', $Request)
        }
        'none' {
            Write-Info "Start it yourself from $Backend with:  .venv\Scripts\activate  then  jarvis dash"
        }
    }
}
finally { Pop-Location }

# A failed task should fail the script too, so -Start run is scriptable.
if (Test-Path variable:script:taskExit) { exit $script:taskExit }
