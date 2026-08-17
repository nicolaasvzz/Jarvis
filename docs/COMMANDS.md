# Jarvis — the complete command set

Everything you can type, in one place: starting up (including the update
pull), every CLI command, every Telegram command, every skill Jarvis has,
and every HTTP endpoint.

Commands are PowerShell (Windows, the main target). On macOS/Linux the only
differences are `source .venv/bin/activate` instead of
`.venv\Scripts\activate`, and `cp` instead of `copy`.

---

## 1. Every day: start Jarvis

Copy-paste this whole block. It updates to the latest code, then starts the
phone bridge.

```powershell
cd C:\path\to\jarvis          # wherever you cloned it
git pull --ff-only            # get the latest code
.venv\Scripts\activate        # enter the virtual environment
jarvis phone                  # start it — control from Telegram
```

Leave that window open; Jarvis is live until you press **Ctrl-C**.

Prefer the local HTTP API instead of Telegram? Swap the last line:

```powershell
jarvis serve                  # http://127.0.0.1:8765
jarvis serve --host 0.0.0.0   # also reachable from your phone on the same wifi
```

### If `git pull --ff-only` complains

```powershell
# No tracking branch set — name the branch explicitly:
git pull --ff-only origin $(git rev-parse --abbrev-ref HEAD)

# You have local edits in the way — stash them, pull, put them back:
git stash ; git pull --ff-only ; git stash pop

# Check what branch you're on / switch to the one you were told to use:
git branch --show-current
git checkout claude/jarvis-startup-skills-commands-ilp298
```

### After a pull that changed dependencies

`pyproject.toml` changing is the signal. It's harmless to run anyway:

```powershell
pip install -e ".[llm,api,phone]"
```

---

## 2. One time only: install

```powershell
git clone https://github.com/nicolaasvzz/Jarvis. jarvis
cd jarvis
python -m venv .venv
.venv\Scripts\activate

pip install -e ".[llm,api,phone]"   # brain + HTTP API + Telegram/push

# Optional capability packs — install the ones you want:
pip install -e ".[browser]" ; playwright install chromium
pip install -e ".[desktop]"
pip install -e ".[vision]"          # also install Tesseract OCR

copy .env.example .env
copy config\config.example.yaml config\config.yaml
jarvis token                        # paste the output into .env as JARVIS_API_TOKEN
```

Then edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
JARVIS_API_TOKEN=<output of: jarvis token>
TELEGRAM_BOT_TOKEN=123456:ABC...     # from @BotFather, only if using Telegram
```

And `config\config.yaml` — point it at the only folder Jarvis may touch:

```yaml
files:
  root: C:/Users/<you>/JarvisWorkspace
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

## 3. Every CLI command

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

## 4. Every Telegram command

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

## 5. Every skill

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

### Browser — 8 skills, needs `pip install -e ".[browser]"` + `playwright install chromium`

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

### Desktop — 8 skills, needs `pip install -e ".[desktop]"` (Windows)

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

### Vision — 3 skills, needs `pip install -e ".[vision]"` + Tesseract OCR

| Skill | What it does |
|---|---|
| `capture_screen()` | Screenshot; returns the saved workspace path. |
| `read_screen_text()` | OCR everything currently on screen. |
| `locate_text_on_screen(text)` | Find text on screen, return its centre `{x, y}` for `click_at`. |

**33 skills** with every pack installed; **14** with the core install.

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

## 6. Every HTTP API command

Start with `jarvis serve`. Every route except `/health` needs the token,
either as `Authorization: Bearer <token>` or as `?token=<token>`.

```bash
BASE=http://<pc-ip>:8765 ; TOKEN=<your JARVIS_API_TOKEN>

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

## 7. Development commands

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

## 8. Config overrides for one run

Environment variables win over `config.yaml`. Prefix `JARVIS_`, nest with `__`:

```powershell
$env:JARVIS_API__PORT=9000        ; jarvis serve
$env:JARVIS_LOGGING__LEVEL="DEBUG" ; jarvis phone
$env:JARVIS_CONFIG_FILE="D:\jarvis\alt.yaml" ; jarvis tools
jarvis --config D:\jarvis\alt.yaml run "<request>"     # same thing, per-run
```

---

## 9. When something breaks

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
