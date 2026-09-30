# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Two halves that only talk over HTTP:

- **`backend/jarvis.py`** — the whole backend in one file: a Gemini tool loop
  (`GEMINI_MODEL`, `gemini-3.5-flash-lite` by default), a handful of tools
  (weather, news, web search/read, terminal commands, job status, workspace
  file reads), and the FastAPI routes the dashboard uses. `persona.md` is the
  system prompt; `.env` holds settings and secrets.
- **`frontend/`** — static HTML/JS/CSS dashboard, no build step, reusable by
  other projects. [`frontend/API.md`](frontend/API.md) is the contract between
  them.

The old multi-module system (planner, agent pool, Telegram, local models) was
deliberately removed; it lives under the git tag `full-agent-v1`. Don't
reintroduce it piecemeal — and the user asked for no Ollama/local-model
worker, only terminal commands.

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

1. **Terminal commands need approval.** `run_command` calls `_approve`, which
   waits for Allow/Deny from the dashboard unless `AUTO_APPROVE=true`. Any new
   tool that changes the machine must be `risky=True` and go through
   `_approve` too.
2. **Tools return dicts, never raise out.** `_call_tool` turns exceptions into
   `{"error": ...}` so Gemini can react; a failed tool must not end the task.
3. **Gemini's model turns go back verbatim.** Gemini 3 requires the
   `thoughtSignature` parts of a function-calling turn to be returned exactly
   as received — `_converse` appends the model's `parts` untouched.
4. **API routes are `async def`.** Plain `def` routes run on a worker thread:
   no event loop to start a task on, and resolving an approval future there
   is not thread-safe (this was a real bug).
5. **Secrets only in `.env`**, which is gitignored. Never log the key or token.
6. **Frontend reaches the backend only through named routes** in
   `frontend/js/lib/connection.js`; change a route's shape → update
   `frontend/API.md`.
7. **Logging never breaks work.** `Hub.emit`'s console print is suppressed on
   error (a cp1252 console once killed a job launch).

## Gotchas

- Google Search grounding is **not** on Gemini's free tier — hence the
  key-free weather/news/search tools instead.
- Long commands (`new_window=true`) run `workspace/jobs/<name>/job.ps1` in a new
  PowerShell window with `-NoExit`; completion is detected by a `.done` file,
  not by the process exiting.
- `.ps1` files are written with a UTF-8 BOM so Windows PowerShell 5.1 reads
  non-ASCII correctly.
