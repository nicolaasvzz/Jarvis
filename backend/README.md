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
| `run_command` | A quick command, run out of sight — **waits for Allow** — returning its output |
| `terminal_open` | Opens a live terminal on the dashboard's **Terminal** tab, optionally running a command in it (that **waits for Allow**) |
| `terminal_write` | Types into an open terminal — a command, an answer, or a key like ctrl+c — **waits for Allow**, then returns the screen |
| `terminal_read` / `terminal_list` | What a terminal's screen shows; which terminals are open and what each is for |
| `read_workspace_file` | Reads a file in the workspace |

Commands run in PowerShell on Windows (bash elsewhere), starting in
`workspace/`. Terminals are real shells in a pseudo-terminal (ConPTY via
`pywinpty`), so colours, prompts and REPLs work; you can open your own from
the Terminal tab and type in any of them without approval. Each terminal
keeps its title, purpose and a log of what was typed and by whom. All of it
goes into Jarvis's prompt on every request, so *"carry on in the npm one"*
lands in the right terminal. Terminals live as long as `jarvis.py` does.

## Settings (`.env`)

| Key | Default | |
|---|---|---|
| `GEMINI_API_KEY` | — | Required. Free from AI Studio. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Fastest. `gemini-3.8-flash` is smarter. |
| `GEMINI_THINKING` | model default | `minimal` / `low` / `medium` / `high` |
| `JARVIS_API_TOKEN` | made on first run | The dashboard's password |
| `HOME_LOCATION` | — | Where "the weather" means |
| `AUTO_APPROVE` | `false` | `true` runs commands, and lets Jarvis type into terminals, without asking |
| `JARVIS_WORKSPACE` | `workspace` | Where commands start |
| `JARVIS_VOICE` | `en-GB-RyanNeural` | `off` for silence |
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
