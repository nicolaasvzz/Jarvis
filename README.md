# Jarvis

A personal AI assistant that runs on your Windows PC, takes instructions in
plain language — typed, spoken, or sent from your phone — **plans before it
acts**, and controls the computer safely: it asks for your confirmation
before anything dangerous and tells you what it did.

```
phone (Telegram) ─┐
browser ─▶ frontend ──HTTP+token──▶ backend: API ──▶ Brain (Gemini) ──▶ Planner
     ▲                                  │                                  │
     └──── live events ──── Event Bus ── Agent Pool ──▶ Tool Manager ──▶ permission policy
                                        (N agents)          │           (confirm dangerous)
                                  files · memory · browser · desktop · vision · voice
```

The thinking is done by **Google Gemini**, and a **free API key** is all you
need — no credit card, no local GPU. Claude is available as an optional
paid alternative.

## What's in this repository

| Folder | What it is | Give it to someone who… |
|---|---|---|
| [`backend/`](backend/) | The assistant itself (Python): planner, agents, tools, the HTTP API, phone bridge, voice — plus its docs in [`backend/docs/`](backend/docs/) | …wants their own Jarvis, with or without a web UI |
| [`frontend/`](frontend/) | The web dashboard (plain HTML/JS, no build step): live core, file constellation, agent office, classic HUD | …wants a UI for a Jarvis running somewhere, or a dashboard for **their own project** — see [frontend/API.md](frontend/API.md) |

The two halves only talk over HTTP, so each works without the other:

- **Backend alone** runs as an API, a terminal tool (`jarvis run "…"`), and a
  Telegram bot. If a `frontend/` folder sits next to it, it also serves the
  dashboard at `http://127.0.0.1:8765/dash/`.
- **Frontend alone** is a folder of static files. Open it with
  `start.bat` (or any static web server), type in the address of a running
  backend and its token, and it connects. See
  [frontend/README.md](frontend/README.md).

## Start here (Windows, one paste)

Open **PowerShell** and paste this whole block. It installs Git and Python
if they are missing, downloads Jarvis into `~\jarvis`, installs everything,
asks for your free Gemini key, and opens the dashboard. Paste it again any
time — it updates and picks up where it left off.

```powershell
& {
    # 'Continue', not 'Stop': Windows PowerShell 5.1 turns anything a native
    # command writes to stderr into a terminating error under 'Stop', and git,
    # winget and python all use stderr routinely. Exit codes are checked below.
    $ErrorActionPreference = 'Continue'
    $Repo = 'https://github.com/nicolaasvzz/Jarvis.'
    $Branch = 'main'
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
    $Script = Join-Path $Dir 'backend\scripts\jarvis.ps1'
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
        # Settings may sit in backend\ or, from before the split, at the top.
        foreach ($keep in @('.env', 'config\config.yaml')) {
            foreach ($from in @((Join-Path $Old "backend\$keep"), (Join-Path $Old $keep))) {
                if (Test-Path $from) {
                    Copy-Item $from (Join-Path $Dir "backend\$keep") -Force
                    Write-Host "Kept your $keep" -ForegroundColor Gray
                    break
                }
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
```

The same block lives in
[backend/scripts/bootstrap.ps1](backend/scripts/bootstrap.ps1).

### Getting the free Gemini key

1. Go to **https://aistudio.google.com/apikey** and sign in with any Google
   account.
2. Click **Create API key**, and copy it.
3. Paste it when the setup asks — or put it in `backend\.env` yourself as
   `GEMINI_API_KEY=...`.

The free tier has per-minute and per-day request limits. Jarvis waits and
retries when it hits the per-minute one; if you hit limits often, lower
`agent.pool_size` in `backend\config\config.yaml` so fewer requests go out
at once.

## Manual setup (any OS)

Needs Python 3.11+.

```bash
git clone https://github.com/nicolaasvzz/Jarvis. jarvis
cd jarvis/backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[api,dash,voice,phone]"

copy .env.example .env            # macOS/Linux: cp .env.example .env
jarvis token                      # paste the output into .env as JARVIS_API_TOKEN
notepad .env                      # and paste your GEMINI_API_KEY
jarvis brain                      # checks the key and the model
jarvis dash                       # starts everything and opens the dashboard
```

Run `jarvis` commands from inside `backend/`: that is where it finds `.env`
and `config/config.yaml`. The full install guide, every optional pack, and
all configuration are in [backend/README.md](backend/README.md).

## Sharing just one half

Everything is on GitHub, so the simplest way is to send the link — they can
use **Code → Download ZIP** and keep only the folder they need. To hand over
a single folder yourself, zip it from git, from inside the repository:

```powershell
git archive --format=zip -o jarvis-backend.zip  HEAD backend
git archive --format=zip -o jarvis-frontend.zip HEAD frontend
```

`git archive` packs only committed files, so your `.env` (API keys),
`config.yaml`, `.venv` and workspace never end up in the zip. Zipping the
folder with Explorer or `Compress-Archive` would include them.

**Letting someone else's frontend use your backend** takes two settings in
`backend\config\config.yaml` — listen on the network, and allow their page —
plus your API token, which they type into the connect screen:

```yaml
api:
  host: 0.0.0.0                        # accept connections from other machines
  cors_origins:
    - http://192.168.1.20:8080         # the address their frontend is opened at
```

Anyone with the token can make Jarvis act on your computer, so only share it
with people you trust, and keep the API off the public internet (use a VPN
such as Tailscale for remote access).

## What it can do

- **Understands plain language** — answers questions directly, or breaks a
  request into a risk-tagged plan before touching anything.
- **Works on several things at once** — independent steps of a plan run in
  parallel on different agents; dependent ones stay in order.
- **Recovers from failure** — failing steps are retried, then replanned
  ("try another approach"), then reported — never crashed on.
- **Files** — read, write, move, search, organize, zip, inside a sandboxed
  folder it cannot escape; deleting asks you first.
- **Memory** — remembers conversations, preferences and every task.
- **Browser, desktop and screen** (optional packs) — browse and fill forms,
  open apps and type, read the screen and click what it finds.
- **Voice** — answers in a British neural voice and listens for "Jarvis";
  speech recognition runs locally.
- **Dashboard** — a particle core that reacts to what Jarvis is doing, your
  files as a constellation that lights up as they're touched, and a pixel
  office where each agent walks to its work.
- **Phone control** — a Telegram bot: send tasks, get notified, tap to
  approve or deny dangerous actions. Outbound-only, so no open ports.

## More

- [backend/docs/COMMANDS.md](backend/docs/COMMANDS.md) — every command, skill, and API route
- [backend/docs/SETUP_FOR_A_FRIEND.md](backend/docs/SETUP_FOR_A_FRIEND.md) — a step-by-step
  guide to send to someone setting up their own Jarvis
- [backend/docs/ARCHITECTURE.md](backend/docs/ARCHITECTURE.md) — design rules and decisions
- [backend/README.md](backend/README.md) · [frontend/README.md](frontend/README.md) · [frontend/API.md](frontend/API.md)
