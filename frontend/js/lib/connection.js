/*
 * Settings, and the connection to the backend: which server, which token,
 * and which URL each piece of data lives at.
 *
 * Everything project-specific about this frontend is resolved here, from the
 * defaults below merged with window.HUD_CONFIG (config.js). Pages never
 * hard-code a URL or a name — they ask for a route by name — so pointing the
 * UI at a different backend, or renaming it, is a config.js edit.
 *
 * Which backend to talk to, highest precedence first:
 *
 *   1. ?server=http://host:port in the address bar (remembered afterwards)
 *   2. the server last entered on the connect screen, in this browser
 *   3. HUD_CONFIG.server, from config.js
 *   4. this page's own origin
 *
 * When the Jarvis backend serves these files itself it appends
 * `server: ""` (same origin) to config.js, so its own pages always talk back
 * to it.
 *
 * A classic script rather than a module, so a plain-script page could use
 * it as well as the dashboard pages (modules).
 */
(function () {
  "use strict";

  var DEFAULTS = {
    server: "",
    appName: "JARVIS",
    // Prefix for everything this UI keeps in localStorage (token, server,
    // mute), so two projects on one origin do not share a login.
    storagePrefix: "jarvis.",
    // Replaces the connect screen's explanation of where the token comes
    // from, when set. Plain text.
    tokenHint: "",
    // The backend API this UI calls, by name. See API.md for what each
    // must return. "{id}" is filled in by route(name, {id: ...}).
    routes: {
      snapshot: "/dash/api/snapshot",
      stream: "/dash/api/stream",
      stats: "/dash/api/stats",
      command: "/dash/api/command",
      listen: "/dash/api/listen",
      speak: "/dash/api/speak",
      terminals: "/dash/api/terminals",
      terminalStream: "/dash/api/terminals/{id}/stream",
      terminalInput: "/dash/api/terminals/{id}/input",
      terminalClose: "/dash/api/terminals/{id}/close",
      terminalExplain: "/dash/api/terminals/{id}/explain",
      terminalTrust: "/dash/api/terminals/{id}/trust",
      mothership: "/dash/api/mothership",
      msControls: "/dash/api/mothership/controls",
      msControl: "/dash/api/mothership/controls/{id}",
      msControlDelete: "/dash/api/mothership/controls/{id}/delete",
      msControlRun: "/dash/api/mothership/controls/{id}/run",
      msControlStop: "/dash/api/mothership/controls/{id}/stop",
      msControlBuild: "/dash/api/mothership/controls/{id}/build",
      msProjects: "/dash/api/mothership/projects",
      msProject: "/dash/api/mothership/projects/{id}",
      msProjectDelete: "/dash/api/mothership/projects/{id}/delete",
      msProjectStatus: "/dash/api/mothership/projects/{id}/status",
      msProjectTerminal: "/dash/api/mothership/projects/{id}/terminal",
      msProjectClaude: "/dash/api/mothership/projects/{id}/claude",
      msIdeas: "/dash/api/mothership/projects/{id}/ideas",
      msIdea: "/dash/api/mothership/projects/{id}/ideas/{idea}",
      msIdeaDelete: "/dash/api/mothership/projects/{id}/ideas/{idea}/delete",
      msIdeaBuild: "/dash/api/mothership/projects/{id}/ideas/{idea}/build",
      connections: "/dash/api/connections",
      connectionsTest: "/dash/api/connections/test",
      connectionsToken: "/dash/api/connections/token",
      restart: "/dash/api/restart",
      phone: "/dash/api/phone",
      phoneTailscale: "/dash/api/phone/tailscale",
      tasks: "/tasks",
      task: "/tasks/{id}",
      approval: "/approvals/{id}",
      approvals: "/approvals",
      system: "/system",
      docs: "/docs",
    },
  };

  var custom = window.HUD_CONFIG || {};
  var settings = {};
  Object.keys(DEFAULTS).forEach(function (key) {
    settings[key] = key in custom ? custom[key] : DEFAULTS[key];
  });
  settings.routes = {};
  var customRoutes = custom.routes || {};
  Object.keys(DEFAULTS.routes).concat(Object.keys(customRoutes)).forEach(function (name) {
    settings.routes[name] = name in customRoutes ? customRoutes[name] : DEFAULTS.routes[name];
  });

  // localStorage can be missing or throw (private windows, blocked site
  // data). Fall back to memory so the page still works for this visit.
  var memory = {};
  function load(key) {
    key = settings.storagePrefix + key;
    try {
      return window.localStorage.getItem(key) || "";
    } catch (_) {
      return memory[key] || "";
    }
  }
  function save(key, value) {
    key = settings.storagePrefix + key;
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
    save("server", tidy(here.searchParams.get("server")));
    here.searchParams.delete("server");
    scrubbed = true;
  }
  if (here.searchParams.has("token")) {
    save("token", String(here.searchParams.get("token")).trim());
    here.searchParams.delete("token");
    scrubbed = true;
  }
  if (scrubbed) window.history.replaceState({}, "", here.toString());

  var Connection = {
    settings: settings,
    load: load,
    save: save,

    /** The backend's base URL, or "" for this page's own origin. */
    server: function () {
      return tidy(load("server")) || tidy(settings.server);
    },
    setServer: function (value) {
      save("server", tidy(value));
    },
    /** Human-readable form of server(), for messages. */
    describe: function () {
      return this.server() || window.location.origin;
    },

    token: function () {
      return load("token");
    },
    setToken: function (value) {
      save("token", String(value || "").trim());
    },
    clearToken: function () {
      save("token", "");
    },

    /** The path for a named route, e.g. route("approval", {id: "a1"}). */
    route: function (name, params) {
      var path = settings.routes[name];
      if (!path) throw new Error("Unknown route: " + name);
      return path.replace(/\{(\w+)\}/g, function (_, key) {
        return encodeURIComponent(params && key in params ? params[key] : "");
      });
    },
    /** An absolute URL for an API path such as "/tasks". */
    url: function (path) {
      return this.server() + path;
    },
    /** url(route(name, params)) — the usual way to address the backend. */
    endpoint: function (name, params) {
      return this.url(this.route(name, params));
    },

    /**
     * A whole sign-in link pasted where the token goes (".../?token=…"):
     * take its token, and its server if it isn't this page's own. True if
     * it was one.
     */
    useLink: function (text) {
      var link;
      try {
        link = new URL(String(text || "").trim());
      } catch (_) {
        return false;
      }
      var token = link.searchParams.get("token");
      if (!token) return false;
      if (link.origin !== window.location.origin) this.setServer(link.origin);
      this.setToken(token);
      return true;
    },
  };

  /* -- installing as an app ---------------------------------------------- */

  // An iPhone keeps a home-screen app's storage apart from Safari's, so the
  // app would open logged out. The backend puts the token in the app's start
  // address when asked with it — only here, only for its own pages (another
  // server would just log the token), and only on iPhones and iPads.
  var apple =
    /iP(hone|ad|od)/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  function pointManifest() {
    var manifest = document.querySelector('link[rel="manifest"]');
    if (!apple || !manifest || Connection.server()) return;
    var token = Connection.token();
    manifest.href = "manifest.webmanifest" + (token ? "?token=" + encodeURIComponent(token) : "");
  }
  pointManifest();
  var setToken = Connection.setToken;
  Connection.setToken = function (value) {
    setToken.call(Connection, value);
    pointManifest();
  };

  /* -- branding --------------------------------------------------------- */

  // Elements marked data-app-name show the configured name; "dotted" spells
  // it J.A.R.V.I.S.-style. Titles are "<name> — <page>".
  function applyBranding() {
    var name = String(settings.appName || DEFAULTS.appName);
    document.querySelectorAll("[data-app-name]").forEach(function (el) {
      el.textContent =
        el.getAttribute("data-app-name") === "dotted"
          ? name.toUpperCase().split("").join(".") + "."
          : name;
    });
    var page = document.documentElement.getAttribute("data-page");
    document.title = page ? name + " — " + page : name;
    if (settings.tokenHint) {
      document.querySelectorAll("[data-token-hint]").forEach(function (el) {
        el.textContent = settings.tokenHint;
      });
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyBranding);
  } else {
    applyBranding();
  }

  window.HudConnection = Connection;
})();
