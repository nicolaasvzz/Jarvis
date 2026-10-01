# The backend API this frontend expects

Everything the pages show comes from these HTTP routes. `backend/jarvis.py`
implements all of them (`listen` answers 503 — it has no speech input); to drive this UI from **another project**, implement
the ones for the pages you want and point `config.js` at your server. Route
paths are defaults — rename any of them under `routes` in `config.js`.

| Page | Routes it uses |
|---|---|
| `index.html` (Core) | `snapshot`, `stream`, `stats`, `command`, `approval`, optionally `system`, `listen`, `speak`, `docs` |
| `terminal.html` | `snapshot`, `stream`, `terminals`, `terminalStream`, `terminalInput`, `terminalClose`, `terminalExplain`, `terminalTrust`, `approvals`, `approval`, `command` |
| `approve.html` | `snapshot`, `stream`, `approvals`, `approval` |

The smallest useful backend is `snapshot` + `stream` + `command`: that gives
the Core page live activity and a working command bar.

## Conventions

- **Auth:** every request carries `Authorization: Bearer <token>` — the token
  typed on the connect screen. Event streams can't send headers, so they get
  `?token=<token>` instead. Answer `401` for a bad token: the UI then clears
  it and shows the connect screen again.
- **Errors:** any non-2xx status; if the body is JSON with a `detail` string,
  that text is shown to the user.
- **Cross-origin:** a standalone copy of the frontend is a different origin,
  so allow CORS for it — methods `GET, POST`, request headers
  `Authorization, Content-Type`. No cookies are used.
- **Times** are ISO-8601 strings. Unknown extra fields are ignored, so you
  can send more than listed here.

## Dashboard routes

### `snapshot` — `GET /dash/api/snapshot`

Everything a page needs to draw itself from cold; fetched on load and after
reconnecting.

```json
{
  "workspace": "C:/Users/me/JarvisWorkspace",
  "tasks":     [Task, ...],
  "approvals": [Approval, ...],
  "terminals": [Terminal, ...],
  "events":    [Frame, ...],
  "voice":     {"enabled": false},
  "settings":  {"particles": 900, "accent": "#22d3ee", "stats_interval": 2.0}
}
```

- **Task** — `{id, request, status, goal, steps: [{id, description, tool, status, risk, error}], result, error, created_at, updated_at}`.
  `status` is one of `pending`, `planning`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`.
- **Approval** — `{id, task_id, tool, arguments, reason, created_at}`.
- **voice** — `{"enabled": false}` hides the microphone. With speech:
  `{enabled, can_speak, can_listen, voice, provider, wake_word, wake_word_required, ...}`
  (`provider` is `edge` or `openai`; `listen_provider` is `browser`, `whisper`, `wispr` or empty; with `browser` the page recognises speech itself and posts the text to `command`, using `listen_language`).
- **Terminal** — see [Terminal routes](#terminal-routes); send `[]` if you
  have none.

### `stream` — `GET /dash/api/stream?token=…`

Server-sent events. Each message is `data: <Frame as JSON>\n\n`; send a
comment line (`: keepalive`) every ~15 s so dead connections are noticed.

```json
{
  "seq": 42,
  "type": "task.completed",
  "message": "Summarised notes.txt in 3 bullet points.",
  "task_id": "t1",
  "created_at": "2026-09-30T12:00:05+00:00",
  "data": {},
  "tool": "get_weather",
  "speak": false
}
```

Only `type` and `message` are required. The types the pages react to:
`task.created`, `task.planning`, `task.started`, `task.progress`,
`task.completed`, `task.failed`, `task.cancelled`, `step.started`,
`step.completed`, `step.failed`, `step.retrying`, `approval.required`,
`approval.resolved`, `approval.auto` (ran without asking — a read-only
command or a trusted terminal), `error`, `heard`, and for the Terminal page
`terminal.opened`, `terminal.exited`, `terminal.updated`,
`terminal.failed` (a command failed), `terminal.finished` (a long command
worked), `terminal.closed` (`data` carries the `terminal`, or its
`terminal_id`) and `terminal.input` (the assistant typed into
`data.terminal_id`). `speak: true` asks the Core page to read `message`
aloud.

### `stats` — `GET /dash/api/stats`

Machine vitals, polled every `settings.stats_interval` seconds. Send
`{"available": false}` to hide the panel's numbers.

```json
{"available": true, "cpu": 23.5,
 "memory": {"percent": 61.0, "used": 17000000000, "total": 28000000000},
 "disk": {"percent": 64.0, "used": 1200000000000, "total": 1900000000000},
 "battery": {"percent": 92, "plugged": true},
 "network": {"sent": 123, "received": 456}}
