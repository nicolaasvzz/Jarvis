/*
 * The Approvals page: Allow or Deny what Jarvis wants to run, from a phone.
 *
 * Nothing else on it — big buttons, the exact command, and a short list of
 * what happened recently — so it works one-handed on a small screen. It
 * listens to the same event stream as the other pages and re-reads the
 * pending list whenever an approval appears or is settled, so it is always
 * current without refreshing. The tab title carries the count, and the
 * phone buzzes (where browsers allow it) when something new arrives.
 */

import { Hud } from "../lib/hud.js";

const el = (id) => document.getElementById(id);
const RECENT = new Set([
  "approval.required", "approval.resolved", "approval.auto", "task.completed",
  "task.failed", "terminal.failed", "terminal.finished", "terminal.opened",
]);

let pending = [];
let recent = [];
let seen = new Set();

Hud.start(async (snapshot) => {
  recent = (snapshot.events || []).filter((f) => RECENT.has(f.type)).slice(-8);
  setPending(snapshot.approvals || [], false);
  renderRecent();

  Hud.onEvent((frame) => {
    if (frame.type.startsWith("approval.")) refresh();
    if (RECENT.has(frame.type)) {
      recent.push(frame);
      recent = recent.slice(-8);
      renderRecent();
    }
  });
  // Phones drop connections when the screen sleeps; catch up on waking.
  document.addEventListener("visibilitychange", () => document.hidden || refresh());
  Hud.onResync(refresh);
});

async function refresh() {
  try {
    setPending(await Hud.getJSON(Hud.route("approvals")), true);
  } catch (_) {
    /* the next event retries */
  }
}

function setPending(list, alert) {
  const fresh = list.filter((a) => !seen.has(a.id));
  if (alert && fresh.length && navigator.vibrate) {
    try {
      navigator.vibrate([120, 60, 120]);
    } catch (_) {
      /* not allowed before a tap; that's fine */
    }
  }
  pending = list;
  seen = new Set(list.map((a) => a.id));
  render();
}

function render() {
  const appName = window.HudConnection.settings.appName;
  document.title = pending.length
    ? `(${pending.length}) ${appName} — Approvals`
    : `${appName} — Approvals`;
  el("pending-count").textContent = pending.length ? `(${pending.length})` : "";

  const host = el("pending");
  if (!pending.length) {
    host.innerHTML =
      '<div class="approve-empty">Nothing waiting. This page updates by itself.</div>';
    return;
  }
  host.innerHTML = pending
    .map((a) => {
      const args = a.arguments || {};
      const command = args.command || args.text || "";
      const where = args.title ? `in “${args.title}”` : "";
      return `
      <article class="ask" data-approval="${Hud.escape(a.id)}">
        <div class="ask-tool">${Hud.escape(a.tool)} ${Hud.escape(where)}</div>
        ${command ? `<pre class="ask-command">${Hud.escape(command)}</pre>` : ""}
        <div class="ask-why">${Hud.escape(a.reason)}</div>
        <div class="ask-meta">${Hud.ago(a.created_at)}</div>
        <div class="ask-buttons">
          <button class="deny" data-decide="deny">Deny</button>
          <button class="allow" data-decide="allow">Allow</button>
        </div>
      </article>`;
    })
    .join("");

  host.querySelectorAll("[data-decide]").forEach((button) => {
    button.addEventListener("click", async () => {
      const card = button.closest("[data-approval]");
      card.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await Hud.postJSON(Hud.route("approval", { id: card.getAttribute("data-approval") }), {
          decision: button.getAttribute("data-decide"),
        });
        card.classList.add("done");
        refresh();
      } catch (err) {
        Hud.toast(String(err.message || err), "bad");
        card.querySelectorAll("button").forEach((b) => (b.disabled = false));
        refresh();
      }
    });
  });
}

function renderRecent() {
  const host = el("recent");
  if (!recent.length) {
    host.innerHTML = '<div class="empty">Nothing yet.</div>';
    return;
  }
  host.innerHTML = recent
    .slice()
    .reverse()
    .map(
      (frame) => `
      <div class="feed-item">
        <span class="pip ${Hud.tone(frame.type)}"></span>
        <span class="txt">
          <span class="msg">${Hud.escape(frame.message)}</span>
          <span class="meta">${Hud.escape(Hud.ago(frame.created_at))}</span>
        </span>
      </div>`
    )
    .join("");
}
