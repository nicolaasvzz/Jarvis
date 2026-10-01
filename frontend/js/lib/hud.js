/*
 * Shared dashboard runtime: authentication, the live event stream, and the
 * small helpers every page needs.
 *
 * Every page is a renderer over one server-sent-event feed. This module
 * owns the connection to it — including reconnecting, which matters more than
 * it sounds: Jarvis runs for days, laptops sleep, and a HUD that silently
 * stopped updating is worse than one that says it is offline.
 *
 * Which backend to talk to, the token, and every route come from
 * js/lib/connection.js, which every page loads first. Pages ask for data by
 * route name — Hud.getJSON(Hud.route("snapshot")) — never by literal path.
 */

export const Hud = (() => {
  const Connection = window.HudConnection;
  const appName = () => Connection.settings.appName;

  /* -- fetch ------------------------------------------------------------- */

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(Connection.url(path), {
        ...options,
        headers: {
          ...(options.body && !(options.body instanceof FormData)
            ? { "Content-Type": "application/json" }
            : {}),
          Authorization: `Bearer ${Connection.token()}`,
          ...(options.headers || {}),
        },
      });
    } catch (_) {
      // The browser gives no detail for a refused connection or a CORS
      // rejection; name the server so the fix is obvious.
      throw new Error(`Could not reach ${appName()} at ${Connection.describe()}.`);
    }
    if (response.status === 401) {
      Connection.clearToken();
      showGate("That token was not accepted.");
      throw new Error("unauthorised");
    }
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        if (body && body.detail) detail = body.detail;
      } catch (_) {
        /* a non-JSON error body is fine; the status line will do */
      }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return response;
  }

  const getJSON = (path) => api(path).then((r) => r.json());
  const postJSON = (path, body) =>
    api(path, { method: "POST", body: JSON.stringify(body) }).then((r) => r.json());

  /* -- token gate -------------------------------------------------------- */

  function showGate(message = "") {
    const gate = document.getElementById("gate");
    if (!gate) return;
    gate.style.display = "grid";
    const err = gate.querySelector(".err");
    if (err) err.textContent = message;
    const server = gate.querySelector("input[name=server]");
    if (server && !server.value) server.value = Connection.server();
    const token = gate.querySelector("input[name=token]");
    if (token) setTimeout(() => token.focus(), 40);
  }

  function hideGate() {
    const gate = document.getElementById("gate");
    if (gate) gate.style.display = "none";
  }

  function wireGate(onReady) {
    const gate = document.getElementById("gate");
    if (!gate) return;
    const server = gate.querySelector("input[name=server]");
    const input = gate.querySelector("input[name=token]");
    const button = gate.querySelector("button");
    const submit = async () => {
      if (!input.value.trim()) return;
      if (server) Connection.setServer(server.value);
      Connection.setToken(input.value);
      try {
        onReady(await getJSON(Connection.route("snapshot")));
      } catch (err) {
        showGate(String(err.message || err));
      }
    };
    button.addEventListener("click", submit);
    for (const field of [server, input]) {
      if (!field) continue;
      field.addEventListener("keydown", (e) => {
        if (e.key === "Enter") submit();
      });
    }
  }

  /* -- live stream ------------------------------------------------------- */

  const listeners = new Set();
  const resyncers = new Set();
  let source = null;
  let retryDelay = 1000;
  let dropped = false; // the stream failed since it last opened
  let status = { state: "", label: "Connecting" };

  /* Every page part showing the connection light ([data-connection]) — a
     tab mounted later is painted by decorate(). */
  function setStatus(state, label) {
    status = { state, label };
    document.querySelectorAll("[data-connection]").forEach(paintStatus);
  }

  function paintStatus(node) {
    node.innerHTML = `<span class="dot ${status.state}"></span>${status.label}`;
  }

  /* Events sent while the stream was down are gone, so anything kept from
     them is stale: tell the pages to re-read. Tabs stay open for days. */
  function resynced() {
    resyncers.forEach((fn) => {
      try {
        fn();
      } catch (err) {
        console.error("resync failed", err);
      }
    });
  }

  function connect() {
    if (source) source.close();
    // EventSource cannot send headers, so the token rides in the query.
    source = new EventSource(
      `${Connection.endpoint("stream")}?token=${encodeURIComponent(Connection.token())}`
    );

    source.onopen = () => {
      retryDelay = 1000;
      setStatus("live", "Online");
      if (dropped) {
        dropped = false;
        resynced();
      }
    };

    source.onmessage = (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      listeners.forEach((fn) => {
        try {
          fn(frame);
        } catch (err) {
          console.error("event listener failed", err);
        }
      });
    };

    source.onerror = () => {
      setStatus("down", "Reconnecting");
      dropped = true;
      source.close();
      source = null;
      // Back off up to 15s so a stopped server does not spin the browser.
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.7, 15000);
    };
  }

  const onEvent = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };

  /** fn() after the stream comes back from a drop, or after signing in again. */
  const onResync = (fn) => {
    resyncers.add(fn);
    return () => resyncers.delete(fn);
  };

  /* -- boot -------------------------------------------------------------- */

  /**
   * Sign in (or show the connect screen), open the event stream, then
   * onReady(snapshot) with the snapshot that proved the token works — so a
   * page needn't fetch it again. Only ever once: signing in again later
   * (a token was rejected) reconnects and resyncs instead.
   */
  async function start(onReady) {
    let started = false;
    const ready = (snapshot) => {
      hideGate();
      connect();
      if (started) return resynced();
      started = true;
      onReady(snapshot);
    };
    wireGate(ready);
    if (!Connection.token()) {
      showGate();
      return;
    }
    let snapshot;
    try {
      snapshot = await getJSON(Connection.route("snapshot"));
    } catch (err) {
      // Unreachable is as likely as unauthorised when the frontend is opened
      // on its own, so both lead back to the connect screen.
      if (String(err.message) !== "unauthorised") showGate(err.message);
      return;
    }
    ready(snapshot);
  }

  /* -- helpers ----------------------------------------------------------- */

  function toast(message, kind = "") {
    const host = document.getElementById("toasts");
    if (!host) return;
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity .3s";
      setTimeout(() => el.remove(), 320);
    }, 5200);
  }

  function ago(iso) {
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return "";
    const seconds = Math.max(0, (Date.now() - then) / 1000);
    if (seconds < 45) return `${Math.round(seconds)}s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
    return `${Math.round(seconds / 86400)}d ago`;
  }

  function bytes(value) {
    if (value === null || value === undefined) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = Number(value);
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit += 1;
    }
    return `${size < 10 && unit > 0 ? size.toFixed(1) : Math.round(size)} ${units[unit]}`;
  }

  const escape = (text) =>
    String(text ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    );

  /* Classify an event for colour: what kind of thing just happened. */
  function tone(type) {
    if (type.endsWith(".completed") || type === "step.completed") return "ok";
    if (type.endsWith(".failed") || type === "error") return "bad";
    if (type.includes("approval") || type.includes("retry")) return "warn";
    if (type.includes("planning")) return "plan";
    return "";
  }

  /** A page's name from its path: "/dash/", "/dash/index.html" → "index". */
  function pageName(path) {
    // "/dash/", "/dash/index.html" and "/dash/terminal" vs "terminal.html" are
    // the same pages, so compare on the page name alone.
    return path.replace(/\/$/, "/index").split("/").pop().replace(/\.html$/, "") || "index";
  }

  /**
   * Bring a page part's chrome up to date: the nav link to the page it
   * belongs to (`page`, default this address's), links into the backend,
   * and the connection light. Tabs mounted later are decorated on arrival.
   */
  function decorate(root = document, page = pageName(window.location.pathname)) {
    root.querySelectorAll(".nav a").forEach((link) => {
      link.classList.toggle("active", pageName(new URL(link.href).pathname) === page);
    });
    // Links into the backend itself (API docs) follow whichever server this
    // page is connected to.
    root.querySelectorAll("[data-route-link]").forEach((link) => {
      link.href = Connection.endpoint(link.dataset.routeLink);
    });
    root.querySelectorAll("[data-connection]").forEach(paintStatus);
  }

  document.addEventListener("DOMContentLoaded", () => decorate());

  return {
    api,
    getJSON,
    postJSON,
    start,
    onEvent,
    onResync,
    decorate,
    pageName,
    toast,
    ago,
    bytes,
    escape,
    tone,
    setStatus,
    url: (path) => Connection.url(path),
    route: (name, params) => Connection.route(name, params),
    endpoint: (name, params) => Connection.endpoint(name, params),
    load: (key) => Connection.load(key),
    save: (key, value) => Connection.save(key, value),
    get token() {
      return Connection.token();
    },
  };
})();
