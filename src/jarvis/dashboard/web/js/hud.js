/*
 * Shared dashboard runtime: authentication, the live event stream, and the
 * small helpers every page needs.
 *
 * All three pages are renderers over one server-sent-event feed. This module
 * owns the connection to it — including reconnecting, which matters more than
 * it sounds: Jarvis runs for days, laptops sleep, and a HUD that silently
 * stopped updating is worse than one that says it is offline.
 */

export const Hud = (() => {
  const TOKEN_KEY = "jarvis.token";

  /* -- authentication ---------------------------------------------------- */

  function readToken() {
    // A token in the URL (from `jarvis dash`) wins once, then is stashed and
    // scrubbed from the address bar so it does not linger in history.
    const url = new URL(window.location.href);
    const fromUrl = url.searchParams.get("token");
    if (fromUrl) {
      localStorage.setItem(TOKEN_KEY, fromUrl);
      url.searchParams.delete("token");
      window.history.replaceState({}, "", url.toString());
      return fromUrl;
    }
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  let token = readToken();

  function setToken(value) {
    token = value.trim();
    localStorage.setItem(TOKEN_KEY, token);
  }

  function clearToken() {
    token = "";
    localStorage.removeItem(TOKEN_KEY);
  }

  /* -- fetch ------------------------------------------------------------- */

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body && !(options.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        Authorization: `Bearer ${token}`,
        ...(options.headers || {}),
      },
    });
    if (response.status === 401) {
      clearToken();
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
    const input = gate.querySelector("input");
    if (input) setTimeout(() => input.focus(), 40);
  }

  function hideGate() {
    const gate = document.getElementById("gate");
    if (gate) gate.style.display = "none";
  }

  function wireGate(onReady) {
    const gate = document.getElementById("gate");
    if (!gate) return;
    const input = gate.querySelector("input");
    const button = gate.querySelector("button");
    const submit = async () => {
      if (!input.value.trim()) return;
      setToken(input.value);
      try {
        await getJSON("/dash/api/snapshot");
        hideGate();
        onReady();
      } catch (err) {
        showGate(String(err.message || err));
      }
    };
    button.addEventListener("click", submit);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
    });
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
    source = new EventSource(
      `/dash/api/stream?token=${encodeURIComponent(token)}`
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
    if (!token) {
      showGate();
      return;
    }
    try {
      await getJSON("/dash/api/snapshot");
    } catch (err) {
      if (String(err.message) !== "unauthorised") {
        toast(`Could not reach Jarvis: ${err.message}`, "bad");
      }
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
    const here = window.location.pathname.replace(/\/$/, "");
    document.querySelectorAll(".nav a").forEach((link) => {
      const target = new URL(link.href).pathname.replace(/\/$/, "");
      if (target === here) link.classList.add("active");
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
    get token() {
      return token;
    },
  };
})();
