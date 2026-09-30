# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

- [README.md](README.md) — what Jarvis is, the repo's two halves, quick start.
- [backend/README.md](backend/README.md) — install, model choice, config, phone, HTTP API.
- [frontend/README.md](frontend/README.md) — the web UI, running it on its own, reusing it.
- [frontend/API.md](frontend/API.md) — the backend routes the UI depends on (keep it true).
- [backend/docs/ARCHITECTURE.md](backend/docs/ARCHITECTURE.md) — module map, design rules, decision log.
- [backend/docs/COMMANDS.md](backend/docs/COMMANDS.md) — the full runnable command reference.

This file covers only what those don't: the commands, and the invariants that
span several files.

## Two halves

- **`backend/`** — the Python package (`backend/src/jarvis`), its tests,
  `pyproject.toml`, `.env`, `config/`, and the install scripts. **Every Python
  command below runs from `backend/`**, which is also where `jarvis` finds
  `.env` and `config/config.yaml` at runtime.
- **`frontend/`** — static HTML/JS/CSS, no build step, reusable by other projects. The backend serves it at
  `/dash/` when it sits beside `backend/` (`dashboard/frontend.py::find_frontend`);
  otherwise the backend runs headless and a separately-hosted copy connects over
  HTTP (CORS: localhost always, other origins via `api.cors_origins`).

Keep them separable — each is handed to people on its own:

- The backend must start and pass its tests **without** `frontend/`. Tests that
  need pages build a fake frontend in `tmp_path` (`tests/test_dashboard.py::fake_frontend`).
- Frontend code reaches the backend only through named routes in
  `js/lib/connection.js` (`HudConnection.endpoint("snapshot")`, `Hud.route(...)` in
  modules). A literal `fetch("/dash/api/...")` works when the backend serves the page
  and silently breaks every standalone copy and every other project using the UI.
- Changing what a dashboard route returns means updating `frontend/API.md` too —
  it is the contract other backends implement.
- `docs/` lives in `backend/docs/`, so the backend folder is complete on its own.

## Commands

```bash
cd backend
pip install -e ".[dev,api,dash,phone]"   # everything the test suite needs
pytest                              # whole suite (runs against fakes — no API key, no network)
pytest tests/test_orchestrator.py   # one file
pytest tests/test_orchestrator.py::test_step_failure_retries_then_revises_then_succeeds   # one test
pytest -k approval                  # by name
ruff check .                        # lint (line-length 100; E,F,I,UP,B,SIM)
mypy src                            # strict — new code must type-clean
```

> **In a git worktree, prefix every one of these with `PYTHONPATH=src`.** See below —
> without it you are not testing the code you are editing.

CLI (after install, from `backend/`): `jarvis dash` · `jarvis serve` · `jarvis tools` ·
`jarvis run "<task>"` · `jarvis brain` · `jarvis phone` · `jarvis token`.

`pytest` is configured with `asyncio_mode = "auto"`, so async tests need no
`@pytest.mark.asyncio`.

## Invariants

These are load-bearing. Breaking one silently defeats the security model or the
error-handling philosophy.

**1. `ToolManager.execute` is the only path to execution.**
Brain, Planner, and Orchestrator never touch the OS. Everything routes through
[`tools/manager.py`](backend/src/jarvis/tools/manager.py), which is therefore the single
place permission checks, approval waits, and audit logging live. A new capability
that calls `subprocess`/`open()`/the network directly from anywhere else is a bug,
not a shortcut.

**2. Tools never raise out; they return `ToolResult`.**
`execute` converts every exception — including unexpected ones — into
`ToolResult.failure`. Callers decide what to do. Don't add `raise` paths that
escape the manager, and don't `try/except` around `execute` expecting throws.

**3. Danger requires two independent opt-ins.**
A tool declares a `risk_category` string; `PermissionPolicy` gates it only if that
string is in `security.require_confirmation`
([`config/schema.py`](backend/src/jarvis/config/schema.py)). `risk_category=None` means
*never gated*. Adding a destructive tool means setting the category **and** adding
it to the config default list — doing only the first ships an ungated action.

