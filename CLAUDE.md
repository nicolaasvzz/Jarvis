# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Two halves that only talk over HTTP:

- **`backend/jarvis.py`** — the whole backend in one file: a Gemini tool loop
  (`GEMINI_MODEL`, `gemini-3.5-flash-lite` by default), a handful of tools
  (weather, news, web search/read, terminal commands, live dashboard
  terminals, workspace file reads), and the FastAPI routes the dashboard uses. `persona.md` is the
  system prompt; `.env` holds settings and secrets.
- **`frontend/`** — static HTML/JS/CSS dashboard, no build step, reusable by
  other projects. [`frontend/API.md`](frontend/API.md) is the contract between
  them.

The old multi-module system (planner, agent pool, Telegram, local models) was
deliberately removed; it lives under the git tag `full-agent-v1`. Don't
reintroduce it piecemeal — and the user asked for no Ollama/local-model
worker, only terminal commands. The Files and Agent Office pages were removed
too, at the user's request: Jarvis works through terminals, not files.

## Commands

```bash
cd backend
pip install -r requirements.txt pytest pytest-asyncio ruff mypy
python jarvis.py                                          # run it
python -m pytest test_jarvis.py                           # fake Gemini — no key, no quota
ruff check --line-length 100 --select E,F,I,UP,B,SIM .    # lint
python -m mypy --strict --ignore-missing-imports jarvis.py
```

## Invariants

1. **Terminal commands need approval.** `run_command`, `terminal_open` (with
   a command) and `terminal_write` call `_approve`, which waits for Allow/Deny
   from the dashboard unless `AUTO_APPROVE=true`. Any new tool that changes
   the machine must be `risky=True` and go through `_approve` too. What the
   user types into a terminal on the dashboard is never gated. The only
   skips, both user-requested: `is_safe` read-only commands (`free=` on
   `_approve`, off with `ALLOW_SAFE_COMMANDS=false`) and terminals the user
   marked trusted. Keep `SAFE_COMMANDS` strictly read-only, and keep
   `NEVER_SAFE` rejecting anything that chains, redirects or substitutes.
2. **Tools return dicts, never raise out.** `_call_tool` turns exceptions into
   `{"error": ...}` so Gemini can react; a failed tool must not end the task.
3. **Gemini's model turns go back verbatim.** Gemini 3 requires the
   `thoughtSignature` parts of a function-calling turn to be returned exactly
   as received — `_converse` appends the model's `parts` untouched.
4. **API routes are `async def`.** Plain `def` routes run on a worker thread:
   no event loop to start a task on, and resolving an approval future there
   is not thread-safe (this was a real bug).
5. **Secrets only in `.env`**, which is gitignored. Never log the key or token.
   The Connections tab writes `.env` (`Jarvis.change_connections`), but only
   names in `Jarvis.EDITABLE`; keys go back to the page masked (`mask()`),
   and events carry names, never values. Only the token route returns a
   whole secret (the new token, to the page that asked).
6. **Frontend reaches the backend only through named routes** in
   `frontend/js/lib/connection.js`; change a route's shape → update
   `frontend/API.md`.
7. **Logging never breaks work.** `Hub.emit`'s console print is suppressed on
   error (a cp1252 console once killed a job launch).

## Gotchas

- **Brains** (`BRAINS`): Gemini (native loop, `_converse_gemini`), Claude
  (`_converse_claude`, the `anthropic` SDK on Jarvis's shared httpx client —
  keep `anthropic<1`, since 1.x uses httpx2), and OpenAI-format ones
  (`_converse_openai`: OpenAI, Groq, OpenRouter, DeepSeek). `self.chat` keeps
  plain-text turns so any brain continues the thread. Switching is manual
  only — the user chose that; don't add silent fallback between brains.
- `reload_settings()` copies a fresh `Settings.load()` into the running
  settings in place (everything but `RESTART_ONLY`), so routes and closures
  see changes at once. Restart (`serve()` → `main()` re-runs `jarvis.py`)
  is only for host/port and closes terminals.
- Google Search grounding is **not** on Gemini's free tier — hence the
  key-free weather/news/search tools instead.
- Dashboard terminals (`Terminal`) are real shells in a pseudo-terminal:
  `pywinpty` (ConPTY) on Windows, `ptyprocess` elsewhere. A reader thread
  pumps output into the event loop with `call_soon_threadsafe`, onto a
  `pyte` virtual screen: Jarvis reads it as plain text, and a browser opening
  the tab is painted from it (`Terminal.redraw`).
- A terminal's size is fixed when it opens; the page scales its font to fit.
  Don't add resizing: pyte doesn't reflow, so shrinking cuts lines, and
  ConPTY's absolute cursor moves make raw output from one width garble at
  another.
- The user's typed commands are logged from the screen on Enter, after
  waiting for the echo (`Terminal.keys`). Keys reach the shell before it
  draws them.
- Ctrl+C written into ConPTY (`\x03`, also pywinpty's `sendintr`) does
  **not** interrupt every program: a Python script or `Start-Sleep` keeps
  running. `Terminal.interrupt` sends it, then after ~3 s ends the processes
  the shell started (`end_children`, psutil), keeping the shell. Stop and
  Restart on controls go through it.
- Press Enter in a PTY with `\r`, not `\r\n`: PowerShell reads the `\n` as
  a second line and shows a `>>` continuation prompt.
- Shells start with a prompt wrapper (`PS_PROMPT_MARK`, or `PROMPT_COMMAND`
  for bash) that prints an invisible OSC 633 mark with the last exit status.
  That is how a command's finish and failure are known (`Terminal._finished`)
  — long ones get a spoken notice, failed ones light up Explain. A command
  "starts" only when Enter is pressed on a line beginning with the prompt.
- `backend/data/` (gitignored) is the user's own: `mothership.json`
  (controls and projects), `history.json` (finished requests), `briefs/`.
  `Mothership` re-reads `mothership.json` whenever its mtime changes — Claude
  finishing a control edits that file directly, so keep that working. Tests
  must pass `data_dir` inside `tmp_path` (`make()` in the tests does) and
  never touch the real folder.
- A control the user presses runs without approval (they chose it); the
  same control pressed by Jarvis (`run_control`) asks unless `trusted`.
- **Phone access is Tailscale**, never a public tunnel: Jarvis stays on
  `127.0.0.1` and `tailscale serve --bg <port>` gives it an https address on
  the user's own tailnet (https is what lets a phone install the app and use
  the mic). `tailscale_status()` only reads `tailscale status/serve status
  --json`; the Phone card's button runs `tailscale serve` in a terminal
  because the first run prints a link the user must open. Phone routes never
  return the token. The page adds its own.
- On a phone (≤ 760px) pages scroll instead of filling a fixed HUD, and
  `hud.js` adds a bottom tab bar. A terminal opened from a phone sends no
  size, so Jarvis's own terminals don't shrink to phone width.
- Tests never start a real shell: `Jarvis.spawn_shell` is swapped for
  `FakeShell` in `test_jarvis.py`.

## Pull requests

- The GitHub repo is `nicolaasvzz/Jarvis` (it was `Jarvis.`, with a trailing dot;
  the old name still redirects, and this checkout's `origin` may use it). Its
  default branch is `claude/jarvis-ai-assistant-9hhbcu`, not `main`.
- On Windows `gh` may not be on PATH in a fresh shell; it installs to
  `C:\Program Files\GitHub CLI\gh.exe` (PowerShell: `& "C:\Program Files\GitHub CLI\gh.exe" ...`).
