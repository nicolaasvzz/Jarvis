/*
 * The Terminal page: live shells on this PC, shared with Jarvis.
 *
 * The backend owns each shell; this page is a window onto one at a time. On
 * selecting a terminal it opens that terminal's own event stream, which starts
 * with a repaint of its screen and scrollback — so the screen is the same
 * whether the page was open all along or only just loaded — and then carries
 * new output as it happens. Keystrokes go back by POST, batched so a fast
 * typist (or a paste) is one request rather than dozens.
 *
 * Jarvis types into these same terminals, after you approve it, so the
 * approval cards are shown here too: you can watch what it wants to type and
 * allow it without leaving the page.
 */

import { Hud } from "../lib/hud.js";

const el = (id) => document.getElementById(id);
const FONT = 13; // the terminal's normal font size, in pixels

let terminals = [];
let approvals = [];
let selected = Hud.load("terminal") || null;
let term = null;
let fit = null;
let source = null;

/* -- boot ------------------------------------------------------------------ */

Hud.start(async () => {
  if (!window.Terminal || !window.FitAddon) {
    el("term-screen").innerHTML =
      '<div class="term-empty">The terminal library could not load — check the internet connection and reload.</div>';
    return;
  }
  setupTerminal();
  setupControls();

  const snapshot = await Hud.getJSON(Hud.route("snapshot"));
  terminals = snapshot.terminals || [];
  approvals = snapshot.approvals || [];
  renderApprovals();
  if (!terminals.length) {
    // A terminal page with nothing in it is a dead end; start with one.
    terminals = [await openTerminal("Main")];
  }
  select(terminals.some((t) => t.id === selected) ? selected : terminals[0].id);

  Hud.onEvent(onFrame);
  // "busy" vs "at its prompt" changes without an event; keep it current.
  setInterval(() => document.hidden || refreshList(), 4000);
});

/* -- the xterm instance ----------------------------------------------------- */

function setupTerminal() {
  term = new window.Terminal({
    cursorBlink: true,
    fontFamily: '"Cascadia Mono", Consolas, ui-monospace, monospace',
    fontSize: FONT,
    scrollback: 5000,
    allowProposedApi: false,
    theme: {
      background: "#02060a",
      foreground: "#d7f2f8",
      cursor: "#22d3ee",
      selectionBackground: "rgba(34, 211, 238, 0.3)",
    },
  });
  fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);

  // Keystrokes: queue them and send one POST at a time, in order.
  let pending = "";
  let sending = false;
  const flush = async () => {
    if (sending || !pending || !selected) return;
    sending = true;
    const data = pending;
    pending = "";
    try {
      await Hud.postJSON(Hud.route("terminalInput", { id: selected }), { data });
      // Enter: the command lands in "Typed here" once the shell has echoed it.
      if (data.includes("\r")) setTimeout(refreshList, 700);
    } catch (err) {
      Hud.toast(String(err.message || err), "bad");
    } finally {
      sending = false;
      if (pending) flush();
    }
  };
  term.onData((data) => {
    pending += data;
    flush();
  });

  window.addEventListener("resize", () => {
    const t = terminals.find((x) => x.id === selected);
    if (t) scaleTo(t.cols, t.rows);
  });
}

/* -- size --------------------------------------------------------------------

   A shell keeps the size it opened at: shrinking one would cut its lines.
   So instead of resizing the shell to the window, the font is scaled so the
   whole terminal fits the window. New terminals open at whatever fits here
   at the normal font size. */

function mount() {
  const screen = el("term-screen");
  if (!screen.querySelector(".xterm")) {
    screen.innerHTML = "";
    term.open(screen);
  }
}

function naturalSize() {
  mount();
  term.options.fontSize = FONT;
  const fits = fit.proposeDimensions() || {};
  return {
    cols: Math.max(40, Math.min(240, fits.cols || 120)),
    rows: Math.max(10, Math.min(80, fits.rows || 30)),
  };
}

function scaleTo(cols, rows) {
  mount();
  let size = FONT + 2;
  term.options.fontSize = size;
  for (;;) {
    const fits = fit.proposeDimensions();
    if (!fits || (fits.cols >= cols && fits.rows >= rows) || size <= 7) break;
    size -= 0.5;
    term.options.fontSize = size;
  }
  term.resize(cols, rows);
}

/* -- choosing a terminal ---------------------------------------------------- */

function select(id) {
  selected = id;
  Hud.save("terminal", id || "");
  if (source) source.close();
  source = null;
  renderList();
  renderHeader();

  if (!id) {
    el("term-screen").innerHTML =
      '<div class="term-empty">No terminal open — press “+ New terminal”.</div>';
    return;
  }
  const chosen = terminals.find((t) => t.id === id);
  if (chosen) scaleTo(chosen.cols, chosen.rows);
  term.reset();
  term.focus();

  // The stream's first message repaints the terminal's screen and scrollback.
  const query = new URLSearchParams({ token: Hud.token });
  source = new EventSource(`${Hud.endpoint("terminalStream", { id })}?${query}`);
  source.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    if (id !== selected) return;
    if (message.type === "replay") {
      scaleTo(message.terminal.cols, message.terminal.rows);
      term.reset();
      term.write(message.data);
      update(message.terminal);
    } else if (message.type === "output") {
      term.write(message.data);
    } else if (message.type === "exit") {
      term.write("\r\n\x1b[2m[the shell has exited — close this terminal or open a new one]\x1b[0m\r\n");
    } else if (message.type === "closed") {
      source.close();
    }
  };
  // EventSource reconnects by itself; the replay on reconnect redraws the
  // screen, so nothing is lost if the connection drops for a moment.
}

