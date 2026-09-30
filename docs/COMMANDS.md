# Jarvis — the complete command set

Everything you can type, in one place: starting up (including the update
pull), installing every skill pack, every CLI command, every Telegram
command, every skill Jarvis has, and every HTTP endpoint.

Commands are PowerShell (Windows, the main target). On macOS/Linux the only
differences are `source .venv/bin/activate` instead of
`.venv\Scripts\activate`, and `cp` instead of `copy`.

The repository has two halves: **`backend\`** (the assistant — everything
below runs there) and **`frontend\`** (the web dashboard, which the backend
serves at `/dash/`, or which you open on its own — see
[frontend/README.md](../frontend/README.md)). Run `jarvis` commands from
inside `backend\`: that is where `.env` and `config\config.yaml` live.

---

## The one-paste way

On a machine that has nothing yet — no Git, no Python, no clone — paste the
block from [the README](../README.md#start-here-windows-one-paste) (same
content as `backend\scripts\bootstrap.ps1`). It installs the prerequisites,
clones Jarvis, and then runs the script below with every skill pack.

## The one-file way

Once the clone exists, `backend\scripts\jarvis.ps1` does sections 1-3 below
— update, install, configure, start — in a single command that is safe to
re-run:

```powershell
powershell -ExecutionPolicy Bypass -File ~\jarvis\backend\scripts\jarvis.ps1
```

It clones Jarvis if it isn't there yet, so that command also works as the
very first thing you ever run. Useful variations:

```powershell
# Install every skill pack, then start:
powershell -ExecutionPolicy Bypass -File ~\jarvis\backend\scripts\jarvis.ps1 -Skills all

# Add just two packs and don't start anything:
powershell -ExecutionPolicy Bypass -File ~\jarvis\backend\scripts\jarvis.ps1 -Skills "browser,vision" -Start none

# Update, then run one task in the terminal:
powershell -ExecutionPolicy Bypass -File ~\jarvis\backend\scripts\jarvis.ps1 -Start run -Request "organize my workspace by file type"

# Start just the Telegram bridge, or the API without opening a browser:
powershell -ExecutionPolicy Bypass -File ~\jarvis\backend\scripts\jarvis.ps1 -Start phone
powershell -ExecutionPolicy Bypass -File ~\jarvis\backend\scripts\jarvis.ps1 -Start serve

# Skip the git pull / skip installing Tesseract:
powershell -ExecutionPolicy Bypass -File ~\jarvis\backend\scripts\jarvis.ps1 -NoUpdate -NoTesseract
```

Already in a PowerShell window in the repo? Then it's just
`.\backend\scripts\jarvis.ps1` — the `-ExecutionPolicy Bypass -File` wrapper
is only needed if PowerShell refuses to run local scripts.

What it does: checks git and Python 3.11+, clones or fast-forwards the repo
(stashing local edits first), creates `backend\.venv`, installs the packs you
asked for plus their non-Python parts, creates `backend\.env` and
`backend\config\config.yaml`, generates `JARVIS_API_TOKEN`, creates the
workspace folder and writes it into the config, asks for your free
`GEMINI_API_KEY` if it isn't set, turns on the Telegram bridge once a bot
token exists, prints the skill inventory, and starts Jarvis with its
dashboard. It never overwrites a value you already set. Given a `backend`
folder on its own (no repository around it), it skips the git steps.

The rest of this page is the same work done by hand — useful when something
goes wrong, or when you want to run one piece on its own.

---

## 1. Every day: start Jarvis

Copy-paste this whole block. It updates to the latest code, then starts
Jarvis and opens the dashboard.

```powershell
cd ~\jarvis
git checkout main
git pull --ff-only origin main
cd backend
.venv\Scripts\activate
jarvis dash
```

`~` is your user folder, so that block is literal — paste it as-is. It only
assumes you cloned into `C:\Users\<you>\jarvis`, which is what step 2 below
does. If `cd ~\jarvis` says the path doesn't exist, find the folder:

```powershell
Get-ChildItem ~ -Recurse -Depth 4 -Directory -Filter "*arvis*" -ErrorAction SilentlyContinue | Select-Object FullName
```

Then `cd` to what it prints and use that path everywhere below.

Leave that window open; Jarvis is live until you press **Ctrl-C**.
Alternatives for the last line:

```powershell
jarvis serve                  # the same, without opening a browser
jarvis serve --host 0.0.0.0   # also reachable from other devices on the same wifi
jarvis phone                  # just the Telegram bridge
```

### If the pull complains

```powershell
# Local edits in the way — stash them, pull, put them back:
git stash ; git pull --ff-only origin main ; git stash pop

