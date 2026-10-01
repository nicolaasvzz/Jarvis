/*
 * Requests: everything you (or a notice, a control, an Explain) asked Jarvis,
 * newest first, as a stack of cards — what was asked, what came back, which
 * tools it used. Click one for the whole story in the side drawer: the
 * answer in full, every step, and anything still waiting for approval.
 */

import { Hud } from "../lib/hud.js";
import { state, task as findTask } from "./state.js";
import {
  esc, $$, icon, pill, clock, dayLabel, took, seconds, plain, markdown, KIND_LABEL,
  openDrawer, act,
} from "./ui.js";

const KINDS = [
  ["all", "All"],
  ["asked", "Asked"],
  ["control", "Controls"],
  ["notice", "Notices"],
  ["explain", "Explain"],
];
const STATUSES = [
  ["any", "Any status"],
  ["running", "Running"],
  ["completed", "Done"],
  ["failed", "Failed"],
];

const view = { kind: "all", status: "any", query: "" };

export function title() {
  return "Requests";
}

export function render(root, params) {
  if (params.q !== undefined) view.query = params.q;
  const tasks = filtered();
  const days = new Map();
  for (const t of tasks) {
    const label = dayLabel(t.created_at);
    if (!days.has(label)) days.set(label, []);
    days.get(label).push(t);
  }
  root.innerHTML = `
    <div class="page-head">
      <div><div class="kicker">Requests</div><h1>Requests &amp; outputs</h1></div>
      <div class="head-actions">
        <label class="search">${icon("list")}<input id="req-search" type="search"
          placeholder="Search requests and answers" value="${esc(view.query)}"></label>
      </div>
    </div>
    <div class="chips" id="req-kinds">${KINDS.map(([k, l]) => chip("kind", k, l)).join("")}
      <span class="chip-gap"></span>${STATUSES.map(([k, l]) => chip("status", k, l)).join("")}
      <span class="chip-count">${tasks.length} of ${state.tasks.length}</span></div>
    <div class="stack">
      ${
        tasks.length
          ? [...days].map(([day, list]) => `
            <div class="stack-day">${esc(day)}</div>
            ${list.map(card).join("")}`).join("")
          : `<div class="empty-card">${
              state.tasks.length
                ? "Nothing matches — try another filter."
                : "No requests yet. Ask Jarvis something in the bar above."
            }</div>`
      }
    </div>`;

  $$("[data-chip]", root).forEach((button) =>
    button.addEventListener("click", () => {
      view[button.dataset.chip] = button.dataset.value;
      render(root, {});
    })
  );
  const search = root.querySelector("#req-search");
  search.addEventListener("input", () => {
    view.query = search.value;
    const at = search.selectionStart;
    render(root, {});
    const again = root.querySelector("#req-search");
    again.focus();
    again.setSelectionRange(at, at);
  });
  wireCards(root);
}

function chip(kind, value, label) {
  const on = view[kind] === value;
  return `<button class="chip ${on ? "on" : ""}" data-chip="${kind}" data-value="${value}">${esc(label)}</button>`;
}

function filtered() {
  const q = view.query.trim().toLowerCase();
  return state.tasks.filter((t) => {
    if (view.kind !== "all" && (t.kind || "asked") !== view.kind) return false;
    if (view.status === "running" && !["running", "pending"].includes(t.status)) return false;
    if (["completed", "failed"].includes(view.status) && t.status !== view.status) return false;
    if (!q) return true;
    return `${t.request} ${t.result || ""} ${t.error || ""}`.toLowerCase().includes(q);
  });
}

