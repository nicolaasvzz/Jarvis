---
name: jarvis-dev
description: Procedural workflows for the Jarvis personal-assistant repo — verifying a change correctly inside a git worktree, adding a new tool end to end, and adding a new Brain provider. Use this whenever working in the Jarvis codebase on anything beyond a one-line edit: adding or changing a capability the assistant can perform, wiring something into the tool registry, touching risk categories or the permission policy, adding or swapping an LLM provider, or running the test suite. Reach for it before running pytest in this repo at all — the obvious invocation silently tests a different checkout and produces failures that belong to someone else's branch.
---

# Working on Jarvis

Jarvis is an agent that controls a real computer. Two consequences shape
everything below: a capability that skips the permission gate is a security
hole rather than a shortcut, and a test result you can't trust is worse than no
test at all. Both failure modes are quiet, which is why they get their own
procedures.

Read [CLAUDE.md](../../../CLAUDE.md) for the invariants these workflows assume.

## Verifying a change

**Establish which code you are actually running, before anything else.**

This repo is worked on through several git worktrees under
`.claude/worktrees/`, and `pip install -e` binds the name `jarvis` to exactly
one checkout — usually the main one at `C:\Users\nicol\jarvis`, which sits on
its own branch with its own uncommitted work. So `pytest` from a worktree runs
*your* tests against *that* checkout's source. The failures look real, name
functions you can see, and have nothing to do with your change.

```bash
PYTHONPATH=src python -c "import jarvis; print(jarvis.__file__)"
```

If that path is not the directory you are editing, stop and fix it. Then run
everything with the same prefix:

```bash
PYTHONPATH=src python -m pytest        # whole suite
PYTHONPATH=src python -m mypy src      # strict, must be clean
ruff check .                           # lint, no PYTHONPATH needed
```

**No test makes a live model call.** The Gemini brain is exercised against a
fake HTTP transport, so the suite needs no API key and never spends free-tier
quota. To check a real key and model, run `jarvis brain` — it looks the model
up without generating anything.

## Adding a new tool

A tool is how Jarvis gains a new power over the machine, so the wiring is
deliberately explicit. Four steps, and skipping the third is the one that
actually matters.

**1. Write the function inside a `build_*_tools()` factory.**

Tools live in a `tools.py` beside the module that owns the capability
(`files/tools.py`, `browser/tools.py`, …) and are built by a factory that takes
the already-constructed controller. The factory shape keeps tools free of
global state, which is what makes them testable with a fake controller.

The argument schema handed to the LLM is derived from type hints, and the
description comes from the docstring — so annotate every parameter and write
the docstring for the model that will read it, not for a human skimming code.
A parameter named `context` is special: declaring it opts the tool into
`ToolContext` injection instead of becoming an LLM-visible argument.

```python
def build_notes_tools(manager: NoteManager) -> list[Tool]:
    def append_note(text: str, notebook: str = "default") -> str:
        """Append a line to a notebook. Returns the notebook path."""
        return manager.append(text, notebook)

    return [
        Tool(
            name="append_note",
            description=append_note.__doc__ or "",
            func=append_note,
        ),
    ]
```

**2. Tag the risk, if there is one.** Pass `risk_category="<category>"` for
anything destructive, outward-facing, or expensive. `risk_category=None` (the
default) declares the action inherently safe and it will *never* pause for
confirmation.

**3. Add the category to `require_confirmation`.** The category string is only
gating if it appears in `SecurityConfig.require_confirmation` in
`config/schema.py`. The existing categories are `delete_files`, `send_email`,
`install_software`, `modify_system_settings`, `spend_money`, `elevated_shell`.

This is the step that is easy to miss and impossible to see: a tool tagged
`"wipe_disk"` with no matching entry in the config is silently ungated, and
everything still passes. A dangerous tool needs **both** opt-ins. If your tool
fits an existing category, reuse it rather than inventing a synonym.

**4. Register it in `app/runtime.py`.** Always-available tools go with the
file and memory registrations:

```python
registry.register_all(build_notes_tools(notes))
```

If the tool needs an optional dependency, register it inside
`_register_optional_tools` behind an `ImportError` guard instead, so a machine
without that dependency logs a line and carries on rather than failing to
start. Add the dependency as an extra in `pyproject.toml`.

Tool names are global and `ToolRegistry.register` raises on a duplicate, so
pick a name no other module has taken.

**Then test it with a fake.** Drive the tool through `ToolManager.execute`
rather than calling the function directly — that is the path production uses,
and it is what proves the permission gate and the failure handling actually
apply to your tool. Assert on the returned `ToolResult`: tools report failure
by returning `ToolResult.failure`, never by raising.

Anything adjustable your tool needs — a limit, a path, a timeout — belongs in
`config/schema.py` with a default, not as a literal in the function.

## Adding a Brain provider

`brain/factory.py::build_brain` is the only place that decides which
implementation to construct. Everything downstream — Planner, Orchestrator,
Tool Manager, phone bridge, API server — sees only the `Brain` protocol from
`brain/base.py`, which is what makes a provider swappable at all.

1. Implement the `Brain` protocol in `brain/<name>_brain.py`. The protocol's
   `complete()` takes `system`, `messages`, and optional `tools`, and returns a
   `BrainResponse`.
2. Translate tool schemas **inside your provider.** Tools are defined once in
   Anthropic's shape; each provider adapts them to its own wire format (Gemini
   turns them into `functionDeclarations` with `parametersJsonSchema`). Keep native tool calling — do not
   stringify tools into the prompt, which loses structured calls.
3. Add a branch to `build_brain`, with the provider import **local to that
   branch.** This is why running on Gemini never imports `anthropic` and never
   asks for an API key. A module-level import would undo that.
4. Enforce that provider's requirements only inside its own branch — an API
   key, a reachable server — so users of other providers are never asked for
   credentials they do not need.
5. Extend `LLMConfig` in `config/schema.py` with any new settings. Sections use
   `extra="forbid"`, so a typo in YAML becomes a startup error rather than a
   silently ignored key.

Test it with a fake HTTP transport, the way `test_gemini_brain.py` does, so the
suite needs no key, no network, and spends no quota.

## Before you call it done

- `PYTHONPATH=src python -m pytest` — green
- `PYTHONPATH=src python -m mypy src` — clean; the project is strict
- `ruff check .` — clean
- New tunables in `config/schema.py`, not hardcoded
- New risk category present in **both** the tool and `require_confirmation`
- `jarvis tools` lists your tool on a machine that has its dependencies
