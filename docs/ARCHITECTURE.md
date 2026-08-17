# Jarvis Architecture

Jarvis is a personal AI desktop assistant that runs continuously on a Windows
machine, receives natural-language instructions from a phone, plans before it
acts, and controls the computer safely. This document is the map of the
system: what each module owns, the rules that keep them independent, and the
order in which they are being built.

## Module map

```
                         ┌──────────────┐
        phone client ──▶ │  API Server  │──▶ Authentication
                         └──────┬───────┘
                                ▼
      ┌───────────┐      ┌──────────────┐      ┌───────────────┐
      │  Memory   │◀────▶│    Brain     │◀────▶│    Planner    │
      └───────────┘      └──────┬───────┘      └───────────────┘
                                ▼
                         ┌──────────────┐
                         │ Tool Manager │──▶ Security (permissions)
                         └──────┬───────┘
              ┌─────────────┬───┴─────────┬──────────────┐
              ▼             ▼             ▼              ▼
      ┌──────────────┐ ┌─────────┐ ┌───────────┐ ┌──────────────┐
      │File Manager  │ │ Desktop │ │  Browser  │ │ Notifications│
      └──────────────┘ └────┬────┘ └───────────┘ └──────────────┘
                            ▼
                       ┌─────────┐
                       │ Vision  │
                       └─────────┘

      Configuration and Logging are cross-cutting: every module uses them,
      neither depends on any other module.
```

| Module | Package | Responsibility |
|---|---|---|
| Configuration | `jarvis.config` | Typed settings from defaults → YAML → env vars; secrets kept structurally separate |
| Logging | `jarvis.logging` | Structured JSON-lines logs + console output, with task context propagation |
| Brain | `jarvis.brain` | LLM connection (local Ollama by default; Anthropic optional); decides what to do next |
| Planner | `jarvis.planner` | Breaks requests into steps, estimates risk, tracks progress |
| Memory | `jarvis.memory` | Persistent conversations, preferences, and task history |
| Tool Manager | `jarvis.tools` | Tool registry + dispatch; enforces permissions; logs every invocation |
| Desktop Controller | `jarvis.desktop` | Windows control: apps, windows, mouse, keyboard |
| Browser Controller | `jarvis.browser` | Playwright web automation |
| Vision | `jarvis.vision` | Screen understanding: text, buttons, windows, layouts |
| File Manager | `jarvis.files` | File and folder operations, archives, search |
| API Server | `jarvis.api` | Secure HTTP API for the phone client |
| Phone Bridge | `jarvis.phone` | Telegram remote control + push; outbound-only HTTP transport |
| Authentication & permissions | `jarvis.security` | Client auth + the safe/confirm action policy |
| Notifications | `jarvis.notifications` | Task lifecycle, error, and approval notifications; log/live/push channels |

## Design rules

1. **Modules are independent and replaceable.** A module may depend on
   `jarvis.config` and `jarvis.logging`, and on the *interfaces* of the
   modules directly beneath it in the diagram — never on their internals.
2. **Nothing executes without a plan.** Requests flow phone → API → Brain →
   Planner; only then does the Tool Manager execute, one step at a time,
   observing each result.
3. **The Tool Manager is the only execution gateway.** Brain and Planner never
   touch the OS directly. This gives one choke point for permission checks,
   confirmation prompts, and audit logging.
4. **Secrets never live in config files.** `AppConfig` (YAML + env) has no
   secret fields by construction; `Secrets` reads only from the environment /
   `.env` and wraps values in `SecretStr`.
5. **No hardcoded tunables.** Anything adjustable belongs in
   `jarvis/config/schema.py` with a default, overridable via YAML or env.
6. **Every important action is logged** with bound context (`task_id`,
   `tool`, ...) to a searchable JSON-lines file.

## Decisions log

| Decision | Why |
|---|---|
| Python 3.11+, `src/` layout | Modern typing (`X | Y`, `Self`); `src/` layout prevents accidentally importing the uninstalled tree |
| pydantic-settings for config | Typed validation at startup, env + YAML layering built in, clear errors on typos (`extra="forbid"` per section) |
| Separate `Secrets` settings class | Makes committing a secret structurally impossible, not just discouraged |
| stdlib `logging` + custom JSON formatter | Zero extra dependencies, universally compatible with libraries, easy to swap for structlog later if needed (the module is replaceable) |
| `contextvars` for log context | Works across threads *and* asyncio tasks — the API server and tool execution will be async |
| `platformdirs` for default paths | Correct per-user locations on Windows (`%LOCALAPPDATA%`) without hardcoding, still works on macOS/Linux for development |
| Logger tree `jarvis.*`, not root | Third-party libraries keep their own logging; our handlers only see our records |
| Telegram bridge for phone control | Outbound long-poll only — no LAN, no port-forwarding, no exposed server; works over any internet (even a phone tether), which is exactly the "no wifi on the laptop" case. Gives push + full control in one integration |
| HTTP behind an `HttpTransport` protocol | The Telegram bridge and push channel are fully unit-testable with a fake transport — no real bot/account/network needed in CI |
| `owner_chat_id` allowlist for Telegram | A personal bot must obey only its owner; unknown chats are refused (but told their own id, to ease first-run setup) |
| Local Ollama model as the default Brain | A desktop assistant reads files, screens, and messages; keeping the model on the machine means none of that is sent anywhere, needs no API key, and costs nothing per task. Design rule 1 already made the provider replaceable, so this is a configuration change, not a rewrite |
| Providers chosen in one factory (`brain.factory`) | Selection lives in exactly one place, and each provider's requirements (an API key, a running server) are enforced only when that provider is picked — so Ollama users are never asked for `ANTHROPIC_API_KEY` |
| Tool schemas translated inside each Brain | Tools are defined once, in Anthropic's shape, and each provider adapts them to its own wire format (Ollama wants the OpenAI function shape). Native tool calling is preserved on both — no stringifying tools into the prompt |

## Build order (one feature at a time)

1. ✅ Foundation — repo scaffolding, Configuration, Logging
2. ✅ Core types + EventBus, Security policy, Notifications
3. ✅ Tool Manager — registry, risk gating, audit logging
4. ✅ File Manager (sandboxed) + Memory (SQLite)
5. ✅ Brain (local Ollama, or Anthropic) + Planner + agent Orchestrator
6. ✅ API Server + Authentication — the phone's entry point
7. ✅ Browser Controller (Playwright)
8. ✅ Desktop Controller + Vision — screen-aware Windows control
9. ✅ Runtime wiring + `jarvis` CLI
10. ✅ Phone Bridge — Telegram remote control + push notifications (`jarvis phone`)

All planned modules are implemented, and the phone can now both receive push
notifications and fully control Jarvis (Telegram bridge + ntfy push). Natural
next steps: sending the screen to Claude's vision for richer layout
understanding, and an email tool (already covered by the `send_email`
confirmation category).
