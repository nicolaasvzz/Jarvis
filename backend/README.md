# Jarvis — backend

The assistant itself: the planner, the agent pool, every tool, the HTTP
API, the Telegram phone bridge and the voice. Python 3.11+.

It runs fine on its own. With a [`frontend/`](../frontend/) folder next to it,
it also serves the web dashboard; without one it is an API, a terminal tool
and a Telegram bot, and any copy of the frontend can connect to it over HTTP.

All commands below are run **from this `backend/` folder** — that is where
Jarvis looks for `.env` and `config/config.yaml`.

## Install

On Windows, one script does everything below (venv, packs, config, keys) and
is safe to re-run every day:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\jarvis.ps1
```

By hand, on any OS:

```bash
python -m venv .venv
.venv\Scripts\activate                  # macOS/Linux: source .venv/bin/activate

pip install -e ".[api,dash]"            # core + HTTP API + dashboard backend
pip install -e ".[voice]"               # a voice (free, no account)
pip install -e ".[phone]"               # Telegram control + push notifications

# Optional capability packs:
pip install -e ".[browser]"  ;  playwright install chromium
pip install -e ".[desktop]"             # Windows only
pip install -e ".[vision]"              # also install Tesseract OCR for screen reading
pip install -e ".[listen]"              # local speech recognition (large download)
pip install -e ".[llm]"                 # only to use Claude instead of Gemini
```

Configure:

```bash
copy .env.example .env                          # macOS/Linux: cp
copy config\config.example.yaml config\config.yaml   # optional; everything has defaults
jarvis token                                    # paste into .env as JARVIS_API_TOKEN
```

and put your **free** Gemini key in `.env` (get one at
https://aistudio.google.com/apikey — sign in, **Create API key**):

```
GEMINI_API_KEY=your-key
JARVIS_API_TOKEN=the-output-of-jarvis-token
```

`.env` and `config/config.yaml` are gitignored — secrets can't be put in the
YAML at all, so they can't be committed by accident.

## Choosing the model

`LLM_PROVIDER` in `.env` picks the Brain. Both speak the same internal
interface, so everything else is identical either way.

| | `gemini` (default) | `anthropic` |
|---|---|---|
| Runs on | Google's Gemini API | Anthropic's API |
| Default model | `gemini-3.8-flash` | `claude-opus-4-8` |
| Key | `GEMINI_API_KEY` — **free** tier available | `ANTHROPIC_API_KEY` — paid per use |
| Install | nothing extra | `pip install -e ".[llm]"` |

Check the connection before anything else — it looks the model up, which
costs none of your quota:

```text
$ jarvis brain
provider: gemini
model:    gemini-3.8-flash
endpoint: https://generativelanguage.googleapis.com/v1beta
connected: yes — Gemini 3.8 Flash (1,048,576 token context)
Jarvis is ready.
```

Other free-tier models work too: set `LLM_MODEL=gemini-3.5-flash-lite` in
`.env` for a lighter one. Tuning lives under `llm:` in `config/config.yaml`:

- `thinking_level` — `minimal` · `low` (default) · `medium` · `high`, or
  `null` for the model's default. Thinking tokens count against the free
  tier, so low is a good balance. Gemini 2.5 models need `null`.
- `max_retries` — how often a rate-limited request is retried (it waits as
  long as Google asks, up to a minute).
- `timeout`, `temperature`, `max_tokens`.

If you hit rate limits often, lower `agent.pool_size`: fewer agents means
fewer requests in the same minute.

## Run

```bash
jarvis dash        # everything — API, dashboard, Telegram — and open the dashboard
jarvis serve       # the same without opening a browser
                   #   --host 0.0.0.0  accept connections from other machines
                   #   --no-dash       the API only
jarvis phone       # just the Telegram bridge
jarvis run "organize the files in my workspace by extension"   # one task, in the terminal
jarvis brain       # check the model connection
jarvis tools       # what Jarvis can do on this machine
jarvis token       # generate a JARVIS_API_TOKEN
```

`serve` and `dash` run the API, the dashboard and the phone bridge on one
event loop **on purpose**: live agent state exists only in memory, so a
second process would build its own Jarvis and show an emptier one than the
one actually doing the work.

## The dashboard (serving the frontend)

When `serve`/`dash` start, the backend looks for the frontend:

1. `dashboard.web_root` in `config/config.yaml`, if set;
2. otherwise the `frontend/` folder beside this `backend/` folder.

If it finds one, the dashboard is at `http://127.0.0.1:8765/dash/` (and `/`
redirects there). If not, the API runs on its own and `/dash/` explains how
to add the pages. Either way, a copy of the frontend opened anywhere else
can connect — see [../frontend/README.md](../frontend/README.md).

Pages opened from a different origin need permission to call the API.
Anything on `localhost`/`127.0.0.1` (any port) is always allowed; list other
origins in the config. The API token is still required either way:

```yaml
api:
  host: 0.0.0.0                  # listen on the network, not just this PC
  cors_origins:
    - http://192.168.1.20:8080   # a frontend opened on another machine
```

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

Click the microphone in the dashboard once to arm it. It only sends a clip
after you have said something and stopped, and only acts on it if it began
with the wake word. Matching forgives common mishearings ("Travis",
"Jervis") while rejecting near-misses like "Marvin".