**4. The failure ladder is retry → revise → report.**
[`agent/orchestrator.py`](backend/src/jarvis/agent/orchestrator.py): a failing step retries
up to `agent.max_step_attempts` (default 2), then the Planner is asked to `revise`
the remaining plan up to `agent.max_plan_revisions` (default 2), then the task
fails *with a recorded error and a notification*. A task never crashes the process.

**5. Secrets cannot be expressed in config.**
`AppConfig` (YAML + env) has no secret fields by construction; `Secrets`
([`config/secrets.py`](backend/src/jarvis/config/secrets.py)) reads env/`.env` only and
wraps values in `SecretStr`. Never add a secret field to a config section — that
makes committing one possible again.

**6. No hardcoded tunables.**
Anything adjustable belongs in `config/schema.py` with a default. Config sections
use `extra="forbid"`, so a YAML typo is a loud startup error rather than a silently
ignored key.

**7. Modules depend downward only.**
Any module may use `jarvis.config` and `jarvis.logging`. Otherwise depend on the
*interface* of modules beneath you in the architecture diagram, never their
internals — `Brain` is a Protocol for exactly this reason.

**8. Provider selection lives only in `brain/factory.py`.**
`build_brain()` is the one place that picks an implementation, and its provider
imports are deliberately local so selecting Gemini never imports `anthropic` and
never asks for an Anthropic key. The default provider is **Gemini**
(`gemini-3.8-flash`, which a free AI Studio key covers), not Anthropic.
Everything downstream sees only the `Brain` protocol — don't import a concrete
brain anywhere else.

## Layout gotchas

- **`src/` layout + worktrees = silent cross-checkout testing.** The editable
  install resolves `jarvis` to whichever checkout `pip install -e` was last run in
  — in practice the main one (the repository root), which sits on its own
  branch and may be ahead of or behind the branch you are editing. Running
  `pytest` from a worktree therefore executes *this* worktree's tests against
  *that* checkout's source, and the mismatched failures look like real bugs.
  Always run `PYTHONPATH=src python -m pytest` from the worktree's `backend/`,
  and confirm with:

  ```bash
  PYTHONPATH=src python -c "import jarvis; print(jarvis.__file__)"
  ```

  If that path isn't the directory you're editing, stop and fix it before trusting
  any test result.
- **Optional extras are gated at runtime.** Browser, desktop, and vision tools
  register only when their imports succeed
  ([`app/runtime.py::_register_optional_tools`](backend/src/jarvis/app/runtime.py)).
  Missing extras log a line and disable those tools; they are never an error.
  `jarvis tools` shows what actually registered on this machine.
- **`ToolRegistry.register` raises on duplicate names.** Tool names are global.
- **The `tool` decorator derives its JSON schema from type hints**
  ([`tools/base.py`](backend/src/jarvis/tools/base.py)). A parameter named `context` is
  special: declaring it opts the tool into `ToolContext` injection rather than
  becoming an LLM-visible argument.
- **Windows is the target, but development works anywhere** — paths come from
  `platformdirs`, and the suite runs against fakes.

## Testing approach

Tests use test doubles, not mocks of our own classes. [`tests/helpers.py`](backend/tests/helpers.py)
provides `ScriptedBrain`, which replays canned `BrainResponse`s in order and records
the calls it received. The Telegram bridge and push channel are testable the same
way via the `HttpTransport` protocol — no bot, account, or network in CI. New
integrations should follow that shape: define the protocol, fake it in tests.

**No test talks to a real model.** The Gemini brain is tested against
`httpx.MockTransport` ([`tests/test_gemini_brain.py`](backend/tests/test_gemini_brain.py)),
so the suite needs no key and no network, and spends none of the free tier's
quota. Keep it that way: a live call belongs in `jarvis brain`, not in CI.
