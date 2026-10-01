/*
 * The Core page: particle sphere, machine vitals, live activity, and voice.
 *
 * Everything reactive here is driven by one event stream. The page keeps only
 * the small amount of state it needs to render — tasks, approvals, the last
 * few events — and rebuilds those panels when they change, rather than trying
 * to patch the DOM in place. At this scale that is both simpler and faster.
 *
 * It is one tab of several (js/lib/tabs.js): mounted once, then shown and
 * hidden. While hidden the sphere and the stats polling stop and the panels
 * aren't redrawn — they catch up in show() — but the voice carries on, so
 * Jarvis still answers out loud while you watch another tab.
 */

import { Hud } from "../lib/hud.js";
import { Core } from "../components/core-visual.js";
import { Voice } from "../components/voice.js";

const el = (id) => document.getElementById(id);

let core = null;
let voice = null;
let muted = Hud.load("muted") === "1";
let statsTimer = null;
let statsEvery = 2000;
let visible = false;

const state = {
  tasks: [],
  approvals: [],
  events: [],
  terminals: [],
  history: { cpu: [], memory: [] },
};

/* -- the tab's life ---------------------------------------------------------- */

export function mount(snapshot) {
  core = new Core(el("core"), {
    accent: snapshot.settings.accent,
    particles: snapshot.settings.particles,
    root: document.querySelector('[data-screen="core"]'),
  });

  document.querySelector("[data-workspace]").textContent = shortenPath(
    snapshot.workspace
  );

  state.tasks = snapshot.tasks || [];
  state.approvals = snapshot.approvals || [];
  state.terminals = snapshot.terminals || [];
  state.events = (snapshot.events || []).slice(-40);
  paint("tasks", "approvals", "feed", "terminals");

  setupVoice(snapshot.voice);
  setupConsole();
  setupMute();

  showBrain();
  statsEvery = Math.max(1000, (snapshot.settings.stats_interval || 2) * 1000);
  Hud.onEvent(onFrame);
  el("reply-close").addEventListener("click", () => (el("reply").hidden = true));
}

export function show() {
  visible = true;
  core.start();
  pollStats();
  flush();
}

export function hide() {
  visible = false;
  core.stop();
  clearInterval(statsTimer);
}

/** Back from a dropped stream: what was missed is in the routes, not the feed. */
export function resync() {
  refresh("tasks", "approvals", "terminals");
}

/* -- drawing, batched: one redraw per frame, none while hidden ------------ */

const dirty = new Set();
let painting = 0;

function paint(...parts) {
  parts.forEach((part) => dirty.add(part));
  if (visible && !painting) painting = requestAnimationFrame(flush);
}

function flush() {
  cancelAnimationFrame(painting);
  painting = 0;
  if (dirty.has("tasks")) renderTasks();
  if (dirty.has("approvals")) renderApprovals();
  if (dirty.has("feed")) renderFeed();
  if (dirty.has("terminals")) renderTerminalSummary();
  dirty.clear();
  refreshState();
}

/* -- live events ----------------------------------------------------------- */

function onFrame(frame) {
  state.events.push(frame);
  if (state.events.length > 60) state.events.shift();
  paint("feed");

  if (core) core.pulse(frame.type.startsWith("task.") ? 0.9 : 0.32);

  // Re-read whatever the event changed — just that, a burst of events
  // becoming one request each.
  const type = frame.type;
  if (type === "task.created" && frame.task_id) {
    asks.set(frame.task_id, frame.message);
    if (asks.size > 20) asks.delete(asks.keys().next().value);
  }
  if (type.startsWith("task.") || type.startsWith("step.")) refresh("tasks");
  if (type.startsWith("approval.")) refresh("approvals", "tasks");
  if (type.startsWith("terminal.")) refresh("terminals");

  // The other tabs say these their own way; here they'd only repeat it.
  if (visible && (type === "task.failed" || type === "error")) {
    Hud.toast(frame.message, "bad");
  }
  if (visible && type === "approval.required") {
    Hud.toast(frame.message, "bad");
  }

  if (type === "task.completed" || type === "task.failed") {
    showReply(frame);
  }

  if (frame.speak && !muted && voice) voice.say(frame.message);
}

