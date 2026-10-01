# Jarvis — frontend

A live web dashboard for an AI-agent backend. Plain HTML, CSS and JavaScript
— no build step, no npm. The one library, xterm.js for the Terminal
page, is loaded from a CDN. It holds no data and no secrets
of its own: everything it shows comes from a backend over HTTP, described in
[API.md](API.md). It was built for Jarvis, but nothing in it is tied to
Jarvis beyond the defaults in one settings file, so it works as the UI for
other projects too.

| Page | What it shows |
|---|---|
| `index.html` — **Core** | A particle sphere that reacts to what the agent is doing (cyan idle, amber thinking, pulsing while it speaks, red on failure), machine vitals, running tasks, live activity, approval buttons, a command bar, and voice |
| `terminal.html` — **Terminal** | Live terminals on the backend's machine, shared with the assistant: you both see them and type in them. Its approval requests show here too, a failed command lights up **Explain**, and a terminal can be trusted so the assistant types there without asking |
| `approve.html` — **Approvals** | Built for a phone: Allow/Deny what the assistant wants to run, with the exact command, and what happened recently |

## Open it

**Served by Jarvis** — with this folder beside `backend/`,
`python jarvis.py` opens it at `http://127.0.0.1:8765/dash/` and it connects
to that backend automatically.

**On its own** — browsers won't run these pages from a `file://`
double-click, so serve the folder:

- **Windows:** double-click **`start.bat`** (needs Python from python.org)
- **Anywhere with Python:** `python serve.py` → http://localhost:8080
  (`--port 9000`, `--server http://host:port`, `--host 0.0.0.0`)
- **Anything else:** any static server, e.g. `npx serve .`, nginx, or a
  folder inside another web app

Then fill in the connect screen:

- **Server** — the backend's address, e.g. `http://127.0.0.1:8765`. Leave it
  blank only when the backend serves the page itself.
- **API token** — the backend's token (for Jarvis: `JARVIS_API_TOKEN` in
  `backend/.env`, made on its first run).

Both are remembered in the browser. To send someone a link that already
points at the right backend, add `?server=`:
`http://localhost:8080/?server=http://192.168.1.20:8765`.

## Configure it: `config.js`

Everything project-specific lives in [`config.js`](config.js); every key is
optional and falls back to the defaults in `js/lib/connection.js`.

```js
window.HUD_CONFIG = {
  server: "http://127.0.0.1:8765",   // the backend to talk to
  appName: "JARVIS",                 // header, connect screen, tab title
  storagePrefix: "jarvis.",          // localStorage keys (token, server, mute)
  tokenHint: "",                     // replaces the "where's my token" sentence
  routes: {                          // where each piece of data lives — see API.md
    snapshot: "/dash/api/snapshot",
    // ...
  },
};
```

When the Jarvis backend serves these files, it serves this same file with
`server: ""` appended, so your other settings still apply.

Colours are CSS variables at the top of `css/hud.css` (`--accent` and
friends); the Core page also takes its accent from the backend's snapshot.

## Use it for another project

1. Copy this `frontend/` folder into your project (or serve it from here).
2. In `config.js`, set `appName`, a `storagePrefix` of your own, and
   `server`.
3. Implement the routes in [API.md](API.md) on your backend — or map
   `routes` in `config.js` onto endpoints you already have. `snapshot` +
   `stream` + `command` is enough for a working Core page, and adding
   `approvals` + `approval` makes `approve.html` work.
4. Allow CORS for wherever the pages are opened from (methods `GET, POST`;
   headers `Authorization, Content-Type`), unless your backend serves the
   folder itself.

The pages only reach the backend through `js/lib/connection.js`
(`HudConnection.endpoint("snapshot")`, or `Hud.route(...)` in modules), so
there are no other paths to find and change.

## Connecting to a Jarvis backend elsewhere

A frontend opened on `localhost` or `127.0.0.1` (any port) can talk to a
Jarvis backend on the same PC with no changes. For one opened from another
address, the backend's owner adds two lines to `backend/.env` and restarts it:

```
JARVIS_HOST=0.0.0.0
JARVIS_CORS_ORIGINS=http://192.168.1.30:8080
```

(`JARVIS_HOST` makes it listen on the network; `JARVIS_CORS_ORIGINS` is
exactly where this frontend is opened.) If the connect screen says it
*could not reach* the backend, it's almost always one of: the backend isn't
running, the server address is wrong, or the page's address isn't in
`JARVIS_CORS_ORIGINS`.

> **Voice:** Jarvis speaks its answers when `edge-tts` is installed on the
> backend. The pages can also listen through the microphone if a backend
> implements the `listen` route (see API.md) — `jarvis.py` doesn't, so you
> type instead.

## Files

```
frontend/
├── index.html  terminal.html  approve.html
├── config.js              project settings: server, name, routes
├── API.md                 the backend routes these pages use
├── start.bat  serve.py    serve the folder (Windows / any OS with Python)
├── css/                   hud.css (theme + layout), terminal.css, approve.css
└── js/
    ├── lib/
    │   ├── connection.js  settings, server, token, routes, branding (every page loads it first)
    │   └── hud.js         fetch + auth, the live event stream, connect screen, helpers
    ├── pages/             core.js, terminal.js, approve.js — one per page
    └── components/        core-visual.js (the sphere),
                           voice.js (wake-word listening, speech playback)
```