async function openTerminal(title = "") {
  return Hud.postJSON(Hud.route("terminals"), { title, ...naturalSize() });
}

/* -- live events ------------------------------------------------------------ */

function onFrame(frame) {
  if (frame.type.startsWith("approval.")) refreshApprovals();
  if (!frame.type.startsWith("terminal.")) return;

  const data = frame.data || {};
  if (frame.type === "terminal.opened" && data.terminal) {
    update(data.terminal);
    if (data.terminal.opened_by === "jarvis") {
      Hud.toast(frame.message, "ok");
      flash(data.terminal.id);
    }
  } else if (frame.type === "terminal.exited" && data.terminal) {
    update(data.terminal);
  } else if (frame.type === "terminal.closed") {
    terminals = terminals.filter((t) => t.id !== data.terminal_id);
    if (selected === data.terminal_id) select(terminals.length ? terminals[0].id : null);
    else renderList();
  } else if (frame.type === "terminal.input") {
    flash(data.terminal_id);
  } else if (frame.type === "terminal.failed" && data.terminal) {
    update(data.terminal);
    flash(data.terminal.id);
    if (data.terminal.id !== selected) Hud.toast(frame.message, "bad");
  } else if (data.terminal) {
    update(data.terminal); // finished, updated
  }
  // The log of what was typed lives on the server; re-read it.
  refreshList();
}

async function refreshList() {
  try {
    const fresh = await Hud.getJSON(Hud.route("terminals"));
    terminals = fresh;
    renderList();
    renderHeader();
  } catch (_) {
    /* the next event will try again */
  }
}

function update(terminal) {
  const at = terminals.findIndex((t) => t.id === terminal.id);
  if (at >= 0) terminals[at] = terminal;
  else terminals.push(terminal);
  renderList();
  renderHeader();
}

function flash(id) {
  const item = document.querySelector(`[data-term="${CSS.escape(id || "")}"]`);
  if (!item) return;
  item.classList.remove("flash");
  void item.offsetWidth; // restart the animation
  item.classList.add("flash");
}

/* -- rendering ---------------------------------------------------------------- */

function dotFor(t) {
  if (t.status !== "running") return "down";
  return t.at_prompt ? "live" : "busy";
}

function renderList() {
  const host = el("term-list");
  const open = terminals.length;
  el("term-count").textContent = open ? `${open}` : "";
  el("term-total").textContent = open ? `${open} terminal${open === 1 ? "" : "s"}` : "";
  if (!open) {
    host.innerHTML = '<div class="empty">No terminals open.</div>';
    return;
  }
  host.innerHTML = terminals
    .map(
      (t) => `
      <button class="term-item ${t.id === selected ? "active" : ""}" data-term="${Hud.escape(t.id)}">
        <div class="top">
          <span class="dot ${dotFor(t)}"></span>
          <span class="name">${Hud.escape(t.title)}</span>
          <span class="who ${t.opened_by === "jarvis" ? "jarvis" : ""}">${Hud.escape(t.id)} · ${
            who(t.opened_by)
          }</span>
        </div>
        <div class="sub ${t.last_result && !t.last_result.ok ? "bad" : ""}">${Hud.escape(
          t.last_result && !t.last_result.ok
            ? `✕ ${t.last_result.command} failed`
            : t.purpose || t.state || ""
        )}</div>
      </button>`
    )
    .join("");
  host.querySelectorAll("[data-term]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.getAttribute("data-term");
      if (id !== selected) select(id);
      else term.focus();
    });
  });
}

/** "jarvis" → "Jarvis", "startup" → "Startup", anyone else → "You". */
function who(by) {
  return { jarvis: "Jarvis", startup: "Startup" }[by] || "You";
}

function renderHeader() {
  const t = terminals.find((x) => x.id === selected);
  el("term-name").textContent = t ? t.title : "No terminal";
  el("term-id").textContent = t ? `${t.id} · ${t.state}` : "";
  el("term-purpose").textContent = t && t.purpose ? t.purpose : "";
  el("term-dot").className = `dot ${t ? dotFor(t) : ""}`;
  el("term-close").hidden = !t;
  el("term-trust-wrap").hidden = !t;
  el("term-trust").checked = Boolean(t && t.trusted);

  // Explain is always there; after a failed command it turns red and says so.
  const explain = el("term-explain");
  const failed = Boolean(t && t.last_result && !t.last_result.ok);
  explain.hidden = !t;
  explain.classList.toggle("failed", failed);
  explain.textContent = failed ? "Explain the error" : "Explain";

  const log = el("term-log");
  const lines = t ? (t.log || []).slice().reverse() : [];
  log.innerHTML = lines.length
    ? lines
        .map(
          (line) => `
        <div class="term-line ${line.by === "jarvis" ? "jarvis" : ""}">
          <span class="by">${who(line.by)}</span>
          <span class="what">${Hud.escape(line.text)}</span>
        </div>`
        )
        .join("")
    : '<div class="empty">Nothing yet.</div>';
}