/* -- the reply panel: Jarvis's latest answer, readable in full ------------- */

let awaiting = null; // the task id of the request just typed, if any
// What each recent task asked, from its task.created event: a quick answer
// can arrive before the task list has been re-read.
const asks = new Map();

function showReply(frame) {
  const task = state.tasks.find((t) => t.id === frame.task_id);
  const asked =
    (frame.data && frame.data.request) || (task && task.request) || asks.get(frame.task_id) || "";
  // Only a request's own answer replaces the panel while another is being
  // waited on.
  if (awaiting && frame.task_id !== awaiting && !asked) return;
  if (frame.task_id === awaiting) awaiting = null;
  paintReply(asked, frame.message, frame.type === "task.failed");
}

function paintReply(question, answer, failed = false, thinking = false) {
  el("reply").hidden = false;
  el("reply-q").textContent = question;
  const body = el("reply-a");
  body.classList.toggle("bad", failed);
  body.classList.toggle("thinking", thinking);
  body.innerHTML = thinking ? Hud.escape(answer) : markdown(answer);
  body.scrollTop = 0;
}

/** Just enough Markdown for chat replies: paragraphs, lists, bold, code, links. */
function markdown(text) {
  const inline = (line) =>
    Hud.escape(line)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  const html = [];
  let list = null;
  for (const raw of String(text || "").split(/\n/)) {
    const line = raw.trimEnd();
    const item = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (item) {
      if (!list) html.push((list = "<ul>"));
      html.push(`<li>${inline(item[1])}</li>`);
      continue;
    }
    if (list) {
      html.push("</ul>");
      list = null;
    }
    if (line.trim()) html.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
  }
  if (list) html.push("</ul>");
  return html.join("");
}

const loaders = {
  // The same 20 the snapshot carries, without its 300 events.
  tasks: async () => (state.tasks = await Hud.getJSON(`${Hud.route("tasks")}?limit=20`)),
  approvals: async () => (state.approvals = await Hud.getJSON(Hud.route("approvals"))),
  terminals: async () => (state.terminals = await Hud.getJSON(Hud.route("terminals"))),
};
const wanted = new Set();
let refreshing = false;
let viaSnapshot = false; // a backend without those routes: the snapshot has them all

/** Re-read parts of the state: one request per part per burst, in order. */
async function refresh(...parts) {
  parts.forEach((part) => wanted.add(part));
  if (refreshing) return;
  refreshing = true;
  try {
    while (wanted.size) {
      await new Promise((resolve) => setTimeout(resolve, 60)); // let a burst gather
      const now = [...wanted];
      wanted.clear();
      await reread(now);
      paint(...now);
    }
  } finally {
    refreshing = false;
  }
}

async function reread(parts) {
  if (!viaSnapshot) {
    const results = await Promise.allSettled(parts.map((part) => loaders[part]()));
    const missing = results.some((r) => r.status === "rejected" && r.reason.status === 404);
    // Anything else dropped is harmless; the next event will try again.
    if (!missing) return;
    viaSnapshot = true;
  }
  try {
    const snapshot = await Hud.getJSON(Hud.route("snapshot"));
    state.tasks = snapshot.tasks || [];
    state.approvals = snapshot.approvals || [];
    state.terminals = snapshot.terminals || [];
    paint("tasks", "approvals", "terminals");
  } catch (_) {
    /* as above */
  }
}

/* -- the sphere's mood ------------------------------------------------------ */