> **On the GPU.** Transcription uses the GPU when it can, but CUDA's runtime
> libraries (cuBLAS, cuDNN) install separately from the graphics driver and
> are often missing — which only shows on the first transcription. Jarvis
> notices, falls back to the CPU, and carries on; `small.en` on CPU is
> perfectly usable.

## Agents

```yaml
agent:
  parallel: true
  pool_size: 4      # up to 20
```

The Planner marks which steps depend on which; anything unblocked at the
same moment runs at the same time, up to `pool_size`. Steps that say nothing
about ordering are chained, because a step that quietly needed an earlier
one and ran too early gives a wrong answer. Dependencies may only point at
earlier steps, which makes a deadlock structurally impossible.

## Control it from your phone (Telegram)

The bridge only makes *outbound* HTTPS calls to Telegram, so it needs no
LAN, no port forwarding and no exposed server — any internet works, even a
phone tether.

1. In Telegram, message **@BotFather**, send `/newbot`, copy the token.
2. Put it in `.env`: `TELEGRAM_BOT_TOKEN=123456:ABC...`
3. Turn it on in `config/config.yaml`: `telegram: {enabled: true}`
4. Run `jarvis phone` and message your bot — it replies with your chat id.
   Put that in the config so only you can command it, and restart:
   ```yaml
   telegram:
     enabled: true
     owner_chat_id: 123456789
   ```

Then: type any task; tap **✅ Allow / ⛔ Deny** on dangerous steps;
`/status`, `/task <id>`, `/cancel <id>`, `/tools`, `/approvals`.

For notifications without the bot, use ntfy (`push: {enabled: true, topic:
jarvis-<random>}`) and subscribe to the topic in the ntfy app.

## HTTP API

Every route except `/health` needs `Authorization: Bearer <JARVIS_API_TOKEN>`
(or `?token=` for the event stream). Interactive docs at `/docs`.

```bash
BASE=http://127.0.0.1:8765 ; TOKEN=<your JARVIS_API_TOKEN>

curl -X POST $BASE/tasks -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"request": "summarise notes.txt"}'
curl $BASE/tasks -H "Authorization: Bearer $TOKEN"
curl -N "$BASE/events?token=$TOKEN"                        # live events (SSE)
curl $BASE/approvals -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/approvals/<id> -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"decision": "allow"}'
```

The full list is in [../docs/COMMANDS.md](../docs/COMMANDS.md). Don't expose
the port to the internet; use a VPN such as Tailscale for remote access.

## Configuration model

Four layers, highest precedence first (see `jarvis.config`):

1. **Environment variables** — `JARVIS_` prefix, `__` nesting:
   `JARVIS_API__PORT=9000`, `JARVIS_LOGGING__LEVEL=DEBUG`
2. **Provider shortcuts** — `LLM_PROVIDER`, `LLM_MODEL`, from the
   environment or `.env`
3. **YAML file** — explicit `--config`, `$JARVIS_CONFIG_FILE`,
   `./config/config.yaml`, or the per-user config dir
4. **Coded defaults** — `src/jarvis/config/schema.py`

Secrets (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `JARVIS_API_TOKEN`,
`TELEGRAM_BOT_TOKEN`, `JARVIS_PUSH_TOKEN`) are a separate, environment-only
layer: they cannot be expressed in YAML at all.

## Logging

Console + rotating JSON-lines file (default `%LOCALAPPDATA%\jarvis\Logs\jarvis.jsonl`
on Windows). Every tool call, decision, error and timing carries the task id:

```bash
jq 'select(.context.task_id == "task-...")' jarvis.jsonl   # one task's trail
jq 'select(.level == "ERROR")' jarvis.jsonl                # all errors
```

## Development

```bash
pip install -e ".[dev,api,dash,phone]"
python -m pytest     # the whole loop, both providers, pool, dashboard, voice — all on fakes
ruff check .         # lint
mypy src             # strict type-check
```

No test calls a real model or needs a key: the Gemini brain is tested
against a fake HTTP transport.

## Layout

```
backend/
├── pyproject.toml, .env.example, config/config.example.yaml
├── scripts/        jarvis.ps1 (install/update/start), bootstrap.ps1 (blank PC)
├── tests/
└── src/jarvis/
    ├── core/           domain models (Task, Plan, ToolResult), event bus, redaction
    ├── config/         typed settings (YAML+env) and env-only secrets
    ├── logging/        structured JSON-lines logging with task context
    ├── security/       token auth + safe/confirm permission policy
    ├── brain/          Brain protocol + Gemini (default) and Anthropic providers
    ├── planner/        request → validated dependency graph; revision on failure
    ├── agent/          orchestrator (policy) + agent pool (concurrency) + roster
    ├── tools/          Tool abstraction, registry, gated ToolManager
    ├── files/          sandboxed file manager + file tools
    ├── memory/         SQLite persistence + memory tools
    ├── browser/        Playwright controller + browser tools
    ├── desktop/        Windows controller + desktop tools
    ├── vision/         screenshots + OCR + locate-on-screen tools
    ├── voice/          neural TTS, local Whisper STT, wake-word matching
    ├── dashboard/      event hub + the dashboard's API; finds and serves ../frontend
    ├── notifications/  event → notification service; log/live/push channels
    ├── phone/          Telegram remote-control bridge + HTTP transport
    ├── api/            FastAPI server (CORS for a separately-hosted frontend)
    └── app/            runtime wiring + the `jarvis` CLI
```
