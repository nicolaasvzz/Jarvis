# Jarvis

A personal AI desktop assistant that runs continuously on your Windows
machine, receives natural-language instructions from your phone, **plans
before it acts**, and controls the computer safely — asking for your
confirmation before dangerous actions and notifying you as work progresses.

```
phone ──HTTP+token──▶ API Server ──▶ Brain (local model) ──▶ Planner
browser ──▶ Dashboard ──┘                 │                 │
     ▲                                    │        dependency-graph plan
     └── live events ── Event Bus ── Agent Pool ──▶ Tool Manager ──▶ policy
                                    (N agents)           │      (confirm dangerous)
                     files · memory · browser · desktop · vision
```

## Start here

Open **PowerShell** on any Windows PC and paste this whole block. It installs
Git and Python if they're missing, clones Jarvis, installs every skill, writes
the config, asks for your API key, and starts. Paste it again any time you
want to start Jarvis — it updates and picks up where it left off.

```powershell
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
```

The model runs **on your own machine**, through
[Ollama](https://ollama.com): no API key, no account, and nothing you say
leaves the computer. The block installs Ollama if it isn't there, pulls the
model, and checks it answers. Claude stays available as an optional provider —
see [Choosing the model](#choosing-the-model).

The only thing it may ask you for is a **Telegram bot token** from @BotFather,
if you want phone control — typing is hidden and it's saved to `.env`, so
you're asked once per machine. The same block lives in
[scripts/bootstrap.ps1](scripts/bootstrap.ps1).

Everything else you can type — every command, skill, and API route — is in
[docs/COMMANDS.md](docs/COMMANDS.md).

## What it can do

- **Understand natural language** and answer questions directly, or break a
  request into a risk-tagged plan before touching anything.
- **Work on several things at once.** The plan is a dependency graph, not a
  queue: steps that genuinely depend on each other stay in order, while
  independent ones (reading four files, searching two folders) are handed to
  different agents and run together.
- **Observe every result**; failing steps are retried, then replanned ("try
  another approach"), then reported — never crashed on.
- **A live dashboard** (`[dash]` extra) at `http://127.0.0.1:8765/dash/` —
  a particle core that reacts to what Jarvis is doing, a constellation of
  your files that lights up as they are touched, and a pixel office where
  each agent walks between rooms and sits down to work. See
  [The dashboard](#the-dashboard).
- **A voice** (`[voice]` / `[listen]` extras): Jarvis answers in a British
  neural voice, and listens for a wake word. Speech recognition runs
  **locally** — recordings never leave the machine.
- **Files** (always available): read, write, list, move, copy, search,
  organize folders, zip/unzip — all inside a sandboxed workspace directory
  it cannot escape; deletion requires your approval.
- **Memory** (always available): remembers conversations, preferences,
  facts, and every task across restarts (SQLite).
- **Browser** (`[browser]` extra): open sites, read pages, click, fill
  forms, upload/download files, extract links, web search — one persistent
  session, so logins survive between steps.
- **Desktop** (`[desktop]` extra, Windows): open/close apps, focus and move
  windows, click, type, keyboard shortcuts.
- **Vision** (`[vision]` extra): screenshots, read screen text (OCR), and
  locate text on screen → coordinates the desktop tools can click, so no
  brittle hardcoded positions.
- **Phone control (Telegram)** (`[phone]` extra): run everything from a
  Telegram chat — send any task, get push notifications, approve/deny
  dangerous actions with tap buttons, check status, cancel. Works over
  **outbound HTTPS only**, so it needs no wifi/LAN, no open ports, and no
  exposed server — even a phone/USB tether is enough. See
  [Control it from your phone](#control-it-from-your-phone-telegram).
- **Phone control (HTTP API)** (`[api]` extra): the same actions over an
  authenticated local HTTP API + live server-sent-events feed, for a custom
  app or `curl` on the same network.
- **Push notifications** (`[phone]` extra): push-only alerts to an
  ntfy-compatible server (no bot, no account) if you don't want the full
  Telegram bridge.

## Install (on the Windows machine)

Requires Python 3.11+. One command does the whole thing — clone, virtual
environment, skill packs, config, secrets, and start — and is safe to re-run
every day:

```powershell
powershell -ExecutionPolicy Bypass -File ~\jarvis\scripts\jarvis.ps1 -Skills all
```

Or do it by hand:

First install [Ollama](https://ollama.com) and pull the model Jarvis uses
by default — one download, then it works offline:

```powershell
ollama pull gpt-oss:20b
```

Then:

```powershell
git clone <this repo> jarvis && cd jarvis
python -m venv .venv
.venv\Scripts\activate

# Core + API server (the local model needs nothing extra):
pip install -e ".[api]"

# The web dashboard, and a voice to go with it:
pip install -e ".[dash,voice,listen]"

# Phone control + push notifications (Telegram / ntfy):
pip install -e ".[phone]"

# Optional capability packs:
pip install -e ".[browser]"   ;  playwright install chromium
pip install -e ".[desktop]"
pip install -e ".[vision]"    # also install Tesseract OCR for screen reading

# Only if you want to use Claude instead of a local model:
pip install -e ".[llm]"
```

The voice extras are free and need no account: `voice` uses Microsoft's
neural voices over plain HTTPS, `listen` runs Whisper on your own machine.
`voice-offline` adds the built-in Windows voices for when there is no
internet.

Configure:

```powershell
copy config\config.example.yaml config\config.yaml   # optional, has defaults
copy .env.example .env
```

Edit `.env`:

```
LLM_PROVIDER=ollama                 # the default: a model on this machine
LLM_MODEL=gpt-oss:20b
JARVIS_API_TOKEN=<run: jarvis token>
```

## Choosing the model

`LLM_PROVIDER` picks which Brain implementation Jarvis builds. Both speak
the same internal interface, so the planner, tools, memory, phone bridge,
and everything else are identical either way.

| | `ollama` (default) | `anthropic` |
|---|---|---|
| Runs | on your machine | Anthropic's API |
| Default model | `gpt-oss:20b` | `claude-opus-4-8` |
| Needs a key | no | `ANTHROPIC_API_KEY` |
| Privacy | nothing leaves the machine | prompts sent to Anthropic |
| Install | nothing extra | `pip install -e ".[llm]"` |

Check the connection before doing anything else:

```powershell
jarvis brain
# provider: ollama
# model:    gpt-oss:20b
# endpoint: http://localhost:11434
# connected: yes — 3 model(s) downloaded
# model 'gpt-oss:20b' is available — Jarvis is ready.
```

To use a different local model, `ollama pull` it and set `LLM_MODEL` in
`.env` — `LLM_MODEL` overrides `config.yaml`, so it is the one place to
change it. The model must support tool calling, which Jarvis relies on.
`gpt-oss:120b` is the larger sibling of the default if the hardware allows.
To switch to Claude instead, set `LLM_PROVIDER=anthropic` and
`ANTHROPIC_API_KEY`.

Tuning for local models lives under `llm:` in `config/config.yaml`:
`timeout` (raise it on slower hardware), `context_window` (`num_ctx` — too
small silently truncates the plan), `temperature`, and `think` (gpt-oss
reasons before answering by default; `false` is faster).

## Run

Every day, either run the script — it pulls, checks the install, and starts
the bridge:

```powershell
powershell -ExecutionPolicy Bypass -File ~\jarvis\scripts\jarvis.ps1
```

…or do the same by hand:

```powershell
cd ~\jarvis
git pull --ff-only origin claude/jarvis-startup-skills-commands-ilp298
.venv\Scripts\activate
jarvis phone                  # Ctrl-C to stop
```

The rest of the commands:

```powershell
# Check the model connection:
jarvis brain

# See which tools are available on this machine:
jarvis tools

# One-off task from the terminal (approvals prompt with y/N):
jarvis run "organize the files in my workspace by extension"

# Everything at once — API, dashboard and Telegram bridge in one process —
# and open the dashboard in your browser:
jarvis dash

# The same without opening a browser:
jarvis serve            # add --host 0.0.0.0 to accept LAN connections
                        # add --no-dash for the API only

# Just the Telegram bridge:
jarvis phone
```

`serve` and `dash` run the API, the dashboard and the phone bridge on one
event loop **on purpose**: live agent state exists only in memory, so a
dashboard in a second process would build its own Jarvis and show you an
emptier one than the one actually doing the work.

## The dashboard

`jarvis dash` opens `http://127.0.0.1:8765/dash/`. Three views onto the same
live event stream:

- **Core** — a particle sphere that idles cyan, turns amber while Jarvis is
  thinking, pulses in time with its own voice while speaking, and flares red
  on failure. Around it: CPU/memory/battery, running tasks with per-step
  progress, the live activity feed, and approval buttons for dangerous
  actions. Type at the bottom, or talk to it.
- **Files** — your workspace as a radial constellation. When Jarvis reads,
  writes or deletes a file, a pulse travels from the centre out along the
  folder chain and the node flares: cyan for a read, green for a write, red
  for a delete. Scroll to zoom, click a node to inspect it.
- **Office** — the agent pool as pixel characters. Each agent walks to the
  room its current tool belongs to (files → Archives, browser → Web Wing,
  memory → Library, screen → Observatory), sits at a free desk, and shows a
  three-dot typing indicator while the tool runs. Independent steps put
  several agents to work side by side.

The dashboard uses the same API token as everything else. `jarvis dash` puts
it in the URL for you; after that it is remembered in the browser.

## Voice

```yaml
voice:
  enabled: true
  provider: edge              # free neural voices, no API key
  voice: en-GB-RyanNeural     # ThomasNeural = clipped, SoniaNeural = female
  wake_word: jarvis
  wake_word_required: true    # false = every utterance is a command
  stt_provider: whisper       # runs locally; audio never leaves the machine
  stt_model: small.en         # base.en is faster, medium.en needs more VRAM
```

Click the microphone once to arm it and it stays listening. It only sends a
clip when you have actually said something and then stopped, and only acts
on it if it began with the wake word — so ambient conversation is ignored.
Matching is deliberately forgiving of how recognisers mishear the name
("Travis", "Jervis") while still rejecting near-misses like "Marvin" and
"Harris".

Jarvis speaks the events listed in `voice.speak_events` (by default: task
finished, task failed, approval needed). Mute it from the Quick Access
panel without changing config.

> **On the GPU.** Transcription uses the GPU when it can, but CUDA's runtime
> libraries (cuBLAS, cuDNN) install separately from the graphics driver and
> are often missing — and the gap only shows up on the first transcription,
> not when the model loads. Jarvis notices, falls back to the CPU, logs it,
> and carries on; `small.en` on CPU is perfectly usable. For the GPU path,
> install the CUDA 12 runtime, or `pip install nvidia-cublas-cu12
> nvidia-cudnn-cu12`.

## Agents

```yaml
agent:
  parallel: true
  pool_size: 4      # up to 20
```

The Planner marks which steps depend on which; anything unblocked at the
same moment runs at the same time, up to `pool_size`. Steps that say nothing
about ordering are chained, because sequencing is the safe assumption — a
step that quietly needed an earlier one and ran too early gives a wrong
answer, whereas one needlessly serialised is merely slower. Dependencies may
only point at earlier steps, which makes a deadlock structurally impossible.

Every command, every skill, and every API route in one page:
[docs/COMMANDS.md](docs/COMMANDS.md).

## Control it from your phone (Telegram)

This is the easiest and most robust way to run Jarvis remotely — and the
one to use when **your laptop has no wifi**. The bridge only ever makes
*outbound* HTTPS calls to Telegram: it long-polls for your messages and
sends replies back. So there's **no LAN, no port-forwarding, no exposed
server** — it works over any internet the laptop has, including plugging it
into your phone's USB tether / mobile hotspot.

**One-time setup:**

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the bot
   token it gives you.
2. Put the token in `.env`:  `TELEGRAM_BOT_TOKEN=123456:ABC...`
3. Turn the bridge on in `config/config.yaml`:
   ```yaml
   telegram:
     enabled: true
   ```
4. Start it once and message your bot anything — it replies with your chat
   id:
   ```powershell
   jarvis phone
   ```
   Put that id in the config so only you can control Jarvis, then restart:
   ```yaml
   telegram:
     enabled: true
     owner_chat_id: 123456789
   ```

**Then, from the Telegram app, you can:**

- **Send any task** — just type it (“download my latest invoices and
  summarise them”). Jarvis plans and does it, messaging progress back.
- **Approve/deny dangerous actions** — when a step needs confirmation you
  get a message with **✅ Allow / ⛔ Deny** buttons; tap one.
- `/status` — recent tasks and their state · `/task <id>` — full detail
- `/cancel <id>` — stop a running task · `/tools` — what Jarvis can do here
- `/approvals`, `/approve`, `/deny` — manage confirmations by command too

That is total control of Jarvis from your phone, with native push
notifications, over nothing but an outbound internet connection.

### Push-only (no bot)

If you just want notifications and not the full control bridge, use ntfy:
install the **ntfy** app, subscribe to an unguessable topic, and set:

```yaml
push:
  enabled: true
  topic: jarvis-<something-random>
```

Also outbound-only; no account needed for public ntfy.sh.

## Use it from your phone over HTTP (alternative)

Any HTTP client works (HTTP Shortcuts on Android, Shortcuts on iOS, or
just a browser + curl). All requests carry `Authorization: Bearer <token>`.

```bash
BASE=http://<pc-ip>:8765 ; TOKEN=<your JARVIS_API_TOKEN>

# Send a task
curl -X POST $BASE/tasks -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"request": "download the top 3 python articles you can find and save summaries"}'

# Watch progress
curl $BASE/tasks -H "Authorization: Bearer $TOKEN"
curl $BASE/tasks/<task-id> -H "Authorization: Bearer $TOKEN"

# Live event stream (server-sent events; token via query for EventSource)
curl -N "$BASE/events?token=$TOKEN"

# Approve or deny a dangerous action
curl $BASE/approvals -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/approvals/<id> -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"decision": "allow"}'

# Cancel, upload, notifications, logs
curl -X POST $BASE/tasks/<task-id>/cancel -H "Authorization: Bearer $TOKEN"
curl -F "file=@photo.jpg" $BASE/files/upload -H "Authorization: Bearer $TOKEN"
curl $BASE/notifications -H "Authorization: Bearer $TOKEN"
curl "$BASE/logs?limit=50" -H "Authorization: Bearer $TOKEN"
```

> Exposing the port beyond your LAN is not recommended; if you need remote
> access, put it behind a VPN (Tailscale works well).

## Development

```bash
pip install -e ".[dev,api,phone]"
pytest          # the whole loop, both providers, pool, dashboard, voice — all on fakes
ruff check .    # lint
mypy src        # strict type-check
```

## Configuration model

Four layers, highest precedence first (see `jarvis.config`):

1. **Environment variables** — `JARVIS_` prefix, `__` nesting:
   `JARVIS_API__PORT=9000`, `JARVIS_LOGGING__LEVEL=DEBUG`
2. **Provider shortcuts** — `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`,
   read from the environment or `.env`, since those are the settings that
   change most often
3. **YAML file** — explicit path, `$JARVIS_CONFIG_FILE`,
   `./config/config.yaml`, or the per-user config dir
4. **Coded defaults** — `src/jarvis/config/schema.py`

Secrets are a separate, environment-only layer (`jarvis.config.secrets`):
they cannot be expressed in YAML at all, so they cannot be committed.

## Logging

Console + rotating JSON-lines file (default: per-user log dir, e.g.
`%LOCALAPPDATA%\jarvis\Logs\jarvis.jsonl`). One JSON object per line;
every tool call, decision, error, and timing is recorded with the task id:

```bash
jq 'select(.context.task_id == "task-...")' jarvis.jsonl   # one task's trail
jq 'select(.level == "ERROR")' jarvis.jsonl                # all errors
```

## Project layout

```
src/jarvis/
├── core/           domain models (Task, Plan, ToolResult), event bus, redaction
├── config/         typed settings (YAML+env) and env-only secrets
├── logging/        structured JSON-lines logging with task context
├── security/       token auth + safe/confirm permission policy
├── brain/          Brain protocol + Ollama (local) and Anthropic providers
├── planner/        request → validated dependency graph; revision on failure
├── agent/          orchestrator (policy) + agent pool (concurrency) + roster
├── tools/          Tool abstraction, registry, gated ToolManager
├── files/          sandboxed file manager + 10 file tools
├── memory/         SQLite persistence + 4 memory tools
├── browser/        Playwright controller + 8 browser tools
├── desktop/        Windows controller (backend-swappable) + 8 tools
├── vision/         screenshots + OCR locate + 3 tools
├── voice/          neural TTS, local Whisper STT, wake-word matching
├── dashboard/      event hub + routes + the web HUD (no build step)
├── notifications/  event → notification service; log/live/push channels
├── phone/          Telegram remote-control bridge + HTTP transport
├── api/            FastAPI server for the phone and the dashboard
└── app/            runtime wiring + the `jarvis` CLI
```

Setting someone else up? Send them
[docs/SETUP_FOR_A_FRIEND.md](docs/SETUP_FOR_A_FRIEND.md) — a step-by-step
guide for installing their **own** Jarvis with their **own** model, so
nothing is shared between your machines.

See [docs/COMMANDS.md](docs/COMMANDS.md) for the complete command set, and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design rules and the
decision log.