/** One request as a card in the stack. */
export function card(t) {
  const tools = [...new Set((t.steps || []).map((s) => s.tool))];
  const output = t.status === "failed" ? t.error : t.result;
  const time = took(t);
  return `
    <button class="req ${esc(t.status)}" data-task="${esc(t.id)}">
      <span class="req-bar"></span>
      <span class="req-main">
        <span class="req-top">
          <span class="req-ask">${esc(t.request)}</span>
          ${pill(t.status)}
        </span>
        ${output ? `<span class="req-out ${t.status === "failed" ? "bad" : ""}">${esc(plain(output))}</span>`
          : `<span class="req-out muted">${t.status === "running" ? "Working on it…" : "Waiting…"}</span>`}
        <span class="req-meta">
          <span>${esc(clock(t.created_at))}</span>
          <span class="tag kind-${esc(t.kind || "asked")}">${esc(KIND_LABEL[t.kind] || "Asked")}</span>
          ${time !== null ? `<span>${icon("clock")} ${esc(seconds(time))}</span>` : ""}
          ${tools.map((name) => `<span class="tool">${esc(name)}</span>`).join("")}
        </span>
      </span>
    </button>`;
}

export function wireCards(root) {
  $$("[data-task]", root).forEach((el) =>
    el.addEventListener("click", () => openTask(el.dataset.task))
  );
}

/* -- the drawer -------------------------------------------------------------------- */

export function openTask(id) {
  openDrawer(
    () => {
      const t = findTask(id);
      if (!t) return null;
      const waiting = state.approvals.filter((a) => a.task_id === t.id);
      const time = took(t);
      const steps = t.steps || [];
      return {
        kicker: `${pill(t.status)} <span class="tag kind-${esc(t.kind || "asked")}">${esc(
          KIND_LABEL[t.kind] || "Asked"
        )}</span> <span class="muted">${esc(clock(t.created_at))}${
          time !== null ? ` · took ${esc(seconds(time))}` : ""
        }</span>`,
        title: t.request,
        body: `
          ${waiting
            .map(
              (a) => `<div class="approval-card" data-approval="${esc(a.id)}">
                <div class="why">${esc(a.reason)}</div>
                <div class="btn-row"><button class="btn ok" data-decide="allow">Allow</button>
                <button class="btn bad" data-decide="deny">Deny</button></div></div>`
            )
            .join("")}
          <section class="drawer-sec">
            <h3>${t.status === "failed" ? "What went wrong" : "Answer"}</h3>
            <div class="answer ${t.status === "failed" ? "bad" : ""}">${
              t.status === "failed"
                ? esc(t.error || "It failed.")
                : t.result
                ? markdown(t.result)
                : '<p class="muted">Still working on it…</p>'
            }</div>
          </section>
          <section class="drawer-sec">
            <h3>Steps <span class="muted">${steps.length || "none — answered directly"}</span></h3>
            <ol class="timeline">${steps
              .map(
                (s) => `<li class="${esc(s.status)}">
                  <span class="tl-dot"></span>
                  <span class="tl-body"><b>${esc(s.tool)}</b> ${esc(s.description)}
                  ${s.risk === "confirm" ? `<span class="tag">${icon("shield")} approval</span>` : ""}
                  ${s.error ? `<span class="tl-err">${esc(s.error)}</span>` : ""}</span>
                </li>`
              )
              .join("")}</ol>
          </section>
          <div class="drawer-actions">
            <button class="btn" data-again>${icon("redo")} Ask again</button>
            ${t.result ? `<button class="btn ghost" data-copy>${icon("copy")} Copy answer</button>` : ""}
          </div>`,
      };
    },
    (body) => {
      const t = findTask(id);
      $$("[data-decide]", body).forEach((button) =>
        button.addEventListener("click", async () => {
          const card = button.closest("[data-approval]");
          card.querySelectorAll("button").forEach((b) => (b.disabled = true));
          await act(
            Hud.postJSON(Hud.route("approval", { id: card.dataset.approval }), {
              decision: button.dataset.decide,
            })
          );
        })
      );
      const again = body.querySelector("[data-again]");
      if (again)
        again.addEventListener("click", async () => {
          const heard = await act(
            Hud.postJSON(Hud.route("command"), { text: t.request, submit: true }),
            "Asked again."
          );
          if (heard && heard.task_id) setTimeout(() => openTask(heard.task_id), 400);
        });
      const copy = body.querySelector("[data-copy]");
      if (copy)
        copy.addEventListener("click", () =>
          act(navigator.clipboard.writeText(t.result), "Copied.")
        );
    }
  );
}
