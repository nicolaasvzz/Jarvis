/*
 * A project's page: what it is, its folder, a live view of its status file
 * (refreshed every few seconds while you look), its controls, an ideas board
 * — each idea one click from Claude — its terminals, and its links.
 */

import { Hud } from "../lib/hud.js";
import { state, project as findProject } from "./state.js";
import {
  esc, $, $$, icon, ago, act, openForm, closeForm, showTerminal, drawCharts,
} from "./ui.js";
import { card as controlCard, wire as wireControls, editControl } from "./controls.js";
import { renderStatus } from "./status.js";

let statusTimer = null;
let lastStatus = { id: null, html: "" };

export function title(params) {
  const p = findProject(params.id);
  return p ? p.name : "Project";
}

export function render(root, params) {
  const p = findProject(params.id);
  if (!p) {
    root.innerHTML = `<div class="empty-card big"><h3>No such project</h3>
      <p>It may have been deleted.</p><a class="btn" href="#/overview">Back to the overview</a></div>`;
    return null;
  }
  const controls = state.controls.filter((c) => c.project === p.id);
  const terminals = state.terminals.filter((t) => t.project === p.id);
  const ideas = p.ideas || [];
  const open = ideas.filter((i) => !i.done);
  const hue = Number.isFinite(p.hue) ? p.hue : 190;
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="kicker"><span class="dot-hue" style="--hue:${hue}"></span>Project</div>
        <h1>${esc(p.name)}</h1>
        ${p.description ? `<p class="lede">${esc(p.description)}</p>` : ""}
        ${p.folder ? `<code class="folder">${icon("folder")} ${esc(p.folder)}</code>` : ""}
      </div>
      <div class="head-actions">
        <button class="btn" data-open-terminal>${icon("terminal")} Terminal here</button>
        <button class="btn claude" data-claude>${icon("claude")} Work on it with Claude</button>
        <button class="btn ghost" data-edit-project>${icon("edit")} Edit</button>
      </div>
    </div>

    <div class="cols">
      <div class="col-main">
        ${p.status_file ? `
        <section class="card">
          <header class="card-head"><h2>${icon("chart")} Live status</h2>
            <span class="muted" id="status-when">${esc(p.status_file)}</span></header>
          <div id="status-body">${lastStatus.id === p.id ? lastStatus.html : '<p class="muted">Reading…</p>'}</div>
        </section>` : ""}

        <section class="card">
          <header class="card-head"><h2>${icon("bolt")} Controls <span class="muted">${controls.length}</span></h2>
            <button class="btn small" data-new-control>${icon("plus")} Add control</button></header>
          ${controls.length
            ? `<div class="control-grid">${controls.map(controlCard).join("")}</div>`
            : `<p class="muted pad">No controls yet — add one for anything you'd press often here
               (start, stop, a backtest, open a report).</p>`}
        </section>
      </div>

      <div class="col-side">
        <section class="card">
          <header class="card-head"><h2>${icon("idea")} Ideas <span class="muted">${open.length} open</span></h2></header>
          <form class="idea-add" id="idea-add">
            <input id="idea-text" type="text" placeholder="An idea, a feature, a menu item…" autocomplete="off">
            <button class="btn small primary" type="submit">${icon("plus")}</button>
          </form>
          <ul class="ideas">${[...open, ...ideas.filter((i) => i.done)]
            .map((i) => `
            <li class="${i.done ? "done" : ""}" data-idea="${esc(i.id)}">
              <input type="checkbox" ${i.done ? "checked" : ""} data-done title="Done">
              <span class="idea-text">${esc(i.text)}
                ${i.by === "jarvis" ? '<span class="tag">from Jarvis</span>' : ""}
                <small class="muted">${esc(ago(i.created_at))}</small></span>
              ${i.done ? "" : `<button class="icon-btn" data-build-idea title="Build it with Claude">${icon("claude")}</button>`}
              <button class="icon-btn" data-delete-idea title="Delete">${icon("trash")}</button>
            </li>`)
            .join("") || '<li class="muted">No ideas yet. Jarvis adds them too: “note an idea for this project…”</li>'}
          </ul>
        </section>

        <section class="card">
          <header class="card-head"><h2>${icon("terminal")} Terminals <span class="muted">${terminals.length}</span></h2></header>
          <ul class="mini-list">${terminals
            .map((t) => `<li><button class="link" data-terminal="${esc(t.id)}">
              <span class="dot ${t.status !== "running" ? "down" : t.at_prompt ? "live" : "busy"}"></span>${esc(t.title)}</button>
              <small class="muted">${esc(t.state)}</small></li>`)
            .join("") || '<li class="muted">None open.</li>'}</ul>
        </section>

        ${(p.links || []).length ? `
        <section class="card">
          <header class="card-head"><h2>${icon("link")} Links</h2></header>
          <ul class="mini-list">${p.links
            .map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${icon("external")} ${esc(l.label)}</a></li>`)
            .join("")}</ul>
        </section>` : ""}
      </div>
    </div>`;

  wireControls(root);
  root.querySelector("[data-new-control]").addEventListener("click", () =>
    editControl(null, { project: p.id, group: p.name, kind: "command" })
  );
  root.querySelector("[data-edit-project]").addEventListener("click", () => editProject(p));
  root.querySelector("[data-open-terminal]").addEventListener("click", async () => {
    const t = await act(Hud.postJSON(Hud.route("msProjectTerminal", { id: p.id }), {}));
    if (t) showTerminal(t.id);
  });
  root.querySelector("[data-claude]").addEventListener("click", async (event) => {
    if (!state.claude) return Hud.toast("Claude Code isn't installed on this PC.", "bad");
    event.currentTarget.disabled = true;
    const t = await act(Hud.postJSON(Hud.route("msProjectClaude", { id: p.id }), {}));
    if (t) showTerminal(t.id);
  });
  $$("[data-terminal]", root).forEach((b) =>
    b.addEventListener("click", () => showTerminal(b.dataset.terminal))
  );

  const form = root.querySelector("#idea-add");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = form.querySelector("#idea-text");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await act(Hud.postJSON(Hud.route("msIdeas", { id: p.id }), { text }));
  });
  $$("[data-idea]", root).forEach((li) => {
    const idea = li.dataset.idea;
    li.querySelector("[data-done]").addEventListener("change", (e) =>
      act(Hud.postJSON(Hud.route("msIdea", { id: p.id, idea }), { done: e.target.checked }))
    );
    li.querySelector("[data-delete-idea]").addEventListener("click", () =>
      act(Hud.postJSON(Hud.route("msIdeaDelete", { id: p.id, idea }), {}))
    );
    const build = li.querySelector("[data-build-idea]");
    if (build)
      build.addEventListener("click", async () => {
        if (!state.claude) return Hud.toast("Claude Code isn't installed on this PC.", "bad");
        build.disabled = true;
        const t = await act(Hud.postJSON(Hud.route("msIdeaBuild", { id: p.id, idea }), {}));
        if (t) showTerminal(t.id);
      });
  });

  if (p.status_file) {
    pollStatus(p.id);
    statusTimer = setInterval(() => document.hidden || pollStatus(p.id), 5000);
  }
  drawCharts();
  return () => clearInterval(statusTimer);
}

