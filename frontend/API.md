# The backend API this frontend expects

Everything the pages show comes from these HTTP routes. `backend/jarvis.py`
implements all of them (`listen` answers 503 — it has no speech input); to drive this UI from **another project**, implement
the ones for the pages you want and point `config.js` at your server. Route
paths are defaults — rename any of them under `routes` in `config.js`.

| Page | Routes it uses |
|---|---|
| `index.html` (Core) | `snapshot`, `stream`, `stats`, `command`, `approval`, optionally `listen`, `speak`, `docs` |
| `files.html` | `snapshot`, `stream`, `tree` |
| `office.html` | `snapshot`, `stream` |
| `hud.html` | `system`, `tasks`, `notifications`, `approvals`, `approval`, `events` |

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
  "rooms":     [{"id": "archives", "name": "Archives", "subtitle": "files", "hue": 190, "desks": 4}],
  "agents":    [{"id": "a1", "name": "Ada", "hue": 180, "sprite": 0, "index": 0,
                 "status": "working", "task_id": "t1", "step_id": "s1", "tool": "read_file",
                 "description": "Read notes.txt", "arguments": {"path": "notes.txt"},
                 "since": "2026-09-30T12:00:00+00:00"}],
  "tasks":     [Task, ...],
  "approvals": [Approval, ...],
  "events":    [Frame, ...],
  "touched":   [{"path": "notes.txt", "action": "read", "at": "...", "agent_id": "a1"}],
  "voice":     {"enabled": false},
  "settings":  {"particles": 900, "accent": "#22d3ee", "stats_interval": 2.0}
}
```

- **Task** — `{id, request, status, goal, steps: [{id, description, tool, status, risk, error}], result, error, created_at, updated_at}`.
  `status` is one of `pending`, `planning`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`.
- **Approval** — `{id, task_id, tool, arguments, reason, created_at}`.
- **voice** — `{"enabled": false}` hides the microphone. With speech:
  `{enabled, can_speak, can_listen, voice, provider, wake_word, wake_word_required, ...}`
  (`provider` is `edge` or `openai`; `listen_provider` is `wispr` or empty).
- `rooms` / `agents` only matter to the Office page; send `[]` otherwise.

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
  "tool": "read_file",
  "agent_id": "a1",
  "room": "archives",
  "speak": false,
  "files": {"action": "read", "paths": ["notes.txt"]}
}
```

Only `type` and `message` are required. The types the pages react to:
`task.created`, `task.planning`, `task.started`, `task.progress`,
`task.completed`, `task.failed`, `task.cancelled`, `step.started`,
`step.completed`, `step.failed`, `step.retrying`, `approval.required`,
`approval.resolved`, `error`, `agent.assigned`, `agent.idle`, `heard`.
`files.action` is `read`, `write` or `delete`; `speak: true` asks the Core
page to read `message` aloud.

### `tree` — `GET /dash/api/tree?depth=3&limit=260`

The workspace for the Files page.

```json
{
  "root": "C:/Users/me/JarvisWorkspace",
  "nodes": [{"id": ".", "name": "workspace", "path": ".", "type": "dir", "size": null, "parent": null, "depth": 0},
            {"id": "notes.txt", "name": "notes.txt", "path": "notes.txt", "type": "file", "size": 120, "parent": ".", "depth": 1}],
  "links": [{"source": ".", "target": "notes.txt"}],
  "truncated": false, "depth": 3, "limit": 260
}
```

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

- `POST /dash/api/listen` — multipart form: `audio` (a webm recording; a 16 kHz
  mono WAV when `voice.listen_provider` is `wispr`) and
  `submit` (`"true"`). Responds like `command`. Answer `503` with a `detail`
  to switch the microphone off with that message.
- `POST /dash/api/speak` — JSON `{"text": "..."}`. Responds with audio bytes
  (`audio/mpeg`, or any type an `<audio>` element plays).

### `docs` — `GET /docs`

Only linked to ("API Reference" on the Core page); point it anywhere.

## HUD routes (`hud.html`)

The single-page HUD polls these plain REST routes instead of the dashboard
API above.

| Route | Default | Returns |
|---|---|---|
| `system` | `GET /system` | `{"brain": {"provider": "gemini", "model": "gemini-3.8-flash", "connected": true}, "tools": ["read_file", ...], "active_tasks": 1, "total_tasks": 5, "workspace": "..."}` |
| `tasks` | `GET /tasks` | `[Task, ...]`, newest first |
| `tasks` | `POST /tasks` | Request `{"request": "..."}` → the new Task |
| `notifications` | `GET /notifications` | `[{"type": "info", "message": "...", "task_id": "t1", "created_at": "..."}]` |
| `approvals` | `GET /approvals` | `[Approval, ...]` still pending |
| `approval` | `POST /approvals/{id}` | as above |
| `events` | `GET /events?token=…` | server-sent events; any message makes the HUD refresh |