function refreshState() {
  if (!core) return;
  const running = state.tasks.filter((t) =>
    ["running", "planning", "pending"].includes(t.status)
  );
  const failed = state.tasks.some((t) => t.status === "failed");

  let mood = "idle";
  let line = "Standing by";
  let detail = "";

  if (state.approvals.length) {
    mood = "error";
    line = "Awaiting your approval";
    detail = state.approvals[0].reason || "";
  } else if (running.length) {
    mood = "thinking";
    line = running[0].status === "planning" ? "Working out a plan" : "Working";
    detail = running[0].request || "";
  } else if (failed && state.tasks[0] && state.tasks[0].status === "failed") {
    mood = "error";
    line = "Last task failed";
    detail = state.tasks[0].error || "";
  } else if (state.tasks.length) {
    line = "Standing by";
    detail = plain(state.tasks[0].result);
  }

  // Speaking overrides the mood so the pulse follows the voice.
  if (voice && voice.speaking) mood = "speaking";

  core.setState(mood);
  el("state-line").textContent = line;
  el("state-detail").textContent = truncate(detail, 130);
}

/* -- panels ---------------------------------------------------------------- */

function renderTasks() {
  const host = el("tasks");
  const live = state.tasks.slice(0, 8);
  el("task-count").textContent = state.tasks.length
    ? `${state.tasks.length}`
    : "";
  if (!live.length) {
    host.innerHTML = '<div class="empty">Nothing running.</div>';
    return;
  }
  host.innerHTML = live
    .map((task) => {
      const steps = (task.steps || [])
        .map((s) => `<i class="${Hud.escape(s.status)}"></i>`)
        .join("");
      const done = (task.steps || []).filter((s) => s.status === "completed").length;
      const total = (task.steps || []).length;
      return `
        <div class="task ${Hud.escape(task.status)}" data-task="${Hud.escape(task.id)}"
             title="Show the answer">
          <div class="req">${Hud.escape(task.request)}</div>
          <div class="sub">${Hud.escape(task.status)}${
            total ? ` · ${done}/${total} steps` : ""
          } · ${Hud.ago(task.created_at)}</div>
          ${steps ? `<div class="steps">${steps}</div>` : ""}
        </div>`;
    })
    .join("");
  // Click a request to read its answer again; the Mothership has the rest.
  host.querySelectorAll("[data-task]").forEach((node) =>
    node.addEventListener("click", () => {
      const task = state.tasks.find((t) => t.id === node.dataset.task);
      if (!task) return;
      const answer = task.status === "failed" ? task.error : task.result;
      paintReply(task.request, answer || "Still working on it…", task.status === "failed");
      el("reply-a").insertAdjacentHTML("beforeend",
        '<p><a href="mothership.html#/requests">Every step of it, in the Mothership →</a></p>');
    })
  );
}

