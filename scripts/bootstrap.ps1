# Jarvis - everything, from a blank Windows PC to running, in one paste.
#
# The contents of this file are also the copy-paste block in README.md: paste
# it into PowerShell on any Windows machine and it installs Git and Python if
# they are missing, clones Jarvis, then hands off to scripts/jarvis.ps1, which
# installs every skill pack, writes the config, asks for your API key, and
# starts the Telegram bridge.
#
# Safe to run every time - nothing already done is done twice.

& {
    # 'Continue', not 'Stop': Windows PowerShell 5.1 turns anything a native
    # command writes to stderr into a terminating error under 'Stop', and git,
    # winget and python all use stderr routinely. Exit codes are checked below.
    $ErrorActionPreference = 'Continue'
    $Repo = 'https://github.com/nicolaasvzz/Jarvis.'
    $Branch = 'claude/jarvis-startup-skills-commands-ilp298'
    $Dir = Join-Path $HOME 'jarvis'

    function Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
    function Refresh {
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User')
    }
    function PyOk {
        # The -c text carries no double quotes on purpose: Windows PowerShell
        # 5.1 strips those before python sees them. The exit code is the
        # answer, so nothing has to be parsed either.
        foreach ($py in @('python', 'py', 'python3')) {
            if (-not (Have $py)) { continue }
            & $py -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $true }
        }
        return $false
    }
    function Stop-With($message) { Write-Host "`n$message" -ForegroundColor Yellow }

    if (-not (Have 'winget') -and (-not (Have 'git') -or -not (PyOk))) {
        Stop-With 'This PC is missing Git or Python 3.11+, and winget is not here to install them. Get Git from https://git-scm.com and Python from https://python.org (tick "Add Python to PATH"), then paste this again.'
        return
    }
    if (-not (Have 'git')) {
        Write-Host 'Installing Git...' -ForegroundColor Cyan
        winget install --id Git.Git --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Stop-With "winget could not install Git (exit $LASTEXITCODE). Install it from https://git-scm.com and paste this again."; return }
        Refresh
    }
    if (-not (PyOk)) {
        Write-Host 'Installing Python 3.12...' -ForegroundColor Cyan
        winget install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Stop-With "winget could not install Python (exit $LASTEXITCODE). Install 3.11+ from https://python.org, tick `"Add Python to PATH`", and paste this again."; return }
        Refresh
    }
    if (-not (Have 'git') -or -not (PyOk)) {
        Stop-With 'Git and Python are installed but not on this window''s PATH yet. Close this window, open a new PowerShell, and paste this again.'
        return
    }
    if (-not (Test-Path (Join-Path $Dir '.git'))) {
        if ((Test-Path $Dir) -and @(Get-ChildItem -LiteralPath $Dir -Force).Count -gt 0) {
            Stop-With "$Dir already exists and is not a Jarvis clone. Rename or delete that folder, then paste this again."
            return
        }
        Write-Host "Cloning Jarvis into $Dir ..." -ForegroundColor Cyan
        git clone --branch $Branch $Repo $Dir
        if ($LASTEXITCODE -ne 0) { Stop-With 'Clone failed - check the GitHub sign-in prompt, or your internet connection.'; return }
    }

    # Always bring the clone to origin's tip before handing off, even when the
    # script is already there: a stale copy of it is just as unusable as a
    # missing one, and it cannot update itself if it will not run. This also
    # covers a clone on an old branch, on a detached HEAD, or half-checked-out.
    # Done in place with git on purpose - another window sitting inside the
    # folder locks it against being renamed, but not against git rewriting it.
    $Script = Join-Path $Dir 'scripts\jarvis.ps1'
    Write-Host "Updating $Dir to $Branch ..." -ForegroundColor Cyan
    git -C $Dir fetch origin
    if ($LASTEXITCODE -ne 0) { Stop-With 'Could not reach the repository - check the GitHub sign-in prompt, or your internet connection.'; return }
    if (@(git -C $Dir status --porcelain).Count -gt 0) {
        Write-Host 'Stashing your local changes first (git stash pop brings them back).' -ForegroundColor Gray
        git -C $Dir stash push -u -m 'jarvis bootstrap'
    }
    git -C $Dir checkout -B $Branch "origin/$Branch"
    if ($LASTEXITCODE -ne 0) { Stop-With "git could not switch $Dir to $Branch."; return }

    # Last resort: move the folder aside and clone fresh, keeping the two files
    # worth keeping. Verified rather than assumed - the move is what fails when
    # another process holds the folder.
    if (-not (Test-Path $Script)) {
        $Old = "$Dir-old-$(Get-Date -Format yyyyMMdd-HHmmss)"
        Move-Item -LiteralPath $Dir -Destination $Old -ErrorAction SilentlyContinue
        if (Test-Path $Dir) {
            Stop-With "Could not move $Dir aside - another program is holding it open. Close any PowerShell, Explorer or editor window sitting in that folder, then paste this again."
            $holders = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) })
            if ($holders.Count -gt 0) { Write-Host ('Running from that folder: ' + (($holders | Select-Object -ExpandProperty Name -Unique) -join ', ')) -ForegroundColor Yellow }
            return
        }
        Write-Host "Moved the old folder to $Old" -ForegroundColor Gray
        Write-Host "Cloning Jarvis into $Dir ..." -ForegroundColor Cyan
        git clone --branch $Branch $Repo $Dir
        if ($LASTEXITCODE -ne 0) { Stop-With "Clone failed. Your old folder is still at $Old"; return }
        foreach ($keep in @('.env', 'config\config.yaml')) {
            $from = Join-Path $Old $keep
            if (Test-Path $from) {
                Copy-Item $from (Join-Path $Dir $keep) -Force
                Write-Host "Kept your $keep" -ForegroundColor Gray
            }
        }
    }
    if (-not (Test-Path $Script)) {
        Stop-With "$Script is still missing. Check what this prints: git -C $Dir log --oneline -1"
        return
    }

    $shell = if (Have 'powershell') { 'powershell' } else { 'pwsh' }
    & $shell -ExecutionPolicy Bypass -File $Script -Skills all
}
