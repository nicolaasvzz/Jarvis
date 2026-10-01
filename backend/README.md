# Jarvis — backend

Everything is in **`jarvis.py`**: the conversation with Gemini, the tools it
can use, and the HTTP API the dashboard talks to.

| File | What it's for |
|---|---|
| `jarvis.py` | The assistant. Run `python jarvis.py`. |
| `persona.md` | Who Jarvis is and how it behaves — the system prompt. Edit freely; it's re‑read on every request. |
| `.env` | Your settings and secrets (created from `.env.example` on first run). Never committed. |
| `requirements.txt` | `fastapi`, `uvicorn`, `httpx`; `pywinpty` and `pyte` for the live terminals; `psutil` and `edge-tts` are optional. |
| `start.bat` | Windows: double‑click to set up and start. |
| `test_jarvis.py` | Tests against a fake Gemini: `python -m pytest test_jarvis.py`. |
| `workspace/` | Where terminal commands and terminals start. |

## Run

```bash
pip install -r requirements.txt
python jarvis.py
```

First run: it copies `.env.example` to `.env` and asks for your free
`GEMINI_API_KEY` (https://aistudio.google.com/apikey). Once the key is in,
it makes a dashboard token, saves it to `.env`, and opens
`http://127.0.0.1:8765/dash/` with you logged in.

## How a request flows

1. You type in the dashboard → `POST /dash/api/command`.
2. `jarvis.py` sends it to Gemini (`GEMINI_MODEL`, Flash‑Lite by default) with
   `persona.md` as the system prompt and the tools below declared.
3. If Gemini asks for a tool, `jarvis.py` runs it and sends the result back,
   until Gemini answers in words (at most 10 rounds).
4. Every step is an event on the live stream, so the dashboard's sphere,
   feed move as it works; the answer appears in the reply panel
   and, with `edge-tts` installed, is spoken.

| Tool | What it does |
|---|---|
| `get_weather` | Current weather + 3‑day forecast (Open‑Meteo, no key) |
| `get_news` | Headlines, top or by topic (Google News RSS) |
| `web_search` / `read_webpage` | DuckDuckGo results; a page's text (public sites only) |
| `run_command` | A quick command, run out of sight — **waits for Allow** unless it is read-only — returning its output |
| `terminal_open` | Opens a live terminal on the dashboard's **Terminal** tab, optionally running a command in it (that **waits for Allow**) |
| `terminal_write` | Types into an open terminal — a command, an answer, or a key like ctrl+c — **waits for Allow** (not in a trusted terminal), then returns the screen |
| `terminal_read` / `terminal_list` | What a terminal's screen shows; which terminals are open and what each is for |
| `run_control` | Presses one of your Mothership controls — **waits for Allow** unless you marked it trusted |
| `add_idea` | Notes an idea on one of your Mothership projects |
| `read_workspace_file` | Reads a file in the workspace |

Commands run in PowerShell on Windows (bash elsewhere), starting in
`workspace/`. Terminals are real shells in a pseudo-terminal (ConPTY via
`pywinpty`), so colours, prompts and REPLs work; you can open your own from
the Terminal tab and type in any of them without approval. Each terminal
keeps its title, purpose and a log of what was typed and by whom. All of it
goes into Jarvis's prompt on every request, so *"carry on in the npm one"*
lands in the right terminal. Terminals live as long as `jarvis.py` does.

**Less asking.** Read-only commands (`git status`, `git log`, `dir`, `ls`,
`pwd`, `where …`, anything `--version`, …) run without asking; anything that
chains, redirects or substitutes (`;`, `|`, `>`, `$(…)`) never counts. On
the Terminal tab, **Jarvis types freely** trusts one terminal so Jarvis can
type there without asking. It's off for every new terminal.

**When commands finish.** Each shell's prompt marks every finished command
with its exit status, invisibly. A failed command lights up **Explain the
error** on the Terminal tab. One that took `NOTIFY_AFTER_SECONDS` or longer,
which Jarvis wasn't already watching, gets a short spoken word from Jarvis on
how it went.

**Start-up terminals.** Copy `terminals.example.json` to `terminals.json`
(not committed) and list the terminals to open whenever Jarvis starts, each
with a `title`, `purpose` and optional `command`. You wrote those commands
yourself, so they run without asking.

**The Mothership** (`/dash/mothership.html`) is the control centre. Every
request is kept — in `data/history.json`, so restarts don't lose them — and
shown as a stack you can click into. **Controls** are buttons for anything:
run a command (in a project's folder), ask Jarvis something, open a page —
or an *idea* that isn't built yet. Jarvis knows them all, so *"make it rain
in BeamNG"* presses the matching control. **Build with Claude** opens Claude
Code in a terminal with a brief; when it works, Claude switches the control
on by editing `data/mothership.json`, which Jarvis re-reads by itself.
**Projects** (a trading bot, a brand, …) get a page each with a live view of
their status file (any JSON — numbers, curves and tables are drawn
automatically), their controls, an ideas board and their terminals. A fresh
install starts from `mothership.example.json`.

**From your phone.** `approve.html` is an Allow/Deny page for a phone. Set
`JARVIS_HOST=0.0.0.0` and `jarvis.py` prints its address on your Wi-Fi at
start-up, token included. Anyone who has that link can approve commands, so
treat it like a password.

## Brains and connections

Jarvis can think with **Gemini** (free tier; up to three keys, e.g. from
different Google accounts), **Claude** (Anthropic, through its SDK; paid —
Opus 5.5 by default, Sonnet 5.5 or Haiku 4.5 are cheaper), or anything that
speaks the OpenAI chat format: **OpenAI**, **Groq** (free tier, fast),
**OpenRouter** (many models) and **DeepSeek**. Every brain runs the same
tools, through the same approvals, and picks up the same conversation.

**Mothership → Connections** is where they're managed: paste a key, pick a
model, press **Test** (a model lookup — no tokens spent), **Use this brain**.
Voice and listening providers and dashboard access (token, phone access,
port) live there too. Everything is written to `.env` and applied **live** —
open terminals and the conversation carry on. Only the host and port need a
restart, and the page offers one (it closes open terminals). Keys only ever
come back masked (`…1a2b`). Jarvis doesn't switch brains by itself: when one
runs out, the error says so and you switch.

## Settings (`.env`)

| Key | Default | |
|---|---|---|
| `JARVIS_BRAIN` | `gemini` | `gemini`, `anthropic`, `openai`, `groq`, `openrouter`, `deepseek` |
| `GEMINI_API_KEY` (`_2`, `_3`) | — | Free from AI Studio; `GEMINI_KEY` = which one is in use |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | — / `claude-opus-5-5` | Claude |
| `GROQ_…`, `OPENROUTER_…`, `DEEPSEEK_…`, `OPENAI_MODEL` | — | The other brains: `_API_KEY` and `_MODEL` each |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Fastest. `gemini-3.8-flash` is smarter. |
| `GEMINI_THINKING` | model default | `minimal` / `low` / `medium` / `high` |
| `JARVIS_API_TOKEN` | made on first run | The dashboard's password |
| `HOME_LOCATION` | — | Where "the weather" means |
| `AUTO_APPROVE` | `false` | `true` runs commands, and lets Jarvis type into terminals, without asking |
| `ALLOW_SAFE_COMMANDS` | `true` | Read-only commands run without asking; `false` asks for those too |
| `NOTIFY_AFTER_SECONDS` | `20` | A command this long gets a "how it went" from Jarvis; `0` = never |
| `STARTUP_TERMINALS` | `terminals.json` | Terminals to open when Jarvis starts |
| `JARVIS_DATA` | `data` | Your Mothership, request history and Claude briefs (not committed) |
| `JARVIS_WORKSPACE` | `workspace` | Where commands start |
| `JARVIS_VOICE` | `en-GB-RyanNeural` | Edge voice name; `off` for silence |
| `JARVIS_VOICE_PROVIDER` | `edge` | `openai` for ChatGPT's voices (needs `OPENAI_API_KEY`; falls back to `edge`) |
| `JARVIS_VOICE_STYLE` | deep, refined British butler | plain-words accent/tone for the OpenAI voice |
| `JARVIS_LISTEN_PROVIDER` | `browser` | `browser` (Chrome/Edge's own speech recognition, free), `whisper` (Whisper on this computer: free, private, any browser; needs `faster-whisper`), `wispr` (Wispr Flow, needs `WISPR_API_KEY`), or `off` |
| `JARVIS_LISTEN_LANGUAGE` | `en-GB` | what you speak, for the `browser` option |
| `WHISPER_MODEL` | `small.en` | Whisper size: `base.en` faster, `medium.en` more accurate |
| `WISPR_API_KEY` / `WISPR_LANGUAGE` | / `en` | Wispr Flow key and language hint |
| `OPENAI_API_KEY` | | key for the OpenAI voice |
| `OPENAI_TTS_MODEL` / `OPENAI_TTS_VOICE` | `gpt-4o-mini-tts` / `onyx` | OpenAI voice model and voice |
| `NEWS_COUNTRY` / `NEWS_LANGUAGE` | `US` / `en` | Google News edition |
| `JARVIS_HOST` / `JARVIS_PORT` | `127.0.0.1` / `8765` | `0.0.0.0` to allow other devices |
| `JARVIS_CORS_ORIGINS` | — | Extra web origins for a separately hosted dashboard |
| `JARVIS_OPEN_BROWSER` | `true` | Open the dashboard on start |

Real environment variables override `.env`.

## API

The dashboard's routes (`/dash/api/*`) and the plain REST ones (`/tasks`,
`/approvals`, `/events`, `/system`, …) are described in
[`../frontend/API.md`](../frontend/API.md); interactive docs are at
`http://127.0.0.1:8765/docs`. Every route but `/health` needs
`Authorization: Bearer <JARVIS_API_TOKEN>` (or `?token=` for event streams).

```bash
curl -X POST http://127.0.0.1:8765/tasks -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"request": "weather in Paris?"}'
```
