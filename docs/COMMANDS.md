# Jarvis — the complete command set

Everything you can type, in one place: starting up (including the update
pull), installing every skill pack, every CLI command, every Telegram
command, every skill Jarvis has, and every HTTP endpoint.

Commands are PowerShell (Windows, the main target). On macOS/Linux the only
differences are `source .venv/bin/activate` instead of
`.venv\Scripts\activate`, and `cp` instead of `copy`.

---

## 1. Every day: start Jarvis

Copy-paste this whole block. It updates to the latest code, then starts the
phone bridge.

```powershell
cd ~\jarvis
git checkout claude/jarvis-startup-skills-commands-ilp298
git pull --ff-only origin claude/jarvis-startup-skills-commands-ilp298
.venv\Scripts\activate
jarvis phone
```

`~` is your user folder, so that block is literal — paste it as-is. It only
assumes you cloned into `C:\Users\<you>\jarvis`, which is what step 2 below
does. If `cd ~\jarvis` says the path doesn't exist, find the folder:

```powershell
Get-ChildItem ~ -Recurse -Depth 4 -Directory -Filter "*arvis*" -ErrorAction SilentlyContinue | Select-Object FullName
```

Then `cd` to what it prints and use that path everywhere below.

Leave that window open; Jarvis is live until you press **Ctrl-C**.

Prefer the local HTTP API instead of Telegram? Swap the last line:

```powershell
jarvis serve                  # http://127.0.0.1:8765
jarvis serve --host 0.0.0.0   # also reachable from your phone on the same wifi
```

### If the pull complains

```powershell
# Local edits in the way — stash them, pull, put them back:
git stash ; git pull --ff-only origin claude/jarvis-startup-skills-commands-ilp298 ; git stash pop

# Check where you actually are:
git branch --show-current
git log --oneline -5
```

### After a pull that changed dependencies

`pyproject.toml` changing is the signal. It's harmless to run anyway:

```powershell
pip install -e ".[llm,api,phone]"
```

---

## 2. One time only: install

```powershell
cd ~
git clone https://github.com/nicolaasvzz/Jarvis. jarvis
cd ~\jarvis
git checkout claude/jarvis-startup-skills-commands-ilp298
python -m venv .venv
.venv\Scripts\activate

pip install -e ".[llm,api,phone]"   # brain + HTTP API + Telegram/push

copy .env.example .env
copy config\config.example.yaml config\config.yaml
jarvis token                        # paste the output into .env as JARVIS_API_TOKEN
```

Open `.env` and fill in the three secrets:

```powershell
notepad ~\jarvis\.env
```

```
ANTHROPIC_API_KEY=sk-ant-...          # console.anthropic.com
JARVIS_API_TOKEN=...                  # the output of: jarvis token
TELEGRAM_BOT_TOKEN=123456:ABC...      # from @BotFather, only if using Telegram
```

Now the workspace — the only folder Jarvis may touch. This creates it and
prints the exact two lines to paste into the config:

```powershell
mkdir ~\JarvisWorkspace -Force | Out-Null
"files:`n  root: " + ((Resolve-Path ~\JarvisWorkspace).Path -replace '\\','/')
```

```powershell
notepad ~\jarvis\config\config.yaml
```

Paste those two lines, and turn Telegram on while you're in there:

```yaml
telegram:
  enabled: true
  owner_chat_id: 123456789      # get it by messaging the bot once, or /whoami
