# Jarvis — frontend

The web dashboard for a Jarvis backend. Plain HTML, CSS and JavaScript —
no build step, no npm, no dependencies. It holds no data and no secrets of
its own: everything it shows comes live from a backend over HTTP.

| Page | What it shows |
|---|---|
| `index.html` — **Core** | A particle sphere that reacts to what Jarvis is doing (cyan idle, amber thinking, pulsing while it speaks, red on failure), machine vitals, running tasks, live activity, approval buttons, a command bar, and voice |
| `files.html` — **Files** | The workspace as a constellation; files flare as Jarvis reads, writes or deletes them |
| `office.html` — **Office** | The agent pool as pixel characters walking to the room of the tool they're using |
| `hud.html` — **HUD** | The classic single-page heads-up display: system status, tasks, activity, approvals |

## Two ways to open it

**1. Served by the backend** — if this `frontend/` folder sits next to the
`backend/` folder, `jarvis dash` opens it at `http://127.0.0.1:8765/dash/`
and it connects to that backend automatically. Nothing to set up.

**2. On its own** — for a frontend without the backend next to it, e.g.
one you were sent. Browsers won't run these pages straight from a
`file://` double-click, so serve the folder:

- **Windows:** double-click **`start.bat`** (needs Python from python.org)
- **Anywhere with Python:** `python serve.py` → opens http://localhost:8080
- **Anything else:** any static server works, e.g. `npx serve .`

Then fill in the connect screen:

- **Server** — the backend's address, e.g. `http://127.0.0.1:8765` for a
  backend on the same PC, or `http://192.168.1.20:8765` for one elsewhere
  on the network. Leave it blank only when the backend serves the page.
- **API token** — the backend's `JARVIS_API_TOKEN` (from its `.env`; the
  backend's owner can give it to you, or make one with `jarvis token`).

Both are remembered in this browser. To send someone a link that already
points at the right backend, add `?server=`:
`http://localhost:8080/?server=http://192.168.1.20:8765`, or run
`python serve.py --server http://192.168.1.20:8765`.

## Setting the default backend

`config.js` holds the backend address used when nothing else says
otherwise. Edit it before handing the folder to someone:

```js
window.JARVIS_CONFIG = {
  server: "http://192.168.1.20:8765",
};
```

(When the backend serves these pages itself, it answers `config.js` with
"this same server", so this file is only used for standalone copies.)

## What the backend needs to allow

A frontend opened on `localhost` or `127.0.0.1` (any port) can talk to a
backend on the same PC with no changes. For a frontend opened from another
machine or address, the backend's owner adds two lines to
`backend/config/config.yaml` and restarts it:

```yaml
api:
  host: 0.0.0.0                    # listen on the network
  cors_origins:
    - http://192.168.1.30:8080     # exactly where this frontend is opened
```

If the connect screen says it *could not reach Jarvis*, it is almost always
one of: the backend isn't running, the server address is wrong, or the
frontend's address isn't in `cors_origins`.

> **Microphone:** browsers only allow the mic on `https://` pages or on
> `localhost`. Voice works when the page is opened on `localhost`; on a
> LAN address, typing still works but the mic stays off.

## Files

```
frontend/
├── index.html  files.html  office.html  hud.html
├── config.js          the default backend address
├── start.bat          double-click to serve on Windows
├── serve.py           tiny static server (Python standard library only)
├── css/               hud.css, pages.css
└── js/
    ├── connection.js  which backend, and the token (shared by every page)
    ├── hud.js         auth, fetch, live event stream, shared helpers
    ├── page-*.js      one per page
    ├── core-visual.js the particle sphere
    ├── office-world.js the pixel office
    └── voice.js       wake-word listening and speech playback
```
