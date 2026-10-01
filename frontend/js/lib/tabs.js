/*
 * Tabs: the Core, Terminal and Mothership pages as one live page.
 *
 * Each tab is still its own HTML file — open any of them directly, bookmark
 * one, reload it — but moving between them no longer loads a page. The first
 * visit to a tab fetches its file, lifts out its screen (the [data-screen]
 * element), adds the stylesheets and scripts it needs, and mounts its module.
 * From then on the screen is only hidden and shown, so switching is instant:
 * the sphere keeps turning where it was, terminals keep their scrollback,
 * the voice keeps listening, and there is one event stream however many
 * tabs have been opened. The address bar still follows along
 * (history.pushState), so Back, Forward and reload behave as before.
 *
 * Shortly after the first screen is up, the others are fetched in the
 * background — file, stylesheets, scripts, module code — so even a first
 * visit is quick. A module that sets `early` is mounted then too, hidden:
 * the Mothership does, so its numbers are already there when you arrive.
 *
 * A screen's module (its data-module attribute) exports:
 *   mount(snapshot)  once, before it is first shown; may be async
 *   show()           each time it becomes the visible tab, the first included
 *   hide()           when another tab takes over
 *   resync()         after the event stream dropped and came back
 *   early            true to be mounted ahead of its first visit
 * A hidden screen should skip drawing and polling, and catch up in show().
 */

import { Hud } from "./hud.js";

const Connection = window.HudConnection;

/* The tabbed pages: file name (as Hud.pageName gives it) → screen. Anything
   else — approve.html, the API docs, other sites — is an ordinary link. */
const SCREENS = { index: "core", terminal: "terminal", mothership: "mothership" };
const FILES = { core: "index.html", terminal: "terminal.html", mothership: "mothership.html" };

const base = new URL(".", window.location.href); // the folder the pages live in
const screens = new Map(); // name → {name, el, title, bodyClass, module, ...}
const docs = new Map(); // name → Promise<Document>
const fetching = new Map(); // name → Promise<screen>, while its file is being assembled
const leftAt = {}; // name → the address that tab was last showing
let active = null;
let ticket = 0; // the latest open(); an older one that finishes late gives way

/** Which tab a link opens, or null if it is not one of them. */
function screenFor(url) {
  if (url.origin !== base.origin) return null;
  const folder = url.pathname.slice(0, url.pathname.lastIndexOf("/") + 1);
  if (folder !== base.pathname) return null;
  return SCREENS[Hud.pageName(url.pathname)] || null;
}

function register(el) {
  const screen = {
    name: el.dataset.screen,
    el,
    title: el.dataset.title || "",
    bodyClass: el.dataset.bodyClass || "",
    module: null,
    mounting: null, // Promise of mount(), once started
  };
  screens.set(screen.name, screen);
  return screen;
}

function call(screen, hook) {
  try {
    const result = screen.module && screen.module[hook] && screen.module[hook]();
    if (result && result.catch) result.catch((err) => console.error(`${screen.name} ${hook}`, err));
  } catch (err) {
    console.error(`${screen.name} ${hook}`, err);
  }
}

/* -- assembling a tab from its file ------------------------------------------ */

function fetchDoc(name) {
  if (!docs.has(name)) {
    const loading = fetch(new URL(FILES[name], base))
      .then((response) => {
        if (!response.ok) throw new Error(`${FILES[name]}: ${response.status}`);
        return response.text();
      })
      .then((html) => new DOMParser().parseFromString(html, "text/html"));
    loading.catch(() => docs.delete(name)); // try again next time
    docs.set(name, loading);
  }
  return docs.get(name);
}

/** The page's stylesheets, in the page's own order relative to ours. */
function addStyles(doc) {
  const live = () => [...document.querySelectorAll('link[rel="stylesheet"]')];
  const waits = [];
  let after = null;
  for (const link of doc.querySelectorAll('link[rel="stylesheet"]')) {
    const href = new URL(link.getAttribute("href"), base).href;
    let node = live().find((l) => l.href === href);
    if (!node) {
      node = document.createElement("link");
      node.rel = "stylesheet";
      node.href = href;
      waits.push(new Promise((resolve) => (node.onload = node.onerror = resolve)));
      const first = live()[0];
      if (after) after.after(node);
      else if (first) first.before(node);
      else document.head.appendChild(node);
    }
    after = node;
  }
  return Promise.all(waits);
}

/** Its classic scripts (libraries such as xterm.js), in order, once each. */
async function addScripts(doc) {
  for (const script of doc.querySelectorAll("script[src]")) {
    if (script.type === "module") continue;
    const src = new URL(script.getAttribute("src"), base).href;
    if ([...document.scripts].some((s) => s.src === src)) continue;
    await new Promise((resolve) => {
      const node = document.createElement("script");
      node.src = src;
      // A library that fails to load is the screen's to report (the
      // Terminal says so); the tab still opens.
      node.onload = node.onerror = resolve;
      document.body.appendChild(node);
    });
  }
}

