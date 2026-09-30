/*
 * Frontend settings — the one file to edit when handing this UI to someone,
 * or reusing it for another project. Anything left out keeps its default
 * (see js/lib/connection.js); every key below is optional.
 *
 * When the Jarvis backend serves these files itself (at /dash/), it serves
 * this file with `server: ""` appended, so its own pages always talk back to
 * it and the value here only matters for a standalone copy.
 */
window.HUD_CONFIG = {
  // The backend these pages talk to:
  //   "http://127.0.0.1:8765"     a backend on this same computer
  //   "http://192.168.1.20:8765"  one elsewhere on the network (it needs
  //                               api.host: 0.0.0.0, and this page's origin
  //                               in api.cors_origins)
  // Anyone can still change it on the connect screen, or by opening a page
  // with ?server=http://host:port in the address bar.
  server: "http://127.0.0.1:8765",

  // The name shown in the header, the connect screen and the tab title.
  appName: "JARVIS",

  // Prefix for what this UI stores in the browser (token, server, mute).
  // Give each project its own, so they don't share a login.
  // storagePrefix: "jarvis.",

  // Replaces the connect screen's "where do I get a token" sentence.
  // tokenHint: "Ask the backend's owner for its API token.",

  // Where each piece of data lives on the backend. Only needed when the
  // backend is not Jarvis — see API.md for what each route must return.
  // routes: {
  //   snapshot: "/dash/api/snapshot",
  //   stream: "/dash/api/stream",
  //   approval: "/approvals/{id}",
  // },
};