```

Verify:

```powershell
jarvis tools                                   # what's available on this machine
jarvis run "make a file called hello.txt that says hi"
```

---

## 3. Install the skills

Skills come in packs. Files and memory are always there; browser, desktop,
and vision each need their pack installed before Jarvis can use them. Run
these from `~\jarvis` with the venv active:

```powershell
cd ~\jarvis
.venv\Scripts\activate
```

### All of them at once — 33 skills

```powershell
pip install -e ".[llm,api,phone,browser,desktop,vision]"
playwright install chromium
winget install --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
```

Then check it took:

```powershell
jarvis tools | Measure-Object -Line        # expect 33
tesseract --version                        # expect a version, not "not recognized"
```

### Or one pack at a time

Installing a pack never removes another, so you can add them in any order
and stop whenever.

**Files + memory — 14 skills. Nothing to install.**

They ship with the core, so `pip install -e ".[llm]"` is all they need:

```powershell
pip install -e ".[llm]"
jarvis tools | Measure-Object -Line        # expect 14
```

**Browser — +8 skills (22 total)**

```powershell
pip install -e ".[browser]"
playwright install chromium
```

`playwright install chromium` is not optional — the pip package is only the
driver, the browser itself is a separate ~150 MB download. Verify:

```powershell
python -c "import playwright; print('playwright ok')"
jarvis tools | Select-String browser_      # expect 8 lines
```

**Desktop — +8 skills (30 total). Windows only.**

```powershell
pip install -e ".[desktop]"
python -c "import pyautogui, pygetwindow; print('desktop ok')"
jarvis tools | Select-String "open_application|click_at|press_hotkey"
```

`pyautogui` needs a real desktop session — it won't import over a headless
remote shell, and `jarvis tools` will quietly list nothing from this pack if
it can't load.

**Vision — +3 skills (33 total)**

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

### The two non-skill packs

These add channels rather than skills, so the `jarvis tools` count doesn't
move:

```powershell
pip install -e ".[phone]"        # Telegram control + ntfy push  → jarvis phone
pip install -e ".[api]"          # HTTP API + SSE                → jarvis serve
pip install -e ".[dev,api,phone]" # pytest, ruff, mypy
```

### What a pack is made of

| Pack | Installs | Gives |
|---|---|---|
| *(core)* | pydantic, pydantic-settings, platformdirs | 14 file + memory skills |
| `llm` | anthropic | the brain — required to run anything |
| `browser` | playwright *(+ `playwright install chromium`)* | 8 browser skills |
| `desktop` | pyautogui, pygetwindow | 8 desktop skills |
| `vision` | pillow, pytesseract, mss *(+ Tesseract binary)* | 3 vision skills |
| `phone` | httpx | Telegram bridge + ntfy push |
| `api` | fastapi, uvicorn, python-multipart | the HTTP server |
| `dev` | pytest, pytest-asyncio, httpx, ruff, mypy | the test/lint tooling |

---

## 4. Every CLI command

| Command | What it does |
|---|---|
| `jarvis phone` | Run the Telegram bridge — full control from your phone. Outbound HTTPS only: no wifi/LAN, no port forwarding, no exposed server. |
| `jarvis serve` | Start the HTTP API server for a custom app or `curl`. |
| `jarvis serve --host 0.0.0.0 --port 8765` | Same, reachable from the LAN, on a chosen port. |
| `jarvis run "<request>"` | Run one task from the terminal, streaming progress; prompts `y/N` for dangerous steps. |
| `jarvis run "<request>" --yes` | Same, auto-approving dangerous actions. Use with care. |
| `jarvis tools` | List every tool available here, marking which need confirmation. |
| `jarvis token` | Generate a strong random value for `JARVIS_API_TOKEN`. |
| `jarvis --version` | Print the version. |
| `jarvis --config PATH <command>` | Use a specific config YAML for this run. Goes **before** the subcommand. |
| `jarvis <command> --help` | Help for any subcommand. |

---

## 5. Every Telegram command

Start the bridge with `jarvis phone`, then in your Telegram chat with the bot:

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

### Files — 10 skills, always available

Sandboxed to `files.root`; Jarvis cannot read or write outside it.

| Skill | What it does |
|---|---|
| `read_file(path)` | Read a file's text contents. |
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

### Browser — 8 skills, needs the `browser` pack (§3)

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

**33 skills** with every pack installed; **14** with the core install. See
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
either as `Authorization: Bearer <token>` or as `?token=<token>`.

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

# Notifications, uploads, logs
curl "$BASE/notifications?limit=50" -H "Authorization: Bearer $TOKEN"
curl -F "file=@photo.jpg" $BASE/files/upload -H "Authorization: Bearer $TOKEN"
curl "$BASE/logs?limit=100" -H "Authorization: Bearer $TOKEN"
```

Don't expose this port to the internet. For remote access use the Telegram
bridge, or put the API behind a VPN such as Tailscale.

---

## 8. Development commands

```bash
pip install -e ".[dev,api,phone]"
pytest                     # the whole loop + phone bridge, against fakes
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
$env:JARVIS_LOGGING__LEVEL="DEBUG" ; jarvis phone
$env:JARVIS_CONFIG_FILE="D:\jarvis\alt.yaml" ; jarvis tools
jarvis --config D:\jarvis\alt.yaml run "<request>"     # same thing, per-run
```

---

## 10. When something breaks

```powershell
jarvis tools                       # fewer tools than expected = an extra isn't installed
git pull --ff-only                 # you may just be behind
pip install -e ".[llm,api,phone]"  # re-sync dependencies after a pull
```

Logs are JSON-lines at `%LOCALAPPDATA%\jarvis\Logs\jarvis.jsonl` — every tool
call, decision, error, and timing:

```bash
jq 'select(.context.task_id == "task-...")' jarvis.jsonl   # one task's trail
jq 'select(.level == "ERROR")' jarvis.jsonl                # all errors
```

| Message | Meaning |
|---|---|
| `Missing required secret 'anthropic_api_key'` | `.env` isn't filled in, or you're running from a different folder. |
| `Cannot start: ...` on `jarvis serve` | No `JARVIS_API_TOKEN` — run `jarvis token` and put it in `.env`. |
| `The Telegram bridge is not configured` | `telegram.enabled: true` missing in `config.yaml`, or `TELEGRAM_BOT_TOKEN` unset. |
| `You're not authorised to control this Jarvis` | Set `telegram.owner_chat_id` to the chat id the bot replies with. |
