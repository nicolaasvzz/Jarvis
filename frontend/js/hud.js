/*
 * Shared dashboard runtime: authentication, the live event stream, and the
 * small helpers every page needs.
 *
 * All three pages are renderers over one server-sent-event feed. This module
 * owns the connection to it — including reconnecting, which matters more than
 * it sounds: Jarvis runs for days, laptops sleep, and a HUD that silently
 * stopped updating is worse than one that says it is offline.
 *
 * Which backend to talk to, and the token, come from js/connection.js, which
 * every page loads first.
 */

export const Hud = (() => {
  const Connection = window.JarvisConnection;

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
      throw new Error(`Could not reach Jarvis at ${Connection.describe()}.`);
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
      throw new Error(detail);
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
        await getJSON("/dash/api/snapshot");
        hideGate();
        onReady();
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
  let source = null;
  let retryDelay = 1000;
  let statusEl = null;

  function setStatus(state, label) {
    if (!statusEl) statusEl = document.querySelector("[data-connection]");
    if (!statusEl) return;
    statusEl.innerHTML = `<span class="dot ${state}"></span>${label}`;
  }

  function connect() {
    if (source) source.close();
    // EventSource cannot send headers, so the token rides in the query.
    source = new EventSource(
      Connection.url(`/dash/api/stream?token=${encodeURIComponent(Connection.token())}`)
    );

    source.onopen = () => {
      retryDelay = 1000;
      setStatus("live", "Online");
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

  /* -- boot -------------------------------------------------------------- */

  async function start(onReady) {
    wireGate(() => {
      connect();
      onReady();
    });
    if (!Connection.token()) {
      showGate();
      return;
    }
    try {
      await getJSON("/dash/api/snapshot");
    } catch (err) {
      // Unreachable is as likely as unauthorised when the frontend is opened
      // on its own, so both lead back to the connect screen.
      if (String(err.message) !== "unauthorised") showGate(err.message);
      return;
    }
    hideGate();
    connect();
    onReady();
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

  function markNav() {
    // "/dash/", "/dash/index.html" and "/dash/files" vs "files.html" are
    // the same pages, so compare on the page name alone.
    const page = (path) =>
      path.replace(/\/$/, "/index").split("/").pop().replace(/\.html$/, "") || "index";
    const here = page(window.location.pathname);
    document.querySelectorAll(".nav a").forEach((link) => {
      if (page(new URL(link.href).pathname) === here) link.classList.add("active");
    });
    // Links into the backend itself (API docs) follow whichever server this
    // page is connected to.
    document.querySelectorAll("[data-api-link]").forEach((link) => {
      link.href = Connection.url(link.dataset.apiLink);
    });
  }

  document.addEventListener("DOMContentLoaded", markNav);

  return {
    api,
    getJSON,
    postJSON,
    start,
    onEvent,
    toast,
    ago,
    bytes,
    escape,
    tone,
    setStatus,
    url: (path) => Connection.url(path),
    get token() {
      return Connection.token();
    },
  };
})();
