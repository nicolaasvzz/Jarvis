/*
 * The Mothership: one page, several views, switched by the address's #hash —
 * #/overview, #/requests, #/approvals, #/controls, #/projects/<id>.
 *
 * Everything shown comes from state.js, which keeps itself current from the
 * event stream; when a part changes, the views that use it redraw. A view
 * isn't redrawn while you are typing in it, so live updates never steal
 * your cursor — it catches up when you leave the field.
 */

import { Hud } from "../lib/hud.js";
import { state, onChange, loadAll, load, hear, controlTerminal } from "./state.js";
import { esc, $, $$, icon, ago, act, refreshDrawer, closeDrawer } from "./ui.js";
import * as overview from "./overview.js";
import * as requests from "./requests.js";
import * as controls from "./controls.js";
import * as projects from "./projects.js";
import * as connections from "./connections.js";
import { editProject } from "./projects.js";
import { openTask } from "./requests.js";

const approvals = {
  title: () => "Approvals",
  render(root) {
    const recent = state.events
      .filter((e) => ["approval.resolved", "approval.auto"].includes(e.type))
      .slice(-12)
      .reverse();
    root.innerHTML = `
      <div class="page-head"><div><div class="kicker">General</div><h1>Approvals</h1>
        <p class="lede">What Jarvis wants to run, waiting for you. Read-only commands and
        trusted controls run without asking; they're listed below too. On your phone,
        use the <a href="approve.html">phone page</a>.</p></div></div>
      <div class="approval-list">${state.approvals.map(approvalCard).join("") ||
        '<div class="empty-card">Nothing waiting.</div>'}</div>
      <section class="card"><header class="card-head"><h2>Recently</h2></header>
        <ul class="activity">${recent
          .map((e) => `<li><span class="avatar ${e.type === "approval.auto" ? "plan" : "ok"}">✓</span>
            <span><span class="act-msg">${esc(e.message)}</span><small class="muted">${esc(ago(e.created_at))}</small></span></li>`)
          .join("") || '<li class="muted">Nothing yet.</li>'}</ul></section>`;
    wireApprovals(root);
  },
};

const VIEWS = {
  overview: { module: overview, parts: ["all", "tasks", "approvals", "terminals", "mothership", "events", "stats", "system"] },
  requests: { module: requests, parts: ["all", "tasks", "approvals"] },
  approvals: { module: approvals, parts: ["all", "approvals", "events"] },
  controls: { module: controls, parts: ["all", "mothership", "terminals"] },
  projects: { module: projects, parts: ["all", "mothership", "terminals"] },
  connections: { module: connections, parts: ["all", "connections", "phone"] },
};

let current = { name: null, params: {}, cleanup: null };
let stale = false;

/* -- routing ------------------------------------------------------------------- */