async function pollStatus(id) {
  let status;
  try {
    status = await Hud.getJSON(Hud.route("msProjectStatus", { id }));
  } catch (err) {
    status = { available: false, note: String(err.message || err) };
  }
  const body = $("#status-body");
  if (!body) return;
  const html = !status.available
    ? `<p class="muted pad">${esc(status.note || "Nothing to show yet.")}</p>`
    : status.data !== undefined
    ? renderStatus(status.data)
    : `<pre class="status-text">${esc(status.text)}</pre>`;
  if (html !== lastStatus.html || lastStatus.id !== id) {
    body.innerHTML = html;
    lastStatus = { id, html };
    drawCharts();
  }
  const when = $("#status-when");
  if (when && status.modified) when.textContent = `updated ${ago(status.modified)}`;
}

/** The new/edit project dialog. */
export function editProject(existing) {
  const p = existing || { hue: Math.floor(Math.random() * 360) };
  const foot = openForm({
    title: existing ? `Edit ${existing.name}` : "New project",
    submit: existing ? "Save" : "Create",
    fields: [
      { name: "name", label: "Name", value: p.name, placeholder: "VelocityRacing" },
      { name: "description", label: "What is it?", type: "textarea", value: p.description,
        placeholder: "My sim racing brand — menus, merch ideas, the website." },
      { name: "folder", label: "Folder", value: p.folder,
        placeholder: "C:\\Users\\you\\projects\\something",
        hint: "Its terminals and command controls start here." },
      { name: "status_file", label: "Status file (optional)", value: p.status_file,
        placeholder: "live_state.json",
        hint: "A JSON file in that folder that the project keeps up to date — shown live here." },
      { name: "links", label: "Links (optional)", type: "textarea", rows: 2,
        value: (p.links || []).map((l) => `${l.label} | ${l.url}`).join("\n"),
        placeholder: "Website | https://example.com", hint: "One per line: label | address" },
      { name: "hue", label: "Colour", type: "select", value: String(p.hue ?? 190),
        options: [["190", "Cyan"], ["150", "Green"], ["40", "Amber"], ["330", "Pink"],
          ["270", "Violet"], ["210", "Blue"], ["0", "Red"]] },
    ],
    extra: existing ? `<button type="button" class="btn bad ghost" data-delete>${icon("trash")} Delete</button>` : "",
    onSubmit: async (v) => {
      if (!v.name) throw new Error("Give it a name.");
      const links = v.links
        .split("\n")
        .map((line) => line.split("|").map((s) => s.trim()))
        .filter(([label, url]) => label && url)
        .map(([label, url]) => ({ label, url }));
      const body = { ...v, hue: Number(v.hue), links };
      const saved = existing
        ? await Hud.postJSON(Hud.route("msProject", { id: existing.id }), body)
        : await Hud.postJSON(Hud.route("msProjects"), body);
      if (!existing) window.location.hash = `#/projects/${saved.id}`;
    },
  });
  const del = foot.querySelector("[data-delete]");
  if (del)
    del.addEventListener("click", async () => {
      if (!window.confirm(`Delete the project “${existing.name}”? Its controls stay, unfiled.`)) return;
      if (await act(Hud.postJSON(Hud.route("msProjectDelete", { id: existing.id }), {}), "Deleted.")) {
        closeForm();
        window.location.hash = "#/overview";
      }
    });
}