/* -- approvals ----------------------------------------------------------------- */

async function refreshApprovals() {
  try {
    approvals = await Hud.getJSON(Hud.route("approvals"));
    renderApprovals();
  } catch (_) {
    /* ignore */
  }
}

function renderApprovals() {
  const panel = el("approvals-panel");
  const host = el("approvals");
  if (!approvals.length) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "";
  el("approval-count").textContent = `${approvals.length}`;
  host.innerHTML = approvals
    .map(
      (a) => `
      <div class="approval" data-approval="${Hud.escape(a.id)}">
        <div class="tool">${Hud.escape(a.tool)}</div>
        <div class="why">${Hud.escape(a.reason)}</div>
        <div class="btn-row">
          <button class="allow" data-decide="allow">Allow</button>
          <button class="deny" data-decide="deny">Deny</button>
        </div>
      </div>`
    )
    .join("");
  host.querySelectorAll("[data-decide]").forEach((button) => {
    button.addEventListener("click", async () => {
      const card = button.closest("[data-approval]");
      card.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await Hud.postJSON(Hud.route("approval", { id: card.getAttribute("data-approval") }), {
          decision: button.getAttribute("data-decide"),
        });
        refreshApprovals();
      } catch (err) {
        Hud.toast(String(err.message || err), "bad");
        card.querySelectorAll("button").forEach((b) => (b.disabled = false));
      }
    });
  });
}

/* -- buttons and the ask box ----------------------------------------------------- */

function setupControls() {
  el("term-new").addEventListener("click", async () => {
    try {
      const made = await openTerminal("");
      update(made);
      select(made.id);
    } catch (err) {
      Hud.toast(String(err.message || err), "bad");
    }
  });

  el("term-close").addEventListener("click", async () => {
    const t = terminals.find((x) => x.id === selected);
    if (!t) return;
    if (t.status === "running" && !window.confirm(`Close “${t.title}”? Anything running in it stops.`)) {
      return;
    }
    try {
      await Hud.postJSON(Hud.route("terminalClose", { id: t.id }), {});
    } catch (err) {
      Hud.toast(String(err.message || err), "bad");
    }
  });

  const ask = el("term-ask");
  ask.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" || !ask.value.trim()) return;
    const t = terminals.find((x) => x.id === selected);
    const text = ask.value.trim();
    ask.value = "";
    // Name the terminal, so "what went wrong?" means this one.
    const about = t ? `About terminal ${t.id} ("${t.title}"): ` : "";
    showReply(text, "Thinking…", "thinking");
    try {
      const heard = await Hud.postJSON(Hud.route("command"), { text: about + text, submit: true });
      waitFor(heard.task_id);
    } catch (err) {
      showReply(text, String(err.message || err), "bad");
    }
  });
  el("term-reply-close").addEventListener("click", () => (el("term-reply").hidden = true));

  el("term-explain").addEventListener("click", async () => {
    if (!selected) return;
    try {
      const task = await Hud.postJSON(Hud.route("terminalExplain", { id: selected }), {});
      showReply(task.request, "Thinking…", "thinking");
      waitFor(task.id);
    } catch (err) {
      Hud.toast(String(err.message || err), "bad");
    }
  });

  const trust = el("term-trust");
  trust.addEventListener("change", async () => {
    if (!selected) return;
    try {
      update(await Hud.postJSON(Hud.route("terminalTrust", { id: selected }), {
        trusted: trust.checked,
      }));
    } catch (err) {
      trust.checked = !trust.checked;
      Hud.toast(String(err.message || err), "bad");
    }
  });
}

/* The answer to a question asked here is shown here, under the terminal.
   A quick answer can arrive on the stream before the request that asked for
   it returns, so recent answers are kept until someone waits for them. */
let awaiting = null;
const answers = new Map();

Hud.onEvent((frame) => {
  if (frame.type !== "task.completed" && frame.type !== "task.failed") return;
  answers.set(frame.task_id, frame);
  if (answers.size > 20) answers.delete(answers.keys().next().value);
  if (frame.task_id === awaiting) answer();
});

function waitFor(taskId) {
  awaiting = taskId || null;
  if (answers.has(awaiting)) answer();
}

function answer() {
  const frame = answers.get(awaiting);
  awaiting = null;
  showReply(el("term-reply-q").textContent, frame.message,
    frame.type === "task.failed" ? "bad" : "");
}

function showReply(question, answer, kind) {
  el("term-reply").hidden = false;
  el("term-reply-q").textContent = question;
  const body = el("term-reply-a");
  body.className = `term-reply-a ${kind}`;
  body.textContent = answer;
}
