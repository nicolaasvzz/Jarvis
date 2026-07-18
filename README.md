# Jarvis

A personal AI desktop assistant that runs continuously on your Windows
machine, receives natural-language instructions from your phone, **plans
before it acts**, and controls the computer safely — asking for your
confirmation before dangerous actions and notifying you as work progresses.

```
phone ──HTTP+token──▶ API Server ──▶ Brain (Claude) ──▶ Planner
                                          │
                                    Tool Manager ──▶ permission policy
                                          │          (confirm dangerous)
                     files · memory · browser · desktop · vision
```

## What it can do

- **Understand natural language** and answer questions directly, or break a
  request into an ordered, risk-tagged plan before touching anything.
- **Execute one step at a time**, observing each result; failing steps are
  retried, then replanned ("try another approach"), then reported — never
  crashed on.
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
- **Phone control**: submit tasks, watch live progress, approve or deny
  dangerous actions, cancel tasks, upload files, read notifications and
  logs — all over an authenticated HTTP API.

## Install (on the Windows machine)

Requires Python 3.11+.

```powershell
git clone <this repo> jarvis && cd jarvis
python -m venv .venv
.venv\Scripts\activate

# Core + API server + the LLM client:
pip install -e ".[llm,api]"

# Optional capability packs:
pip install -e ".[browser]"   ;  playwright install chromium
pip install -e ".[desktop]"
pip install -e ".[vision]"    # also install Tesseract OCR for screen reading
```

Configure:

```powershell
copy config\config.example.yaml config\config.yaml   # optional, has defaults
copy .env.example .env
```

Edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...        # console.anthropic.com
JARVIS_API_TOKEN=<run: jarvis token>
```

## Run

```powershell
# See which tools are available on this machine:
jarvis tools

# One-off task from the terminal (approvals prompt with y/N):
jarvis run "organize the files in my workspace by extension"

# Start the server your phone talks to:
jarvis serve            # add --host 0.0.0.0 to accept LAN connections
```

## Use it from your phone

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
pip install -e ".[dev,api]"
pytest          # 95 tests — the whole loop runs against a scripted brain
ruff check .    # lint
mypy src        # strict type-check
```

## Configuration model

Three layers, highest precedence first (see `jarvis.config`):

1. **Environment variables** — `JARVIS_` prefix, `__` nesting:
   `JARVIS_API__PORT=9000`, `JARVIS_LOGGING__LEVEL=DEBUG`
2. **YAML file** — explicit path, `$JARVIS_CONFIG_FILE`,
   `./config/config.yaml`, or the per-user config dir
3. **Coded defaults** — `src/jarvis/config/schema.py`

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
├── core/           domain models (Task, Plan, ToolResult), event bus, errors
├── config/         typed settings (YAML+env) and env-only secrets
├── logging/        structured JSON-lines logging with task context
├── security/       token auth + safe/confirm permission policy
├── brain/          Brain protocol + Anthropic implementation
├── planner/        request → validated JSON plan; revision on failure
├── agent/          the plan → execute → observe orchestrator
├── tools/          Tool abstraction, registry, gated ToolManager
├── files/          sandboxed file manager + 10 file tools
├── memory/         SQLite persistence + 4 memory tools
├── browser/        Playwright controller + 8 browser tools
├── desktop/        Windows controller (backend-swappable) + 8 tools
├── vision/         screenshots + OCR locate + 3 tools
├── notifications/  event → notification service with channels
├── api/            FastAPI server for the phone
└── app/            runtime wiring + the `jarvis` CLI
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design rules and the
decision log.