# Check where you actually are:
git branch --show-current
git log --oneline -5
```

### After a pull that changed dependencies

`backend\pyproject.toml` changing is the signal. It's harmless to run anyway,
from `backend\`:

```powershell
pip install -e ".[api,dash,voice,phone]"
```

### Coming from before the backend/frontend split

Older copies kept `.env` and `config\config.yaml` at the top of the repo.
They now live in `backend\`. `jarvis.ps1` moves them for you; by hand:

```powershell
cd ~\jarvis
move .env backend\.env
move config\config.yaml backend\config\config.yaml
cd backend ; python -m venv .venv ; .venv\Scripts\activate
pip install -e ".[api,dash,voice,phone]"
```

If your `.env` or `config.yaml` still says `ollama`, switch it to Gemini:
`LLM_PROVIDER=gemini`, add `GEMINI_API_KEY`, and delete any `base_url`,
`context_window`, `think` or `num_gpu` lines under `llm:` — `jarvis brain`
names anything left over.

---

## 2. One time only: install

```powershell
cd ~
git clone https://github.com/nicolaasvzz/Jarvis. jarvis
cd ~\jarvis\backend
python -m venv .venv
.venv\Scripts\activate

pip install -e ".[api,dash,voice,phone]"   # API + dashboard + voice + Telegram/push

copy .env.example .env
copy config\config.example.yaml config\config.yaml
jarvis token                               # paste the output into .env as JARVIS_API_TOKEN
```

Jarvis thinks with Google Gemini, and a **free** key is enough: sign in at
https://aistudio.google.com/apikey, click **Create API key**, and put it in
`.env`:

```powershell
notepad ~\jarvis\backend\.env
```

```
GEMINI_API_KEY=...                    # from aistudio.google.com/apikey
JARVIS_API_TOKEN=...                  # the output of: jarvis token
TELEGRAM_BOT_TOKEN=123456:ABC...      # from @BotFather, only if using Telegram
```

```powershell
jarvis brain                          # provider, model, and whether the key works
```

Using Claude instead (paid) — see
[Choosing the model](../backend/README.md#choosing-the-model).

Now the workspace — the only folder Jarvis may touch. This creates it and
prints the exact two lines to paste into the config:

```powershell
mkdir ~\JarvisWorkspace -Force | Out-Null
"files:`n  root: " + ((Resolve-Path ~\JarvisWorkspace).Path -replace '\\','/')
```

```powershell
notepad ~\jarvis\backend\config\config.yaml
```

Paste those two lines, and turn Telegram on while you're in there:

```yaml
telegram:
  enabled: true
  owner_chat_id: 123456789      # get it by messaging the bot once, or /whoami
