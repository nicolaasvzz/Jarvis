# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

- [README.md](README.md) — what Jarvis does, install, phone setup, HTTP API surface.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map, design rules, decision log.

This file covers only what those two don't: the commands, and the invariants that
span several files.

## Commands

```bash
pip install -e ".[dev,api,phone]"   # everything the test suite needs
pytest                              # whole suite (runs against fakes — no API key, no network)
pytest tests/test_orchestrator.py   # one file
pytest tests/test_orchestrator.py::test_step_failure_retries_then_revises_then_succeeds   # one test
pytest -k approval                  # by name
ruff check .                        # lint (line-length 100; E,F,I,UP,B,SIM)
mypy src                            # strict — new code must type-clean
```

> **In a git worktree, prefix every one of these with `PYTHONPATH=src`.** See below —
> without it you are not testing the code you are editing.

CLI (after install): `jarvis tools` · `jarvis run "<task>"` · `jarvis serve` ·
`jarvis phone` · `jarvis token`.

`pytest` is configured with `asyncio_mode = "auto"`, so async tests need no
`@pytest.mark.asyncio`.

## Invariants

These are load-bearing. Breaking one silently defeats the security model or the
error-handling philosophy.

**1. `ToolManager.execute` is the only path to execution.**
Brain, Planner, and Orchestrator never touch the OS. Everything routes through
[`tools/manager.py`](src/jarvis/tools/manager.py), which is therefore the single
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
([`config/schema.py`](src/jarvis/config/schema.py)). `risk_category=None` means
*never gated*. Adding a destructive tool means setting the category **and** adding
it to the config default list — doing only the first ships an ungated action.

**4. The failure ladder is retry → revise → report.**
[`agent/orchestrator.py`](src/jarvis/agent/orchestrator.py): a failing step retries
up to `agent.max_step_attempts` (default 2), then the Planner is asked to `revise`
the remaining plan up to `agent.max_plan_revisions` (default 2), then the task
fails *with a recorded error and a notification*. A task never crashes the process.

**5. Secrets cannot be expressed in config.**
`AppConfig` (YAML + env) has no secret fields by construction; `Secrets`
([`config/secrets.py`](src/jarvis/config/secrets.py)) reads env/`.env` only and
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

## Layout gotchas

- **`src/` layout + worktrees = silent cross-checkout testing.** The editable
  install resolves `jarvis` to whichever checkout `pip install -e` was last run in
  — in practice the main one at `C:\Users\nicol\jarvis`, which sits on its own
  branch with its own uncommitted changes. Running `pytest` from a worktree
  therefore executes *this* worktree's tests against *that* checkout's source, and
  the mismatched failures look like real bugs. Always run
  `PYTHONPATH=src python -m pytest` in a worktree, and confirm with:

  ```bash
  PYTHONPATH=src python -c "import jarvis; print(jarvis.__file__)"
  ```

  If that path isn't the directory you're editing, stop and fix it before trusting
  any test result.
- **Optional extras are gated at runtime.** Browser, desktop, and vision tools
  register only when their imports succeed
  ([`app/runtime.py::_register_optional_tools`](src/jarvis/app/runtime.py)).
  Missing extras log a line and disable those tools; they are never an error.
  `jarvis tools` shows what actually registered on this machine.
- **`ToolRegistry.register` raises on duplicate names.** Tool names are global.
- **The `tool` decorator derives its JSON schema from type hints**
  ([`tools/base.py`](src/jarvis/tools/base.py)). A parameter named `context` is
  special: declaring it opts the tool into `ToolContext` injection rather than
  becoming an LLM-visible argument.
- **Windows is the target, but development works anywhere** — paths come from
  `platformdirs`, and the suite runs against fakes.

## Testing approach

Tests use test doubles, not mocks of our own classes. [`tests/helpers.py`](tests/helpers.py)
provides `ScriptedBrain`, which replays canned `BrainResponse`s in order and records
the calls it received. The Telegram bridge and push channel are testable the same
way via the `HttpTransport` protocol — no bot, account, or network in CI. New
integrations should follow that shape: define the protocol, fake it in tests.
