/*
 * What the Mothership knows, in one place: requests, approvals, terminals,
 * controls and projects, recent events, machine stats. Each part is loaded
 * from its route and re-loaded (debounced) when the event stream says it
 * changed; views subscribe with onChange() and redraw.
 */

import { Hud } from "../lib/hud.js";

export const state = {
  tasks: [],
  approvals: [],
  terminals: [],
  controls: [],
  projects: [],
  claude: false,
  connections: null,
  phone: null,
  events: [],
  stats: null,
  system: null,
};

const listeners = new Set();

export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function changed(part) {
  listeners.forEach((fn) => {
    try {
      fn(part);
    } catch (err) {
      console.error("mothership view failed", err);
    }
  });
}

const loaders = {
  async tasks() {
    state.tasks = await Hud.getJSON(`${Hud.route("tasks")}?limit=500`);
  },
  async approvals() {
    state.approvals = await Hud.getJSON(Hud.route("approvals"));
  },
  async terminals() {
    state.terminals = await Hud.getJSON(Hud.route("terminals"));
  },
  async mothership() {
    const data = await Hud.getJSON(Hud.route("mothership"));
    state.controls = data.controls || [];
    state.projects = data.projects || [];
    state.claude = Boolean(data.claude);
  },
  async connections() {
    state.connections = await Hud.getJSON(Hud.route("connections"));
  },
  async phone() {
    state.phone = await Hud.getJSON(Hud.route("phone"));
  },
  async stats() {
    state.stats = await Hud.getJSON(Hud.route("stats"));
  },
  async system() {
    state.system = await Hud.getJSON(Hud.route("system"));
  },
};

/** Load one part now. */
export async function load(part) {
  try {
    await loaders[part]();
    changed(part);
  } catch (err) {
    if (String(err.message) !== "unauthorised") console.warn(`couldn't load ${part}`, err);
  }
}

const timers = {};

/** Load one part soon — a burst of events becomes one request. */
export function reload(part, wait = 250) {
  clearTimeout(timers[part]);
  timers[part] = setTimeout(() => load(part), wait);
}

/** Load everything; `snapshot` (when the caller has a fresh one) saves a request. */
export async function loadAll(snapshot) {
  const fresh = snapshot || (await Hud.getJSON(Hud.route("snapshot")));
  state.events = (fresh.events || []).slice(-80);
  await Promise.all(Object.keys(loaders).map((part) => loaders[part]().catch(() => {})));
  changed("all");
}

const taskTimers = {};

/**
 * Re-read one request — an event names the task it is about, and that one
 * is all that changed, so there is no need to fetch every request again.
 */
function reloadTask(id) {
  clearTimeout(taskTimers[id]);
  taskTimers[id] = setTimeout(async () => {
    delete taskTimers[id];
    try {
      const fresh = await Hud.getJSON(Hud.route("task", { id }));
      const at = state.tasks.findIndex((t) => t.id === id);
      if (at >= 0) state.tasks[at] = fresh;
      else state.tasks.unshift(fresh); // a new one: the newest of all
      changed("tasks");
    } catch (_) {
      reload("tasks"); // gone or unreachable: the whole list will say
    }
  }, 250);
}

/** Feed one live event in: remember it, and reload what it touched. */
export function hear(frame) {
  state.events.push(frame);
  if (state.events.length > 120) state.events.shift();
  const type = frame.type || "";
  if (type.startsWith("task.") || type.startsWith("step.")) {
    if (frame.task_id) reloadTask(frame.task_id);
    else reload("tasks");
  }
  if (type.startsWith("approval.")) reload("approvals", 80);
  if (type.startsWith("terminal.")) reload("terminals");
  if (type === "mothership.updated") reload("mothership", 80);
  if (type === "connections.updated") {
    reload("connections", 80);
    reload("system", 80);
  }
  changed("events");
}

/* -- lookups ----------------------------------------------------------------- */

export const project = (id) => state.projects.find((p) => p.id === id);
export const control = (id) => state.controls.find((c) => c.id === id);
export const task = (id) => state.tasks.find((t) => t.id === id);

/** The terminal a command control is running in, if any. */
export const controlTerminal = (id) =>
  state.terminals.find((t) => t.control === id && t.status === "running");

export const groups = () =>
  [...new Set(state.controls.map((c) => c.group).filter(Boolean))].sort((a, b) => a.localeCompare(b));
