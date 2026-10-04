/*
 * Controls: buttons you make for anything — change the weather in a sim,
 * start the trading bot, open a page, or ask Jarvis something you ask often.
 * Jarvis can press them too when you ask by voice. A control that isn't
 * built yet is an "idea": Build with Claude opens Claude Code in a terminal
 * with a brief, and when it works Claude switches the control on.
 */

import { Hud } from "../lib/hud.js";
import { state, project as findProject, controlTerminal, groups } from "./state.js";
import { esc, $$, icon, ago, act, openForm, showTerminal } from "./ui.js";
import { openTask } from "./requests.js";

const KINDS = {
  idea: { label: "Idea", icon: "idea", run: "" },
  ask: { label: "Ask Jarvis", icon: "ask", run: "Ask" },
  command: { label: "Command", icon: "command", run: "Run" },
  link: { label: "Link", icon: "link", run: "Open" },
};

let groupFilter = "all";

export function title() {
  return "Controls";
}

export function render(root) {
  const all = state.controls;
  const names = groups();
  const shown = all.filter((c) => groupFilter === "all" || (c.group || "General") === groupFilter);
  const byGroup = new Map();
  for (const c of shown) {
    const g = c.group || "General";
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push(c);
  }
  const ideas = all.filter((c) => c.kind === "idea").length;
  root.innerHTML = `
    <div class="page-head">
      <div><div class="kicker">Workspace</div><h1>Controls</h1>
        <p class="lede">Buttons for anything on this PC. Jarvis presses them too — say
        “make it rain in BeamNG” and it runs the matching control.
        ${ideas ? `<b>${ideas}</b> ${ideas === 1 ? "is an idea" : "are ideas"} waiting to be built.` : ""}</p></div>
      <div class="head-actions"><button class="btn primary" id="new-control">${icon("plus")} New control</button></div>
    </div>
    ${names.length > 1 ? `<div class="chips">${["all", ...names, ...(all.some((c) => !c.group) ? ["General"] : [])]
      .map((g) => `<button class="chip ${groupFilter === g ? "on" : ""}" data-group="${esc(g)}">${esc(g === "all" ? "All" : g)}</button>`)
      .join("")}</div>` : ""}
    ${
      all.length
        ? [...byGroup].map(([g, list]) => `
          <section class="group">
            <h2 class="group-title">${esc(g)} <span class="muted">${list.length}</span></h2>
            <div class="control-grid">${list.map(card).join("")}</div>
          </section>`).join("")
        : `<div class="empty-card big">
            <h3>No controls yet</h3>
            <p>Start with an idea — “Rain in BeamNG”, “Start the trade bot”, “Night time in
            Assetto Corsa” — and press <b>Build with Claude</b> to make it real.</p>
            <button class="btn primary" data-new>${icon("plus")} New control</button>
          </div>`
    }`;
  root.querySelector("#new-control").addEventListener("click", () => editControl());
  const blank = root.querySelector("[data-new]");
  if (blank) blank.addEventListener("click", () => editControl());
  $$("[data-group]", root).forEach((b) =>
    b.addEventListener("click", () => {
      groupFilter = b.dataset.group;
      render(root);
    })
  );
  wire(root);
}

/** One control as a card; used here and on project pages. */
export function card(c) {
  const kind = KINDS[c.kind] || KINDS.idea;
  const running = controlTerminal(c.id);
  const busy = running && running.running;
  const owner = c.project ? findProject(c.project) : null;
  const main =
    c.kind === "idea" || !c.action
      ? `<button class="btn claude" data-build="${esc(c.id)}">${icon("claude")} Build with Claude</button>`
      : busy
      ? `<button class="btn bad" data-stop="${esc(c.id)}">${icon("stop")} Stop</button>
         <button class="btn ghost" data-restart="${esc(c.id)}" title="Stop it, then run it again">${icon("redo")} Restart</button>
         <button class="btn ghost" data-terminal="${esc(running.id)}">${icon("terminal")} View</button>`
      : `<button class="btn primary" data-run="${esc(c.id)}">${icon(c.kind === "link" ? "external" : "play")} ${esc(kind.run)}</button>`;
  return `
    <article class="control ${esc(c.kind)} ${busy ? "busy" : ""}">
      <header>
        <span class="control-icon">${icon(kind.icon)}</span>
        <span class="control-kind">${esc(kind.label)}</span>
        ${c.trusted && c.kind === "command" ? `<span class="tag" title="Jarvis may run this without asking">${icon("shield")} trusted</span>` : ""}
        <span class="grow"></span>
        <button class="icon-btn" data-edit="${esc(c.id)}" title="Edit">${icon("edit")}</button>
      </header>
      <h3>${esc(c.name)}</h3>
      ${c.description ? `<p class="control-desc">${esc(c.description)}</p>` : ""}
      ${c.action && c.kind !== "idea" ? `<code class="control-action" title="${esc(c.action)}">${esc(c.action)}</code>` : ""}
      <div class="control-meta">
        ${owner ? `<a class="tag" href="#/projects/${esc(owner.id)}">${icon("folder")} ${esc(owner.name)}</a>` : ""}
        ${busy ? `<span class="tag warn">running</span>` : ""}
        ${c.last_run ? `<span class="muted">ran ${esc(ago(c.last_run))}</span>` : ""}
      </div>
      <footer>
        ${main}
        ${c.kind !== "idea" && c.action ? `<button class="icon-btn" data-build="${esc(c.id)}" title="Improve it with Claude">${icon("claude")}</button>` : ""}
      </footer>
    </article>`;
}