function renderApprovals() {
  const panel = el("approvals-panel");
  const host = el("approvals");
  if (!state.approvals.length) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "";
  el("approval-count").textContent = `${state.approvals.length}`;
  host.innerHTML = state.approvals
    .map(
      (a) => `
      <div class="approval" data-approval="${Hud.escape(a.id)}">
        <div class="tool">${Hud.escape(a.tool)}</div>
        <div class="why">${Hud.escape(a.reason)}</div>
        <div class="args">${Hud.escape(JSON.stringify(a.arguments))}</div>
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
      const id = card.getAttribute("data-approval");
      card.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await Hud.postJSON(Hud.route("approval", { id }), {
          decision: button.getAttribute("data-decide"),
        });
        refresh("approvals", "tasks");
      } catch (err) {
        Hud.toast(String(err.message || err), "bad");
        card.querySelectorAll("button").forEach((b) => (b.disabled = false));
      }
    });
  });
}

function renderFeed() {
  const host = el("feed");
  const items = state.events.slice(-26).reverse();
  if (!items.length) {
    host.innerHTML = '<div class="empty">Waiting for Jarvis to do something.</div>';
    return;
  }
  host.innerHTML = items
    .map((frame) => {
      const bits = [frame.type, Hud.ago(frame.created_at)];
      return `
        <div class="feed-item">
          <span class="pip ${Hud.tone(frame.type)}"></span>
          <span class="txt">
            <span class="msg">${Hud.escape(plain(frame.message))}</span>
            <span class="meta">${Hud.escape(bits.join(" · "))}</span>
          </span>
        </div>`;
    })
    .join("");
}

function renderTerminalSummary() {
  const open = state.terminals.length;
  document.querySelector("[data-terminals-summary]").textContent = open
    ? `${open} terminal${open === 1 ? "" : "s"}`
    : "";
}

/* -- machine vitals --------------------------------------------------------- */

/** Which model is thinking, and with how many tools — from the `system` route. */
async function showBrain() {
  try {
    const system = await Hud.getJSON(Hud.route("system"));
    const brain = system.brain || {};
    const host = el("brain");
    host.innerHTML = `
      <span class="dot ${brain.connected ? "live" : "down"}"></span>
      <span class="who">${Hud.escape(brain.model || brain.provider || "unknown model")}</span>
      <span class="meta">${(system.tools || []).length} tools</span>`;
    host.title = (system.tools || []).join(", ");
    host.hidden = false;
  } catch (_) {
    /* an optional route; the panel works without it */
  }
}

/** Sample the machine now and every few seconds — while this tab is shown. */
function pollStats() {
  const run = async () => {
    // A tab in the background has no one to show the numbers to.
    if (document.hidden) return;
    try {
      const stats = await Hud.getJSON(Hud.route("stats"));
      if (visible) renderStats(stats);
    } catch (_) {
      /* a missed sample is not worth reporting */
    }
  };
  run();
  clearInterval(statsTimer);
  statsTimer = setInterval(run, statsEvery);
}

function renderStats(stats) {
  const host = el("stats");
  if (!stats.available) {
    host.innerHTML =
      '<div class="empty">Install psutil for live system stats:<br>' +
      '<code>pip install psutil</code></div>';
    return;
  }

  push(state.history.cpu, stats.cpu ?? 0);
  push(state.history.memory, stats.memory ? stats.memory.percent : 0);

  const parts = [
    meter("CPU", stats.cpu, `${Math.round(stats.cpu ?? 0)}%`, state.history.cpu),
    stats.memory
      ? meter(
          "Memory",
          stats.memory.percent,
          `${Math.round(stats.memory.percent)}%`,
          state.history.memory,
          `${Hud.bytes(stats.memory.used)} / ${Hud.bytes(stats.memory.total)}`
        )
      : "",
    stats.disk
      ? meter("Disk", stats.disk.percent, `${Math.round(stats.disk.percent)}%`, null,
          `${Hud.bytes(stats.disk.used)} / ${Hud.bytes(stats.disk.total)}`)
      : "",
    stats.battery
      ? meter(
          stats.battery.plugged ? "Battery (charging)" : "Battery",
          stats.battery.percent,
          `${stats.battery.percent}%`
        )
      : "",
  ];
  host.innerHTML = parts.filter(Boolean).join("");
}

function meter(label, percent, value, history, note) {
  const level = percent >= 90 ? "hot" : percent >= 70 ? "warn" : "";
  return `
    <div class="metric">
      <div class="metric-head">
        <span class="metric-label">${Hud.escape(label)}</span>
        <span class="metric-value ${note ? "sm" : ""}">${Hud.escape(value)}</span>
      </div>
      <div class="bar ${level}"><i style="width:${Math.max(0, Math.min(100, percent || 0))}%"></i></div>
      ${history ? sparkline(history) : ""}
      ${note ? `<div class="meta metric-label" style="margin-top:4px;font-size:10px">${Hud.escape(note)}</div>` : ""}
    </div>`;
}

function push(series, value) {
  series.push(Number(value) || 0);
  if (series.length > 48) series.shift();
}

function sparkline(series) {
  if (series.length < 2) return "";
  const width = 100;
  const height = 22;
  const step = width / (series.length - 1);
  const points = series
    .map((v, i) => `${(i * step).toFixed(1)},${(height - (v / 100) * height).toFixed(1)}`)
    .join(" ");
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <polyline points="${points}" fill="none" stroke="var(--accent)"
                stroke-width="1.2" vector-effect="non-scaling-stroke" opacity="0.8"/>
    </svg>`;
}

/* -- voice and console ------------------------------------------------------ */

function setupVoice(capability) {
  const mic = el("mic");
  const transcript = el("transcript");

  if (!capability || !capability.enabled) {
    mic.style.display = "none";
    return;
  }

  voice = new Voice({
    wav: capability.listen_provider === "wispr",
    browser: capability.listen_provider === "browser",
    language: capability.listen_language,
    onTranscript: (heard) => {
      if (!heard.text) return;
      if (!heard.addressed) {
        // Heard, but not for us. Show it faintly so the wake word is
        // visibly working, without acting on it.
        transcript.classList.remove("hot");
        transcript.textContent = `“${heard.text}” — not addressed to Jarvis`;
        return;
      }
      transcript.classList.add("hot");
      transcript.textContent = `“${heard.command || heard.text}”`;
      if (heard.submitted) Hud.toast(`Running: ${heard.command}`, "ok");
    },
    onStateChange: (mode) => {
      mic.classList.toggle("armed", mode === "armed" || mode === "speaking");
      mic.classList.toggle("hearing", mode === "hearing");
      if (mode === "hearing") transcript.textContent = "Listening…";
      if (mode === "thinking") transcript.textContent = "Transcribing…";
      refreshState();
    },
    onAmplitude: (value) => core && core.setAmplitude(value),
    onError: (message) => Hud.toast(message, "bad"),
    onNotice: (message) => Hud.toast(message, "ok"),
    onInterim: (text) => {
      transcript.classList.remove("hot");
      transcript.textContent = `“${text}…”`;
    },
  });

  if (!capability.can_listen) {
    mic.title = "Listening is not installed — text still works";
    mic.addEventListener("click", () =>
      Hud.toast(
        "Listening isn't built in — type to Jarvis instead.",
        "bad"
      )
    );
    return;
  }

  const wake = capability.wake_word_required
    ? `Say “${capability.wake_word}” then your request`
    : "Just speak — no wake word needed";
  mic.title = wake;

  mic.addEventListener("click", async () => {
    if (voice.listening) {
      voice.stopListening();
      transcript.textContent = "";
      Hud.toast("Microphone off", "");
    } else {
      await voice.startListening();
      transcript.textContent = wake;
    }
  });
}

function setupConsole() {
  const input = el("prompt");
  input.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" || !input.value.trim()) return;
    const text = input.value.trim();
    input.value = "";
    paintReply(text, "Thinking…", false, true);
    try {
      const heard = await Hud.postJSON(Hud.route("command"), {
        text,
        submit: true,
      });
      if (heard.task_id) awaiting = heard.task_id;
      if (!heard.submitted && !heard.addressed) {
        Hud.toast(
          `Start with “${(heard.details && heard.details.wake_word) || "Jarvis"}” or turn off wake_word_required.`,
          "bad"
        );
      }
    } catch (err) {
      Hud.toast(String(err.message || err), "bad");
    }
  });
}

function setupMute() {
  const label = el("mute-label");
  const paint = () => (label.textContent = muted ? "Voice: muted" : "Voice: on");
  paint();
  el("mute-toggle").addEventListener("click", (event) => {
    event.preventDefault();
    muted = !muted;
    Hud.save("muted", muted ? "1" : "0");
    paint();
  });
}

/* -- helpers ---------------------------------------------------------------- */

/** One-line form of a reply: Markdown marks removed, lines joined. */
function plain(text) {
  return String(text || "")
    .replace(/\*\*|__|`/g, "")
    .replace(/^\s*(?:#+|[-*•])\s+/gm, "")
    .replace(/\s*\n+\s*/g, " — ")
    .trim();
}

function truncate(text, max) {
  const value = String(text || "");
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function shortenPath(path) {
  const parts = String(path || "").replace(/\\/g, "/").split("/");
  return parts.length > 3 ? `…/${parts.slice(-2).join("/")}` : path;
}