```

Verify:

```powershell
jarvis brain                                   # does the key work?
jarvis tools                                   # what's available on this machine
jarvis run "make a file called hello.txt that says hi"
```

---

## 3. Install the skills

Skills come in packs. Files and memory are always there; browser, desktop,
and vision each need their pack installed before Jarvis can use them. Run
these from `~\jarvis\backend` with the venv active:

```powershell
cd ~\jarvis\backend
.venv\Scripts\activate
```

### All of them at once — 33 skills

```powershell
pip install -e ".[api,dash,voice,phone,browser,desktop,vision]"
playwright install chromium
winget install --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
```

Then check it took:

```powershell
jarvis tools | Measure-Object -Line        # expect 33 or more
tesseract --version                        # expect a version, not "not recognized"
```

### Or one pack at a time

Installing a pack never removes another, so you can add them in any order
and stop whenever.

**Files + memory — always there. Nothing to install.**

They ship with the core, and so does the Gemini brain, so a bare install is
all they need:

```powershell
pip install -e "."
jarvis tools
```

**Browser — +9 skills (24 total)**

```powershell
pip install -e ".[browser]"
playwright install chromium
```

`playwright install chromium` is not optional — the pip package is only the
driver, the browser itself is a separate ~150 MB download. Verify:

```powershell
python -c "import playwright; print('playwright ok')"
jarvis tools | Select-String browser_      # expect 9 lines
```

**Desktop — +8 skills (32 total). Windows only.**

```powershell
pip install -e ".[desktop]"
python -c "import pyautogui, pygetwindow; print('desktop ok')"
jarvis tools | Select-String "open_application|click_at|press_hotkey"
```

`pyautogui` needs a real desktop session — it won't import over a headless
remote shell, and `jarvis tools` will quietly list nothing from this pack if
it can't load.

**Vision — +3 skills (35 total)**

Two halves. The pip install alone gets you `capture_screen`; the two OCR
skills (`read_screen_text`, `locate_text_on_screen`) also need the Tesseract
*binary*, which is not a Python package:

```powershell
pip install -e ".[vision]"
winget search tesseract                    # confirm the package id
winget install --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
tesseract --version
```

If `tesseract --version` says *not recognized*, it installed but isn't on
PATH. Jarvis reads it from PATH only (there's no config setting for it), so
add it permanently and open a new PowerShell:

```powershell
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\Program Files\Tesseract-OCR", "User")
```

No winget? Download the installer from
https://github.com/UB-Mannheim/tesseract/wiki and tick *Add to PATH*.

### The packs that are not skills

These add channels, the dashboard, a voice, or a different model provider
rather than skills, so the `jarvis tools` count doesn't move:

```powershell
pip install -e ".[api]"          # HTTP API + SSE                → jarvis serve
pip install -e ".[dash]"         # the dashboard's API + vitals  → jarvis dash
pip install -e ".[voice]"        # Jarvis speaks (free neural voices)
pip install -e ".[listen]"       # Jarvis listens (local Whisper; large download)
pip install -e ".[phone]"        # Telegram control + ntfy push  → jarvis phone
pip install -e ".[llm]"          # paid Claude instead of Gemini
pip install -e ".[dev,api,dash,phone]" # pytest, ruff, mypy
```

### What a pack is made of

| Pack | Installs | Gives |
|---|---|---|
| *(core)* | pydantic, pydantic-settings, platformdirs, httpx | 15 file + memory skills, and the Gemini brain |
| `llm` | anthropic | optional: paid Claude instead of Gemini |
| `browser` | playwright *(+ `playwright install chromium`)* | 9 browser skills |
| `desktop` | pyautogui, pygetwindow | 8 desktop skills |
| `vision` | pillow, pytesseract, mss *(+ Tesseract binary)* | 3 vision skills |
| `phone` | httpx | Telegram bridge + ntfy push |
| `api` | fastapi, uvicorn, python-multipart | the HTTP server |
| `dash` | psutil | the dashboard's machine-vitals panel |
| `voice` | edge-tts | speech out |
| `listen` | faster-whisper | speech in, transcribed locally |
| `dev` | pytest, pytest-asyncio, httpx, ruff, mypy | the test/lint tooling |

---

## 4. Every CLI command

| Command | What it does |
|---|---|
| `jarvis dash` | Start everything — API, dashboard, Telegram bridge — and open the dashboard in your browser. |
| `jarvis serve` | The same without opening a browser. Serves the dashboard at `/dash/` when a `frontend` folder is beside `backend`. |
| `jarvis serve --host 0.0.0.0 --port 8765` | Same, reachable from the LAN, on a chosen port. |
| `jarvis serve --no-dash` | The HTTP API only. |
| `jarvis phone` | Run just the Telegram bridge — full control from your phone. Outbound HTTPS only: no wifi/LAN, no port forwarding, no exposed server. |
| `jarvis run "<request>"` | Run one task from the terminal, streaming progress; prompts `y/N` for dangerous steps. |
| `jarvis run "<request>" --yes` | Same, auto-approving dangerous actions. Use with care. |
| `jarvis brain` | Show the configured provider and model, and check the key and model work (a lookup — spends no quota). Exits non-zero when they don't, saying why. |
| `jarvis tools` | List every tool available here, marking which need confirmation. |
| `jarvis token` | Generate a strong random value for `JARVIS_API_TOKEN`. |
| `jarvis --version` | Print the version. |
| `jarvis --config PATH <command>` | Use a specific config YAML for this run. Goes **before** the subcommand. |
| `jarvis <command> --help` | Help for any subcommand. |

---

## 5. Every Telegram command

Start the bridge with `jarvis phone` (or `jarvis dash`/`serve`, which run it
too once it's configured), then in your Telegram chat with the bot:

| Command | What it does |
|---|---|
| *(just type anything)* | Send a task. Jarvis plans it, does it, and messages progress back. |
| `/status` | Recent tasks and their state. |
| `/task <id>` | Full detail of one task: every step and its status. |
| `/cancel <id>` | Stop a running task. |
| `/approvals` | List actions waiting for your decision. |
| `/approve [id]` | Allow a pending action. |
| `/deny [id]` | Refuse a pending action. |
| `/tools` | What Jarvis can do on that machine. |
| `/whoami` | Your chat id — paste it into `telegram.owner_chat_id`. |
| `/help` (or `/start`) | The command list. |

Dangerous actions also arrive as a message with **✅ Allow / ⛔ Deny** buttons —
tapping one is the same as `/approve` or `/deny`.

---

## 6. Every skill

These are the tools Jarvis chooses from while planning. You don't call them
directly — you describe what you want in plain language and it picks. They're
listed so you know what's actually possible, and `jarvis tools` prints the
subset installed on your machine.

### Files — 11 skills, always available

Sandboxed to `files.root`; Jarvis cannot read or write outside it.

| Skill | What it does |
|---|---|
| `read_file(path)` | Read a file's text contents. |
| `read_file_slice(path, start=0, length=6000)` | Read part of a long file, with where to continue from — for archived pages and transcripts. |
| `write_file(path, content)` | Create or overwrite a text file. |
| `list_directory(path=".")` | List a folder's entries with type and size. |
| `make_directory(path)` | Create a folder, including missing parents. |
| `move_path(source, destination)` | Move or rename a file or folder. |
| `copy_path(source, destination)` | Copy a file or folder. |
| `delete_path(path)` | **Asks first.** Delete a file or folder. |
| `search_files(pattern, path=".")` | Recursively find files matching a glob. |
| `compress_files(sources, archive)` | Zip files or folders. |
| `extract_archive(archive, destination=".")` | Unzip into a folder. |

### Memory — 4 skills, always available

Persisted in SQLite, so it survives restarts.

| Skill | What it does |
|---|---|
| `remember_fact(content, tag="")` | Store something to remember, optionally tagged. |
| `recall_facts(tag="")` | Recall stored facts, optionally by tag. |
| `set_preference(key, value)` | Save a preference, e.g. `photos_folder`. |
| `get_preference(key)` | Look a preference back up. |

### Browser — 9 skills, needs the `browser` pack (§3)

One persistent session, so logins survive between steps.

| Skill | What it does |
|---|---|
| `browser_open(url)` | Open a URL, report its title. |
| `browser_read_page()` | Read the visible text of the open page. |
| `browser_click(target)` | Click by CSS selector or by visible text. |
| `browser_fill(selector, value)` | Fill a form field. |
| `browser_upload(selector, path)` | Attach a workspace file to a file input. |
| `browser_download(url, save_as)` | Download into the workspace, using session cookies. |
| `browser_extract_links()` | List the links (text + URL) on the page. |
| `browser_search(query)` | Web search, top results as title + URL. |
| `browser_research(query, pages=2, save_to="research")` | Search, then open and archive the top pages in the workspace; returns each page's URL, title, saved path and an excerpt. |

### Desktop — 8 skills, needs the `desktop` pack (§3). Windows only.

| Skill | What it does |
|---|---|
| `open_application(command)` | Open an app by name or path, e.g. `notepad`. |
| `list_windows()` | Titles of all open windows. |
| `focus_window(title)` | Bring a window to the front. |
| `close_window(title)` | Close a window. |
| `move_window(title, x, y)` | Move a window to screen coordinates. |
| `click_at(x, y)` | Click at screen coordinates — coordinates come from `locate_text_on_screen`, never guesswork. |
| `type_text(text)` | Type into the focused window. |
| `press_hotkey(keys)` | Press a shortcut, `+`-separated: `ctrl+s`, `alt+f4`. |

### Vision — 3 skills, needs the `vision` pack + Tesseract (§3)

| Skill | What it does |
|---|---|
| `capture_screen()` | Screenshot; returns the saved workspace path. |
| `read_screen_text()` | OCR everything currently on screen. |
| `locate_text_on_screen(text)` | Find text on screen, return its centre `{x, y}` for `click_at`. |

**35 skills** with every pack installed; **15** with the core install. See
§3 for the install commands.

### Which skills ask permission

Anything whose risk category is listed under `security.require_confirmation`
in `config.yaml` stops and waits for your approval:

```yaml
security:
  require_confirmation:
    - delete_files          # delete_path
    - send_email
    - install_software
    - modify_system_settings
    - spend_money
    - elevated_shell