```

Any of the inner objects may be `null`.

### `command` — `POST /dash/api/command`

The command bar. Request `{"text": "summarise notes.txt", "submit": true}`;
response:

```json
{"text": "summarise notes.txt", "addressed": true, "command": "summarise notes.txt",
 "submitted": true, "task_id": "t1", "confidence": null, "details": {}}
```

### `approval` — `POST /approvals/{id}`

Allow or deny a pending action. Request `{"decision": "allow"}` or
`{"decision": "deny"}`; respond with any JSON object, e.g.
`{"detail": "Approval allowed."}`.

### `listen` / `speak` — voice (optional)

- `POST /dash/api/listen` (`whisper` and `wispr` only; the `browser` option never calls it) — multipart form: `audio` (a webm recording; a 16 kHz
  mono WAV when `voice.listen_provider` is `wispr`) and
  `submit` (`"true"`). Responds like `command`. Answer `503` with a `detail`
  to switch the microphone off with that message.
- `POST /dash/api/speak` — JSON `{"text": "..."}`. Responds with audio bytes
  (`audio/mpeg`, or any type an `<audio>` element plays).

### `docs` — `GET /docs`

Only linked to ("API Reference" on the Core page); point it anywhere.

## Terminal routes

Live shells on the backend's machine, shown with xterm.js on
`terminal.html`. The backend owns each shell (a pseudo-terminal); the page
shows one at a time and types into it. The assistant can use the same
terminals, so both see the same screen.

**Terminal** —

```json
{"id": "term-2", "title": "npm install", "purpose": "install the app's packages",
 "opened_by": "jarvis", "status": "running", "state": "at its prompt",
 "at_prompt": true, "exit_code": null, "created_at": "...", "cols": 120, "rows": 30,
 "trusted": false, "running": null,
 "last_result": {"command": "npm install", "by": "jarvis", "ok": true, "seconds": 41,
                 "started_at": "...", "finished_at": "...", "watched": false},
 "log": [{"at": "...", "by": "jarvis", "text": "npm install"},
         {"at": "...", "by": "you", "text": "npm run dev"}]}
```

`status` is `running` or `exited`; `opened_by` and `log[].by` are `you`,
`jarvis` or `startup` (opened from the backend's start-up list). `log` is
the last few lines typed and who typed them. `running` is the command in
progress (`{command, by, started_at}`) or `null`; `last_result` is how the
last one went. `trusted` means the assistant may type there without asking. `cols` and
`rows` are fixed when the terminal opens: shrinking a terminal would cut its
lines. Show it at exactly that size, scaling the font to fit.

| Route | Default | |
|---|---|---|
| `terminals` | `GET /dash/api/terminals` | `[Terminal, ...]` |
| `terminals` | `POST /dash/api/terminals` | `{"title": "", "purpose": "", "cols": 120, "rows": 30}` (all optional; the size defaults to the last one given, which is also what the assistant's terminals use) → the new Terminal. `409` with a `detail` when no more can be opened. |
| `terminalStream` | `GET /dash/api/terminals/{id}/stream?token=…` | Server-sent events, below |
| `terminalInput` | `POST /dash/api/terminals/{id}/input` | `{"data": "<keystrokes, as xterm.js sends them>"}`. `409` if the shell has exited. |
| `terminalClose` | `POST /dash/api/terminals/{id}/close` | Stops the shell and forgets it |
| `terminalExplain` | `POST /dash/api/terminals/{id}/explain` | Asks the assistant what the screen shows and what went wrong → the new Task; its answer arrives as `task.completed` |
| `terminalTrust` | `POST /dash/api/terminals/{id}/trust` | `{"trusted": true}` lets the assistant type there without asking; `false` takes that back → the Terminal |

Every terminal route answers `404` for a terminal that is closed.

The stream's messages, each `data: <JSON>\n\n`:

- `{"type": "replay", "data": "...", "terminal": Terminal}` — first, always:
  the scrollback and screen as escape codes, colours included. Write it into
  a freshly reset xterm of the terminal's size.
- `{"type": "output", "data": "..."}` — new output, raw.
- `{"type": "exit", "code": 0}` — the shell ended; the terminal stays listed
  until closed.
- `{"type": "closed"}` — the terminal was closed; the stream then ends.

## Other routes

| Route | Default | Returns |
|---|---|---|
| `approvals` | `GET /approvals` | `[Approval, ...]` still pending — the Terminal and Approvals pages |
| `system` | `GET /system` | `{"brain": {"provider": "gemini", "model": "gemini-3.5-flash-lite", "connected": true}, "tools": ["run_command", ...], "active_tasks": 1, "total_tasks": 5, "workspace": "..."}` — the model line on the Core page |

`jarvis.py` also has plain REST routes no page uses, for scripts and curl:
`GET`/`POST /tasks`, `GET /tasks/{id}`, `GET /notifications` and
`GET /events` (the event stream again).
