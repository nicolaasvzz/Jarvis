# Jarvis

A personal AI desktop assistant that runs continuously on your Windows
machine, receives natural-language instructions from your phone, **plans
before it acts**, and controls the computer safely — asking for your
confirmation before dangerous actions and notifying you as work progresses.

```
phone ──HTTP+token──▶ API Server ──▶ Brain (Claude) ──▶ Planner
browser ──▶ Dashboard ──┘                 │                 │
     ▲                                    │        dependency-graph plan
     └── live events ── Event Bus ── Agent Pool ──▶ Tool Manager ──▶ policy
                                    (N agents)           │      (confirm dangerous)
                     files · memory · browser · desktop · vision
```

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

Requires Python 3.11+.

```powershell
git clone <this repo> jarvis && cd jarvis
python -m venv .venv
.venv\Scripts\activate

# Core + API server + the LLM client:
pip install -e ".[llm,api]"

# The web dashboard, and a voice to go with it:
pip install -e ".[dash,voice,listen]"

# Phone control + push notifications (Telegram / ntfy):
pip install -e ".[phone]"

# Optional capability packs:
pip install -e ".[browser]"   ;  playwright install chromium
pip install -e ".[desktop]"
pip install -e ".[vision]"    # also install Tesseract OCR for screen reading
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
ANTHROPIC_API_KEY=sk-ant-...        # console.anthropic.com
JARVIS_API_TOKEN=<run: jarvis token>
```

## Run

```powershell
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
pytest          # 197 tests — loop, pool, dashboard, voice, all against fakes
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
├── core/           domain models (Task, Plan, ToolResult), event bus, redaction
├── config/         typed settings (YAML+env) and env-only secrets
├── logging/        structured JSON-lines logging with task context
├── security/       token auth + safe/confirm permission policy
├── brain/          Brain protocol + Anthropic implementation
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
guide for installing their **own** Jarvis with their **own** API key, so
nothing is shared between your machines.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design rules and the
decision log.
