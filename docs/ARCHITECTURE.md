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
        browser      ──▶ │  + Dashboard │
                         └──────┬───────┘
                                ▼
      ┌───────────┐      ┌──────────────┐      ┌───────────────┐
      │  Memory   │◀────▶│    Brain     │◀────▶│    Planner    │
      └───────────┘      └──────┬───────┘      └───────────────┘
                                ▼            (plan = dependency graph)
                         ┌──────────────┐
                         │ Orchestrator │──▶ retry / revise / give up
                         └──────┬───────┘
                                ▼
                         ┌──────────────┐
                         │  Agent Pool  │──▶ N agents, unblocked steps
                         └──────┬───────┘
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
                       │ Vision  │       Voice ──▶ speech in / out
                       └─────────┘

      Configuration and Logging are cross-cutting: every module uses them,
      neither depends on any other module.

      The Dashboard is a pure consumer: it subscribes to the Event Bus and
      reads the Orchestrator, and nothing depends on it.
```

| Module | Package | Responsibility |
|---|---|---|
| Configuration | `jarvis.config` | Typed settings from defaults → YAML → env vars; secrets kept structurally separate |
| Logging | `jarvis.logging` | Structured JSON-lines logs + console output, with task context propagation |
| Brain | `jarvis.brain` | LLM connection (Gemini by default; Anthropic optional); decides what to do next |
| Planner | `jarvis.planner` | Breaks requests into a risk-tagged dependency graph of steps |
| Agent Pool | `jarvis.agent.pool` | Runs every unblocked step at once across named agents |
| Memory | `jarvis.memory` | Persistent conversations, preferences, and task history |
| Voice | `jarvis.voice` | Neural speech out; wake-word listening, transcribed locally |
| Dashboard | `jarvis.dashboard` + `frontend/` | The event hub and API behind the web UI; the pages themselves are the separate `frontend/` folder |
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
   Planner; only then does the Agent Pool execute, observing each result.
   Steps run concurrently only where the plan says they are independent.
7. **Sequencing is the safe default.** A plan that says nothing about
   ordering is chained. A step that quietly relied on an earlier one and ran
   too early produces a wrong answer; one needlessly serialised is merely
   slower.
8. **Observers never slow down work.** The dashboard's subscribers have
   bounded queues and drop frames rather than apply back-pressure to the
   agent loop, and no module depends on anything in `jarvis.dashboard`.
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
| `backend/` and `frontend/` as separate top-level folders | Each half can be handed to someone on its own. They share nothing but HTTP: the backend runs headless without the pages, and the pages connect to any backend by URL + token. The backend serves `../frontend` at `/dash/` when it is there |
| pydantic-settings for config | Typed validation at startup, env + YAML layering built in, clear errors on typos (`extra="forbid"` per section) |
| Separate `Secrets` settings class | Makes committing a secret structurally impossible, not just discouraged |
| stdlib `logging` + custom JSON formatter | Zero extra dependencies, universally compatible with libraries, easy to swap for structlog later if needed (the module is replaceable) |
| `contextvars` for log context | Works across threads *and* asyncio tasks — the API server and tool execution will be async |
| `platformdirs` for default paths | Correct per-user locations on Windows (`%LOCALAPPDATA%`) without hardcoding, still works on macOS/Linux for development |
| Logger tree `jarvis.*`, not root | Third-party libraries keep their own logging; our handlers only see our records |
| Telegram bridge for phone control | Outbound long-poll only — no LAN, no port-forwarding, no exposed server; works over any internet (even a phone tether), which is exactly the "no wifi on the laptop" case. Gives push + full control in one integration |
| HTTP behind an `HttpTransport` protocol | The Telegram bridge and push channel are fully unit-testable with a fake transport — no real bot/account/network needed in CI |
| `owner_chat_id` allowlist for Telegram | A personal bot must obey only its owner; unknown chats are refused (but told their own id, to ease first-run setup) |
| Gemini as the default Brain | A free AI Studio key covers it, so a new install costs nothing and needs no GPU. It replaced a local Ollama provider: local models were free too, but needed a large download and a strong machine, which is the wrong default for sharing Jarvis with anyone. Design rule 1 made this a provider swap, not a rewrite |
| Gemini over plain HTTPS, no SDK | `httpx` is already in the base install; one fewer dependency, and the brain is tested against a fake transport with no key and no network |
| Rate limits absorbed in the brain | The free tier is limited per minute. A `429` waits as long as Google asks (capped at a minute, so a daily quota fails rather than sleeping for hours) before the Planner ever sees an error |
| Providers chosen in one factory (`brain.factory`) | Selection lives in exactly one place, and each provider's requirements (its API key) are enforced only when that provider is picked — so Gemini users are never asked for `ANTHROPIC_API_KEY` |
| Tool schemas translated inside each Brain | Tools are defined once, in Anthropic's shape, and each provider adapts them to its own wire format (Gemini wants `functionDeclarations` with `parametersJsonSchema`). Native tool calling is preserved on both — no stringifying tools into the prompt |
| Plans are dependency graphs, not lists | Lets independent work run in parallel without inventing a second planning concept. A fully-chained graph behaves exactly as the old sequential list did |
| Dependencies may only point backwards | Makes a cycle — and therefore a deadlock — structurally impossible, rather than something to detect at runtime |
| Concurrency in the pool, policy in the Orchestrator | Retry/revise needs the Planner and task history; task-juggling needs neither. Splitting them keeps both readable |
| Tool arguments are redacted before publishing | The dashboard needs to know *which file*; it must never receive the file's contents or a password. Identifying fields survive, payloads become `<N chars>` |
| Dashboard API served in-process | Live agent state exists only in the Orchestrator's memory; a second process could only ever show what had already reached disk. The *pages* can live anywhere — only the API has to be in-process |
| CORS: localhost always, other origins by config | A standalone frontend is a different origin. The bearer token (not a cookie) is what authorises a request, so CORS only decides which pages may try; localhost is safe to allow by default, anything else is listed in `api.cors_origins` |
| Vanilla ES modules, no build step | A Python project should not need npm to serve its own UI. Canvas 2D and modules are enough for all four pages, and the folder can be served by anything |
| Office sprites drawn in code | No third-party art licence to honour, and agent colour can be derived from the agent's own hue so twenty stay distinguishable |
| Speech synthesised server-side, played in the browser | Lets the HUD visualise the waveform in time with the voice, and keeps a headless server silent instead of talking to an empty room |
| Transcription runs locally | An always-listening microphone that streams to someone else's server is a different product; Jarvis should not quietly be the first |

## Build order (one feature at a time)

1. ✅ Foundation — repo scaffolding, Configuration, Logging
2. ✅ Core types + EventBus, Security policy, Notifications
3. ✅ Tool Manager — registry, risk gating, audit logging
4. ✅ File Manager (sandboxed) + Memory (SQLite)
5. ✅ Brain (Gemini, or Anthropic) + Planner + agent Orchestrator
6. ✅ API Server + Authentication — the phone's entry point
7. ✅ Browser Controller (Playwright)
8. ✅ Desktop Controller + Vision — screen-aware Windows control
9. ✅ Runtime wiring + `jarvis` CLI
10. ✅ Phone Bridge — Telegram remote control + push notifications (`jarvis phone`)
11. ✅ Agent Pool — dependency-graph plans executed by N named agents
12. ✅ Voice — British neural speech out, wake-word listening transcribed locally
13. ✅ Dashboard — particle core, file constellation, pixel agent office

All planned modules are implemented. Jarvis plans as a graph, works on
several branches at once, can be watched doing it, and can be spoken to.
Natural next steps: sending the screen to Claude's vision for richer layout
understanding, and an email tool (already covered by the `send_email`
confirmation category).
