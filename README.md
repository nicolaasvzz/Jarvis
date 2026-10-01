# Jarvis

A personal AI assistant with a live sci‑fi dashboard. Type to it and it
answers — checking live weather and news, searching the web, and running
terminal commands on your PC when you allow it. The thinking is done by
Google's **Gemini** (the fast Flash‑Lite model), and a **free** API key is
all it needs.

```
browser ──▶ frontend (dashboard) ──HTTP+token──▶ backend/jarvis.py ──▶ Gemini
                                                    │
                                   weather · news · web · terminal (you approve)
```

| Folder | What it is |
|---|---|
| [`backend/`](backend/) | **One file, `jarvis.py`**: talks to Gemini, runs the tools, serves the dashboard. Plus `persona.md` (who Jarvis is), `.env` (your key and settings) and `start.bat`. |
| [`frontend/`](frontend/) | The dashboard: a reactive particle core, live terminals you share with Jarvis, and a phone page for approvals. Plain HTML/JS — reusable for other projects, see [frontend/API.md](frontend/API.md). |

## Start it

1. Install **Python 3.11+** from https://python.org (tick *Add Python to PATH*).
2. Get a **free Gemini key**: https://aistudio.google.com/apikey → sign in →
   **Create API key**.
3. Download this repo (**Code → Download ZIP**, or `git clone`) and
   double‑click **`backend/start.bat`**. The first run installs what it needs
   and creates `backend/.env` — paste your key after `GEMINI_API_KEY=`, save,
   and double‑click `start.bat` again.

Your browser opens the dashboard. Type at the bottom — *"what's the weather
in Cape Town?"*, *"top news today"*, *"what's in my Downloads folder?"*.

On macOS/Linux, or by hand:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python jarvis.py
```

## What Jarvis can do

- **Answer and chat** — questions, explanations, writing, maths.
- **Live information** — weather and forecasts (Open‑Meteo), news
  headlines (Google News), web search and reading pages. No extra keys.
- **Terminal commands** — Jarvis writes the command, the dashboard shows it
  with **Allow / Deny**, and only then does it run. Quick commands return
  their output; long ones (installs, builds, servers) open **their own
  terminal window** and keep going, and Jarvis tells you when they finish.
- **Speak** its answers in a British voice (`edge-tts`, free).

To change its personality or rules, edit `backend/persona.md` — no restart
needed. Every setting is in `backend/.env` (see `.env.example`), e.g.
`HOME_LOCATION=Cape Town` so it knows where "the weather" means, or
`GEMINI_MODEL=gemini-3.8-flash` for a smarter, slower model.

## Safety

Jarvis can run commands on your computer, so:

- every command waits for **Allow** on the dashboard (set `AUTO_APPROVE=true`
  in `.env` only if you trust every command a model might write);
- the dashboard needs a token (made for you on first run and kept in `.env`);
- it only listens on this PC unless you set `JARVIS_HOST=0.0.0.0`.

`.env` holds your key and token and is never committed to git.

The earlier, much larger version of Jarvis (planner, agent pool, Telegram,
local models) is kept under the git tag **`full-agent-v1`**.
