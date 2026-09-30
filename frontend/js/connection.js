/*
 * Which Jarvis backend this page talks to, and the token it presents.
 *
 * The frontend is a folder of static files that can live in two places:
 *
 *   * served by the backend itself, at /dash/ — the backend then answers
 *     /dash/config.js with "same origin", so nothing needs setting;
 *   * opened on its own from any static web server (see README.md) — then it
 *     has to be told where the backend is.
 *
 * Where the backend is, highest precedence first:
 *
 *   1. ?server=http://host:port in the address bar (remembered afterwards)
 *   2. the server last entered on the connect screen, in this browser
 *   3. window.JARVIS_CONFIG.server, from config.js
 *   4. this page's own origin
 *
 * A classic script rather than a module, so the single-page HUD (plain
 * script) and the dashboard pages (modules) share one implementation.
 */
(function () {
  "use strict";

  var SERVER_KEY = "jarvis.server";
  var TOKEN_KEY = "jarvis.token";

  // localStorage can be missing or throw (private windows, blocked site
  // data). Fall back to memory so the page still works for this visit.
  var memory = {};
  function load(key) {
    try {
      return window.localStorage.getItem(key) || "";
    } catch (_) {
      return memory[key] || "";
    }
  }
  function save(key, value) {
    memory[key] = value;
    try {
      if (value) window.localStorage.setItem(key, value);
      else window.localStorage.removeItem(key);
    } catch (_) {
      /* memory already holds it */
    }
  }

  function tidy(value) {
    return String(value || "").trim().replace(/\/+$/, "");
  }

  // A server or token handed over in the URL (`jarvis dash` does this) wins
  // once, then is stored and scrubbed from the address bar so the token does
  // not linger in browser history.
  var here = new URL(window.location.href);
  var scrubbed = false;
  if (here.searchParams.has("server")) {
    save(SERVER_KEY, tidy(here.searchParams.get("server")));
    here.searchParams.delete("server");
    scrubbed = true;
  }
  if (here.searchParams.has("token")) {
    save(TOKEN_KEY, String(here.searchParams.get("token")).trim());
    here.searchParams.delete("token");
    scrubbed = true;
  }
  if (scrubbed) window.history.replaceState({}, "", here.toString());

  var configured = tidy(window.JARVIS_CONFIG && window.JARVIS_CONFIG.server);

  window.JarvisConnection = {
    /** The backend's base URL, or "" for this page's own origin. */
    server: function () {
      return tidy(load(SERVER_KEY)) || configured;
    },
    setServer: function (value) {
      save(SERVER_KEY, tidy(value));
    },
    /** Human-readable form of server(), for messages. */
    describe: function () {
      return this.server() || window.location.origin;
    },
    token: function () {
      return load(TOKEN_KEY);
    },
    setToken: function (value) {
      save(TOKEN_KEY, String(value || "").trim());
    },
    clearToken: function () {
      save(TOKEN_KEY, "");
    },
    /** An absolute URL for an API path such as "/tasks". */
    url: function (path) {
      return this.server() + path;
    },
  };
})();