function parseHash() {
  const [path, query = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
  const [name, id] = path.split("/");
  const params = Object.fromEntries(new URLSearchParams(query));
  if (id) params.id = decodeURIComponent(id);
  return { name: VIEWS[name] ? name : "overview", params };
}

function show() {
  const { name, params } = parseHash();
  if (current.cleanup) current.cleanup();
  current = { name, params, cleanup: null };
  draw(true);
  $(".ms-view").scrollTop = 0;
  $("#ms-side").classList.remove("open");
}

function draw(force = false) {
  const root = $("#ms-view");
  const active = document.activeElement;
  const typing = active && root.contains(active) && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
  if (typing && !force) {
    stale = true;
    return;
  }
  stale = false;
  const view = VIEWS[current.name].module;
  if (current.cleanup) current.cleanup();
  const scroll = root.scrollTop;
  current.cleanup = view.render(root, current.params) || null;
  if (!force) root.scrollTop = scroll;
  $("#ms-crumb").textContent = view.title(current.params);
  markNav();
}

function markNav() {
  $$(".ms-nav a").forEach((a) => {
    const href = a.getAttribute("href");
    const here =
      (current.name === "projects" && href === `#/projects/${current.params.id}`) ||
      href === `#/${current.name}`;
    a.classList.toggle("active", here);
  });
}

/* -- chrome: sidebar, badges, approvals banner -------------------------------------- */

function sidebar() {
  $("#ms-projects-nav").innerHTML =
    state.projects
      .map((p) => {
        const open = (p.ideas || []).filter((i) => !i.done).length;
        const busy = state.terminals.some((t) => t.project === p.id && t.running);
        return `<a href="#/projects/${esc(p.id)}">
          <span class="dot-hue" style="--hue:${Number.isFinite(p.hue) ? p.hue : 190}"></span>${esc(p.name)}
          ${busy ? '<span class="ms-badge live">●</span>' : open ? `<span class="ms-badge">${open}</span>` : ""}</a>`;
      })
      .join("") || '<span class="ms-nav-empty">No projects yet</span>';
  badge("#nav-running", state.tasks.filter((t) => ["running", "pending"].includes(t.status)).length);
  badge("#nav-approvals", state.approvals.length);
  badge("#ms-bell-count", state.approvals.length);
  badge("#nav-controls", state.controls.length);
  badge("#nav-terminals", state.terminals.length);
  const brain = state.system && state.system.brain;
  if (brain) {
    $("#ms-model").textContent = `${brain.name || "Jarvis"} · ${brain.model || ""}`;
    const tag = $("#nav-brain");
    tag.hidden = false;
    tag.textContent = brain.name || brain.provider || "";
    tag.classList.toggle("hot", !brain.connected);
  }
  markNav();
  waitingBanner();
}

function badge(selector, count) {
  const node = $(selector);
  node.hidden = !count;
  node.textContent = count ? String(count) : "";
}

let bannerKey = "";

function waitingBanner() {
  const host = $("#ms-waiting");
  const show = state.approvals.length && current.name !== "approvals";
  host.hidden = !show;
  // Redraw only when the set of approvals changes, so a button never
  // moves (or vanishes) under the pointer while other updates stream in.
  const key = show ? state.approvals.map((a) => a.id).join() : "";
  if (!show || key === bannerKey) {
    bannerKey = key;
    return;
  }
  bannerKey = key;
  host.innerHTML = state.approvals.map(approvalCard).join("");
  wireApprovals(host);
}

function approvalCard(a) {
  const args = a.arguments || {};
  const command = args.command || args.text || "";
  return `<div class="approval-card" data-approval="${esc(a.id)}">
    <span class="approval-icon">${icon("shield")}</span>
    <div class="approval-text"><b>${esc(a.tool)}</b> ${esc(a.reason)}
      ${command ? `<code>${esc(command)}</code>` : ""}
      <small class="muted">${esc(ago(a.created_at))} · <button class="link" data-open-task="${esc(a.task_id)}">see request</button></small></div>
    <div class="btn-row"><button class="btn ok" data-decide="allow">${icon("check")} Allow</button>
      <button class="btn bad ghost" data-decide="deny">Deny</button></div></div>`;
}

function wireApprovals(root) {
  $$("[data-decide]", root).forEach((button) =>
    button.addEventListener("click", async () => {
      const card = button.closest("[data-approval]");
      card.querySelectorAll("button").forEach((b) => (b.disabled = true));
      await act(
        Hud.postJSON(Hud.route("approval", { id: card.dataset.approval }), {
          decision: button.dataset.decide,
        })
      );
      load("approvals");
    })
  );
  $$("[data-open-task]", root).forEach((b) =>
    b.addEventListener("click", () => openTask(b.dataset.openTask))
  );
}

/* -- the ask bar -------------------------------------------------------------------- */

function askBar() {
  const input = $("#ms-ask");
  const wrap = input.closest(".ms-ask");
  const menu = document.createElement("div");
  menu.className = "ms-suggest";
  menu.hidden = true;
  wrap.appendChild(menu);

  const matches = () => {
    const q = input.value.trim().toLowerCase();
    if (!q) return [];
    return state.controls
      .filter((c) => c.kind !== "idea" && c.action && `${c.name} ${c.group || ""}`.toLowerCase().includes(q))
      .slice(0, 5);
  };
  const paint = () => {
    const list = matches();
    menu.hidden = !list.length;
    menu.innerHTML = list
      .map((c) => `<button type="button" data-control="${esc(c.id)}">${icon("play")} ${esc(c.name)}
        <small class="muted">${esc(c.group || "")}</small></button>`)
      .join("") + (list.length ? `<div class="ms-suggest-hint">Enter asks Jarvis instead</div>` : "");
    $$("[data-control]", menu).forEach((b) =>
      b.addEventListener("mousedown", async (event) => {
        event.preventDefault();
        input.value = "";
        menu.hidden = true;
        const c = state.controls.find((x) => x.id === b.dataset.control);
        const result = await act(Hud.postJSON(Hud.route("msControlRun", { id: c.id }), {}), `Ran “${c.name}”.`);
        if (result && result.url) window.open(result.url, "_blank", "noopener");
        if (result && result.task_id) setTimeout(() => openTask(result.task_id), 300);
      })
    );
  };
  input.addEventListener("input", paint);
  input.addEventListener("blur", () => setTimeout(() => (menu.hidden = true), 120));
  input.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" || !input.value.trim()) return;
    const text = input.value.trim();
    input.value = "";
    menu.hidden = true;
    const heard = await act(Hud.postJSON(Hud.route("command"), { text, submit: true }));
    if (heard && heard.task_id) {
      await load("tasks");
      openTask(heard.task_id);
    }
  });
  document.addEventListener("keydown", (event) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (event.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) {
      event.preventDefault();
      input.focus();
    }
  });
}

/* -- boot ----------------------------------------------------------------------------- */

Hud.start(async () => {
  await loadAll();
  window.addEventListener("hashchange", show);
  show();
  sidebar();
  askBar();

  onChange((part) => {
    sidebar();
    if (VIEWS[current.name].parts.includes(part)) draw();
    refreshDrawer();
  });
  Hud.onEvent(hear);

  // Catch up after typing; keep stats and hand-edited controls current.
  document.addEventListener("focusout", () => setTimeout(() => stale && draw(), 50));
  setInterval(() => !document.hidden && current.name === "overview" && load("stats"), 4000);
  setInterval(() => !document.hidden && load("mothership"), 10000);

  $("#ms-burger").addEventListener("click", () => $("#ms-side").classList.toggle("open"));
  $("#nav-new-project").addEventListener("click", () => editProject());
  window.addEventListener("hashchange", closeDrawer);
});

// Exported for the console, when poking at a running page.
window.Mothership = { state, controlTerminal };
