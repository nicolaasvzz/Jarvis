/*
 * Frontend settings. Edit this file before handing the frontend to someone.
 *
 * server: the Jarvis backend these pages talk to, e.g.
 *   "http://127.0.0.1:8765"      a backend on this same computer
 *   "http://192.168.1.20:8765"   a backend elsewhere on the network (that
 *                                backend needs api.host: 0.0.0.0, and this
 *                                page's origin in api.cors_origins)
 *
 * Anyone can still change it on the connect screen, or by opening a page
 * with ?server=http://host:port in the address bar.
 *
 * When the backend serves these files itself (at /dash/), it replaces this
 * file with one that says "same server", so this value is only used when
 * the frontend is opened on its own.
 */
window.JARVIS_CONFIG = {
  server: "http://127.0.0.1:8765",
};
