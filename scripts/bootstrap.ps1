# Jarvis — everything, from a blank Windows PC to running, in one paste.
#
# The contents of this file are also the copy-paste block in README.md: paste
# it into PowerShell on any Windows machine and it installs Git and Python if
# they are missing, clones Jarvis, then hands off to scripts/jarvis.ps1, which
# installs every skill pack, writes the config, asks for your API key, and
# starts the Telegram bridge.
#
# Safe to run every time — nothing already done is done twice.

& {
    $ErrorActionPreference = 'Stop'
    $Repo = 'https://github.com/nicolaasvzz/Jarvis.'
    $Branch = 'claude/jarvis-startup-skills-commands-ilp298'
    $Dir = Join-Path $HOME 'jarvis'

    function Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
    function Refresh {
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User')
    }
    function PyOk {
        foreach ($py in @('python', 'py', 'python3')) {
            if (-not (Have $py)) { continue }
            $v = (& $py -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $v -and [version]$v -ge [version]'3.11') { return $true }
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
        Write-Host "Cloning Jarvis into $Dir ..." -ForegroundColor Cyan
        git clone --branch $Branch $Repo $Dir
        if ($LASTEXITCODE -ne 0) { Stop-With 'Clone failed — check the GitHub sign-in prompt, or your internet connection.'; return }
    }

    $shell = if (Have 'powershell') { 'powershell' } else { 'pwsh' }
    & $shell -ExecutionPolicy Bypass -File (Join-Path $Dir 'scripts\jarvis.ps1') -Skills all
}