```

`jarvis tools` marks these with `[confirm: <category>]`.

---

## 7. Every HTTP API command

Start with `jarvis serve`. Every route except `/health` needs the token,
either as `Authorization: Bearer <token>` or as `?token=<token>`. The
interactive reference is at `http://127.0.0.1:8765/docs`.

On the PC itself the base URL is exactly `http://127.0.0.1:8765`. To reach it
from your phone you need the PC's LAN address — this prints it, and you must
have started the server with `jarvis serve --host 0.0.0.0`:

```powershell
(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike "*Loopback*" }).IPAddress
```

Set both variables once, then every command below runs verbatim:

```bash
BASE=http://127.0.0.1:8765            # or http://<the IP printed above>:8765
TOKEN=$JARVIS_API_TOKEN               # the same value as in .env

# Health (no auth)
curl $BASE/health

# Tasks
curl -X POST $BASE/tasks -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"request": "<what to do>"}'
curl $BASE/tasks              -H "Authorization: Bearer $TOKEN"
curl $BASE/tasks/<task-id>    -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/tasks/<task-id>/cancel -H "Authorization: Bearer $TOKEN"

# Approvals
curl $BASE/approvals -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/approvals/<approval-id> -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"decision": "allow"}'   # or "deny"

# Live event stream (SSE — token as a query param so EventSource works)
curl -N "$BASE/events?token=$TOKEN"

# Notifications, uploads, logs, system summary
curl "$BASE/notifications?limit=50" -H "Authorization: Bearer $TOKEN"
curl -F "file=@photo.jpg" $BASE/files/upload -H "Authorization: Bearer $TOKEN"
curl "$BASE/logs?limit=100" -H "Authorization: Bearer $TOKEN"
curl $BASE/system -H "Authorization: Bearer $TOKEN"

# The dashboard's own API (what the frontend calls)
curl $BASE/dash/api/snapshot -H "Authorization: Bearer $TOKEN"   # everything, from cold
curl -N "$BASE/dash/api/stream?token=$TOKEN"                     # enriched live events
curl $BASE/dash/api/tree  -H "Authorization: Bearer $TOKEN"      # the file constellation
curl $BASE/dash/api/stats -H "Authorization: Bearer $TOKEN"      # CPU, memory, disk, battery
curl -X POST $BASE/dash/api/command -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"text": "jarvis, what time is it"}'
```

