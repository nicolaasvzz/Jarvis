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

Requires Python 3.11+.

```powershell
git clone <this repo> jarvis && cd jarvis
python -m venv .venv
.venv\Scripts\activate

# Core + API server + the LLM client:
pip install -e ".[llm,api]"

# Phone control + push notifications (Telegram / ntfy):
pip install -e ".[phone]"

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

# Control it from your phone via Telegram (recommended — no wifi/LAN needed):
jarvis phone

# Or start the local HTTP API for a custom app / curl on the same network:
jarvis serve            # add --host 0.0.0.0 to accept LAN connections
```

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
pytest          # 106 tests — the whole loop + phone bridge run against fakes
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
├── notifications/  event → notification service; log/live/push channels
├── phone/          Telegram remote-control bridge + HTTP transport
├── api/            FastAPI server for the phone
└── app/            runtime wiring + the `jarvis` CLI
```

Setting someone else up? Send them
[docs/SETUP_FOR_A_FRIEND.md](docs/SETUP_FOR_A_FRIEND.md) — a step-by-step
guide for installing their **own** Jarvis with their **own** API key, so
nothing is shared between your machines.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design rules and the
decision log.