/** Fetch a tab and put its screen in the page, hidden, with its code loaded. */
function fetchScreen(name) {
  if (screens.has(name)) return Promise.resolve(screens.get(name));
  if (!fetching.has(name)) {
    let el = null;
    const work = (async () => {
      const doc = await fetchDoc(name);
      const source = doc.querySelector(`[data-screen="${name}"]`);
      if (!source) throw new Error(`${FILES[name]} has no [data-screen="${name}"]`);
      await addStyles(doc);
      el = document.importNode(source, true);
      el.hidden = true;
      // Where it sits in its own page: before the shared toasts and connect screen.
      document.body.insertBefore(el, document.getElementById("toasts"));
      Connection.applyBranding(el);
      Hud.decorate(el, Hud.pageName(FILES[name]));
      await addScripts(doc);
      const module = await import(new URL(el.dataset.module, base).href);
      const screen = register(el);
      screen.module = module;
      return screen;
    })();
    work
      .catch(() => el && el.remove()) // a later open starts over
      .finally(() => fetching.delete(name));
    fetching.set(name, work);
  }
  return fetching.get(name);
}

function mountScreen(screen, snapshot) {
  if (!screen.mounting) {
    screen.mounting = (async () => {
      let fresh = snapshot;
      try {
        fresh = fresh || (await Hud.getJSON(Hud.route("snapshot")));
      } catch (err) {
        screen.mounting = null; // nothing ran yet; the next open tries again
        throw err;
      }
      await screen.module.mount(fresh);
    })();
  }
  return screen.mounting;
}

/* -- switching ----------------------------------------------------------------- */

function swap(next) {
  if (active === next.name) return;
  const previous = screens.get(active);
  if (previous) {
    previous.el.hidden = true;
    if (previous.bodyClass) document.body.classList.remove(previous.bodyClass);
    call(previous, "hide");
  }
  if (next.bodyClass) document.body.classList.add(next.bodyClass);
  next.el.hidden = false;
  active = next.name;
  Connection.setTitle(next.title);
  call(next, "show");
}

async function open(name) {
  const mine = ++ticket;
  try {
    const screen = await fetchScreen(name);
    await mountScreen(screen);
    if (mine === ticket) swap(screen);
  } catch (err) {
    console.error(`couldn't open the ${name} tab here`, err);
    // The address already says where to go: load it the ordinary way.
    if (mine === ticket) window.location.reload();
  }
}

/**
 * Go to a tab (or anywhere else) by URL, as a link would. A tab opened by
 * its plain address comes back as you left it — the same Mothership view.
 */
export function go(target) {
  const url = new URL(target, window.location.href);
  const name = screenFor(url);
  if (!name || !active) {
    window.location.href = url.href;
    return;
  }
  // The address can be ahead of the screen while a tab is still arriving.
  const here = screenFor(new URL(window.location.href));
  if (here === active) leftAt[active] = window.location.href;
  if (name === active) {
    ticket += 1; // staying put: a tab still on its way must not take over
    if (here !== active) window.history.pushState(null, "", leftAt[name] || url.href);
    else if (url.hash && url.hash !== window.location.hash) window.location.hash = url.hash;
    return;
  }
  const plain = !url.hash && !url.search;
  window.history.pushState(null, "", plain && leftAt[name] ? leftAt[name] : url.href);
  open(name);
}

function wireLinks() {
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest && event.target.closest("a[href]");
    if (!link || (link.target && link.target !== "_self") || link.hasAttribute("download")) return;
    const url = new URL(link.href, window.location.href);
    const name = screenFor(url);
    if (!name) return;
    // The Mothership's own #/… links route inside it, as ever.
    if (name === active && url.hash) return;
    event.preventDefault();
    go(url);
  });
  window.addEventListener("popstate", () => {
    const name = screenFor(new URL(window.location.href));
    if (!name) return;
    if (name !== active) open(name);
    else ticket += 1; // back where we are: cancel a tab still arriving
  });
}

/* -- getting ahead ----------------------------------------------------------------- */

const idle = () =>
  new Promise((resolve) =>
    window.requestIdleCallback
      ? window.requestIdleCallback(() => resolve(), { timeout: 3000 })
      : setTimeout(resolve, 300)
  );

async function prefetch() {
  await new Promise((resolve) => setTimeout(resolve, 800));
  for (const name of Object.values(SCREENS)) {
    if (screens.has(name)) continue;
    await idle();
    try {
      const screen = await fetchScreen(name);
      if (screen.module.early) await mountScreen(screen);
    } catch (err) {
      console.warn(`couldn't prepare the ${name} tab ahead of time`, err);
    }
  }
}

/* -- boot ----------------------------------------------------------------------------- */

function boot() {
  const el = document.querySelector("[data-screen]");
  if (!el) return;
  const screen = register(el);
  active = screen.name;
  if (screen.bodyClass) document.body.classList.add(screen.bodyClass);
  // Fetch the code while signing in, not after.
  const code = import(new URL(el.dataset.module, base).href);
  Hud.start(async (snapshot) => {
    screen.module = await code;
    await mountScreen(screen, snapshot);
    call(screen, "show");
    wireLinks();
    Hud.onResync(() => screens.forEach((s) => s.mounting && call(s, "resync")));
    prefetch();
  });
}

boot();