/** Wire every control button inside `root`. */
export function wire(root) {
  $$("[data-run]", root).forEach((b) => b.addEventListener("click", () => run(b.dataset.run, b)));
  $$("[data-stop]", root).forEach((b) =>
    b.addEventListener("click", () =>
      act(Hud.postJSON(Hud.route("msControlStop", { id: b.dataset.stop }), {}), "Sent Ctrl+C.")
    )
  );
  $$("[data-restart]", root).forEach((b) =>
    b.addEventListener("click", () => {
      b.disabled = true;
      Hud.toast("Stopping it, then starting it again…");
      act(Hud.postJSON(Hud.route("msControlRestart", { id: b.dataset.restart }), {}), "Restarted.");
    })
  );
  $$("[data-build]", root).forEach((b) => b.addEventListener("click", () => build(b.dataset.build, b)));
  $$("[data-edit]", root).forEach((b) =>
    b.addEventListener("click", () => editControl(state.controls.find((c) => c.id === b.dataset.edit)))
  );
  $$("[data-terminal]", root).forEach((b) =>
    b.addEventListener("click", () => showTerminal(b.dataset.terminal))
  );
}

async function run(id, button) {
  const c = state.controls.find((x) => x.id === id);
  if (!c) return;
  if (button) button.disabled = true;
  const result = await act(Hud.postJSON(Hud.route("msControlRun", { id }), {}));
  if (button) button.disabled = false;
  if (!result) return;
  if (result.url) window.open(result.url, "_blank", "noopener");
  else if (result.task_id) {
    Hud.toast(`Asked Jarvis: ${c.action}`, "ok");
    setTimeout(() => openTask(result.task_id), 300);
  } else if (result.terminal) Hud.toast(`${c.name} is running in a terminal.`, "ok");
}

async function build(id, button) {
  if (!state.claude) {
    Hud.toast("Claude Code isn't installed on this PC — see claude.com/claude-code.", "bad");
    return;
  }
  if (button) button.disabled = true;
  const terminal = await act(Hud.postJSON(Hud.route("msControlBuild", { id }), {}));
  if (button) button.disabled = false;
  if (terminal) showTerminal(terminal.id);
}

/** The new/edit control dialog. `preset` fills a new one (e.g. from a project). */
export function editControl(existing, preset = {}) {
  const c = existing || { kind: "idea", ...preset };
  const projects = [["", "— none —"], ...state.projects.map((p) => [p.id, p.name])];
  const foot = openForm({
    title: existing ? `Edit “${existing.name}”` : "New control",
    submit: existing ? "Save" : "Create",
    fields: [
      { name: "name", label: "Name", value: c.name, placeholder: "Rain in BeamNG" },
      { name: "description", label: "What should it do?", type: "textarea", value: c.description,
        placeholder: "Switch BeamNG.drive's weather to heavy rain on the current map.",
        hint: "Claude builds it from this, and Jarvis matches your words against it." },
      { name: "kind", label: "What it does now", type: "select", value: c.kind,
        options: [["idea", "Nothing yet — it's an idea for Claude to build"],
          ["command", "Run a command in a terminal"], ["ask", "Ask Jarvis something"],
          ["link", "Open a web page"]] },
      { name: "action", value: c.action, show: (v) => v.kind !== "idea",
        label: (v) => ({ command: "Command line", ask: "What to ask Jarvis", link: "Web address" })[v.kind] || "Action",
        placeholder: "e.g. .\\run-local.ps1 -Mode backtest" },
      { name: "group", label: "Group", value: c.group, list: groups(),
        placeholder: "BeamNG.drive, Assetto Corsa, TradeBot…" },
      { name: "project", label: "Project", type: "select", value: c.project || "", options: projects,
        hint: "Command controls run in the project's folder." },
      { name: "trusted", label: "Jarvis may run this without asking me", type: "checkbox",
        value: c.trusted, show: (v) => v.kind === "command",
        hint: "For harmless buttons you'll say out loud often, like weather changes." },
    ],
    extra: existing ? `<button type="button" class="btn bad ghost" data-delete>${icon("trash")} Delete</button>` : "",
    onSubmit: async (v) => {
      if (!v.name) throw new Error("Give it a name.");
      if (v.kind !== "idea" && !v.action) throw new Error("Fill in what it runs, or make it an idea.");
      const body = { ...v, action: v.kind === "idea" ? v.action || "" : v.action };
      if (existing) await Hud.postJSON(Hud.route("msControl", { id: existing.id }), body);
      else await Hud.postJSON(Hud.route("msControls"), body);
      Hud.toast(existing ? "Saved." : `Created “${v.name}”.`, "ok");
    },
  });
  const del = foot.querySelector("[data-delete]");
  if (del)
    del.addEventListener("click", async () => {
      if (!window.confirm(`Delete “${existing.name}”?`)) return;
      if (await act(Hud.postJSON(Hud.route("msControlDelete", { id: existing.id }), {}), "Deleted."))
        document.querySelector("#ms-modal").hidden = true;
    });
}