A web page on another origin (a frontend opened on its own) may call these
when its origin is on `localhost`/`127.0.0.1`, or listed in
`api.cors_origins`.

Don't expose this port to the internet. For remote access use the Telegram
bridge, or put the API behind a VPN such as Tailscale.

---

## 8. Development commands

From `backend\`:

```bash
pip install -e ".[dev,api,dash,phone]"
python -m pytest           # the whole loop, both providers, dashboard — against fakes
pytest tests/test_api.py   # one file
pytest -k telegram         # one pattern
ruff check .               # lint
ruff check . --fix         # lint + autofix
mypy src                   # strict type-check
```

---

## 9. Config overrides for one run

Environment variables win over `config.yaml`. Prefix `JARVIS_`, nest with `__`:

```powershell
$env:JARVIS_API__PORT=9000        ; jarvis serve
$env:JARVIS_LOGGING__LEVEL="DEBUG" ; jarvis dash
$env:JARVIS_CONFIG_FILE="D:\jarvis\alt.yaml" ; jarvis tools
jarvis --config D:\jarvis\alt.yaml run "<request>"     # same thing, per-run
```

---

## 10. When something breaks

```powershell
jarvis tools                       # fewer tools than expected = an extra isn't installed
git pull --ff-only                 # you may just be behind
pip install -e ".[api,dash,voice,phone]"   # re-sync dependencies after a pull (from backend\)
```

Logs are JSON-lines at `%LOCALAPPDATA%\jarvis\Logs\jarvis.jsonl` — every tool
call, decision, error, and timing:

```bash
jq 'select(.context.task_id == "task-...")' jarvis.jsonl   # one task's trail
jq 'select(.level == "ERROR")' jarvis.jsonl                # all errors
```

| Message | Meaning |
|---|---|
| `Missing required secret 'gemini_api_key'` | No `GEMINI_API_KEY` in `backend\.env` — get a free one at https://aistudio.google.com/apikey. Also check you ran `jarvis` from inside `backend\`. |
| `Gemini rejected the API key` | The key is wrong or was deleted — make a new one at https://aistudio.google.com/apikey. |
| `Gemini's rate limit was hit` | The free tier's per-minute or per-day limit. Wait; lower `agent.pool_size` if it keeps happening. |
| `Gemini has no model called ...` | `LLM_MODEL` / `llm.model` names a model that doesn't exist. Remove it to use the default. |
| `Ollama support has been removed` | An old config. Set `LLM_PROVIDER=gemini` and delete the Ollama settings it names. |
| `Missing required secret 'anthropic_api_key'` | Only when `LLM_PROVIDER=anthropic`. On Gemini (the default), this key isn't needed. |
| `Cannot start: ...` on `jarvis serve` | No `JARVIS_API_TOKEN` — run `jarvis token` and put it in `.env`. |
| `The Telegram bridge is not configured` | `telegram.enabled: true` missing in `config.yaml`, or `TELEGRAM_BOT_TOKEN` unset. |
| `You're not authorised to control this Jarvis` | Set `telegram.owner_chat_id` to the chat id the bot replies with. |
| Dashboard says *could not reach Jarvis* | The backend isn't running, the server address is wrong, or (for a frontend opened elsewhere) its origin isn't in `api.cors_origins`. |
