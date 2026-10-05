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
| [`frontend/`](frontend/) | The dashboard: a reactive particle core, a Mothership control centre (custom controls, projects, every request), live terminals you share with Jarvis, and an approvals page. It installs on your phone as an app. Plain HTML/JS — reusable for other projects, see [frontend/API.md](frontend/API.md). |
| [`tradebot/`](tradebot/) | **The TradeBot**, already set up as a Mothership project: a trading bot on Alpaca's free paper (pretend) money that builds and tests its own sets of indicators, learns from every backtest and can watch the news. See [The TradeBot](#the-tradebot) below and [tradebot/README.md](tradebot/README.md). |

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

## The TradeBot

A fresh Jarvis comes with the TradeBot on its Mothership: **Mothership →
TradeBot** has its live status (equity, positions, trades, what it learned),
every report it has written, and buttons for its modes:

- **Champ-set builder** — tries indicator sets until it finds the best one.
- **Learning mode** — backtests over and over, keeping only changes that hold up.
- **Paper trading** — trades the best set on your Alpaca paper account.
- **Test mode** — checks every part of the bot without trading.
- **Update Jarvis and the bot** — pulls the latest version.

Each mode can also run **news research** alongside it: Gemini reads the latest
market news, and (if you choose) the bot trades it on paper, betting more the
likelier Gemini thinks the move is.

To set it up, make a free account at [Alpaca](https://alpaca.markets), create
a **paper trading** API key, copy `tradebot/.env.example` to `tradebot/.env`
and fill in `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and `GEMINI_API_KEY` (the
same key as Jarvis's). The first press of a mode installs what the bot needs.
It trades pretend money unless you deliberately change that.

**What stays on your computer:** keys (`.env`), your Mothership and request
history (`backend/data/`), and everything the bot learns or holds: backtest
lessons, lab results, downloaded prices, the news it read, its reports and
trades. None of it is committed.

## On your phone, from anywhere

The whole dashboard works on a phone: ask, approve, terminals (with the keys
a phone keyboard lacks), controls, projects and connections, with a tab bar
along the bottom. It installs to your home screen like an app. To reach it
away from home, Jarvis uses **[Tailscale](https://tailscale.com/download)**:
a free, private network between your own devices. Nothing is opened to the
internet, and Jarvis keeps listening on this PC only.

1. Install Tailscale on this PC and sign in.
2. Install Tailscale on your phone (App Store / Google Play) and sign in with
   the same account.
3. In the dashboard, open **Mothership → Connections → Your phone** and press
   **Turn it on**. That runs `tailscale serve --bg 8765` in a terminal, which
   gives Jarvis a private `https://<your-pc>.<tailnet>.ts.net` address. The
   first time, it prints a link for allowing https in your Tailscale
   account. Open it, and the command finishes.
4. Press **Show QR code** and scan it with your phone. It opens logged in.
   Then **iPhone:** Share → *Add to Home Screen*; **Android:** ⋮ →
   *Install app*.

The card ticks off each step as it finds it done, and `jarvis.py` prints the
phone link at start-up. The QR code and link contain your token. If someone
else gets hold of them, **Make a new token** (same page) logs every device
out.

To change its personality or rules, edit `backend/persona.md` — no restart
needed. Every setting is in `backend/.env` (see `.env.example`), e.g.
`HOME_LOCATION=Cape Town` so it knows where "the weather" means, or
`GEMINI_MODEL=gemini-3.8-flash` for a smarter, slower model.

## Safety

Jarvis can run commands on your computer, so:

- every command waits for **Allow** on the dashboard (set `AUTO_APPROVE=true`
  in `.env` only if you trust every command a model might write);
- the dashboard needs a token (made for you on first run and kept in `.env`);
- it only listens on this PC unless you set `JARVIS_HOST=0.0.0.0`. Your phone
  reaches it through Tailscale, which only your own signed-in devices can
  use. Don't put Jarvis on the open internet: its token is all that stands
  between a stranger and your terminals.

`.env` holds your key and token and is never committed to git.

The earlier, much larger version of Jarvis (planner, agent pool, Telegram,
local models) is kept under the git tag **`full-agent-v1`**.
