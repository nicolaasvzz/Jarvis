/*
 * The Core page: particle sphere, machine vitals, live activity, and voice.
 *
 * Everything reactive here is driven by one event stream. The page keeps only
 * the small amount of state it needs to render — tasks, approvals, the last
 * few events — and rebuilds those panels when they change, rather than trying
 * to patch the DOM in place. At this scale that is both simpler and faster.
 */

import { Hud } from "./hud.js";
import { Core } from "./core-visual.js";
import { Voice } from "./voice.js";

const el = (id) => document.getElementById(id);

let core = null;
let voice = null;
let muted = localStorage.getItem("jarvis.muted") === "1";
let statsTimer = null;

const state = {
  tasks: [],
  approvals: [],
  events: [],
  agents: [],
  history: { cpu: [], memory: [] },
};

/* -- boot ------------------------------------------------------------------ */

Hud.start(async () => {

  const snapshot = await Hud.getJSON("/dash/api/snapshot");
  core = new Core(el("core"), {
    accent: snapshot.settings.accent,
    particles: snapshot.settings.particles,
  });
  core.start();

  document.querySelector("[data-workspace]").textContent = shortenPath(
    snapshot.workspace
  );

  state.tasks = snapshot.tasks || [];
  state.approvals = snapshot.approvals || [];
  state.agents = snapshot.agents || [];
  state.events = (snapshot.events || []).slice(-40);

  renderTasks();
  renderApprovals();
  renderFeed();
  renderAgentSummary();
  refreshState();

  setupVoice(snapshot.voice);
  setupConsole();
  setupMute();

  pollStats(snapshot.settings.stats_interval || 2);
  Hud.onEvent(onFrame);
});

/* -- live events ----------------------------------------------------------- */

function onFrame(frame) {
  state.events.push(frame);
  if (state.events.length > 60) state.events.shift();
  renderFeed();

  if (core) core.pulse(frame.type.startsWith("task.") ? 0.9 : 0.32);

  // Anything that changes task or approval shape needs a re-read; the
  // snapshot is small and this keeps one source of truth.
  if (
    frame.type.startsWith("task.") ||
    frame.type.startsWith("step.") ||
    frame.type.startsWith("approval.")
  ) {
    refreshCollections();
  }
  if (frame.type.startsWith("agent.")) refreshAgents();

  if (frame.type === "task.failed" || frame.type === "error") {
    Hud.toast(frame.message, "bad");
  }
  if (frame.type === "approval.required") {
    Hud.toast(frame.message, "bad");
  }

  if (frame.speak && !muted && voice) voice.say(frame.message);

  refreshState();
}

async function refreshCollections() {
  try {
    const snapshot = await Hud.getJSON("/dash/api/snapshot");
    state.tasks = snapshot.tasks || [];
    state.approvals = snapshot.approvals || [];
    state.agents = snapshot.agents || [];
    renderTasks();
    renderApprovals();
    renderAgentSummary();
    refreshState();
  } catch (_) {
    /* a dropped refresh is harmless; the next event will try again */
  }
}

async function refreshAgents() {
  try {
    const snapshot = await Hud.getJSON("/dash/api/snapshot");
    state.agents = snapshot.agents || [];
    renderAgentSummary();
  } catch (_) {
    /* ignore */
  }
}

/* -- the sphere's mood ------------------------------------------------------ */

function refreshState() {
  if (!core) return;
  const running = state.tasks.filter((t) =>
    ["running", "planning", "pending"].includes(t.status)
  );
  const failed = state.tasks.some((t) => t.status === "failed");
  const busy = state.agents.filter((a) => a.status === "working");

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
    if (busy.length) {
      const names = busy.map((a) => a.name).join(", ");
      detail = `${names} — ${detail}`;
    }
  } else if (failed && state.tasks[0] && state.tasks[0].status === "failed") {
    mood = "error";
    line = "Last task failed";
    detail = state.tasks[0].error || "";
  } else if (state.tasks.length) {
    line = "Standing by";
    detail = state.tasks[0].result || "";
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
        <div class="task ${Hud.escape(task.status)}">
          <div class="req">${Hud.escape(task.request)}</div>
          <div class="sub">${Hud.escape(task.status)}${
            total ? ` · ${done}/${total} steps` : ""
          } · ${Hud.ago(task.created_at)}</div>
          ${steps ? `<div class="steps">${steps}</div>` : ""}
        </div>`;
    })
    .join("");
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
        await Hud.postJSON(`/approvals/${encodeURIComponent(id)}`, {
          decision: button.getAttribute("data-decide"),
        });
        refreshCollections();
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
      const who = frame.data && frame.data.agent ? frame.data.agent.name : null;
      const bits = [frame.type, who, Hud.ago(frame.created_at)].filter(Boolean);
      return `
        <div class="feed-item">
          <span class="pip ${Hud.tone(frame.type)}"></span>
          <span class="txt">
            <span class="msg">${Hud.escape(frame.message)}</span>
            <span class="meta">${Hud.escape(bits.join(" · "))}</span>
          </span>
        </div>`;
    })
    .join("");
}

function renderAgentSummary() {
  const busy = state.agents.filter((a) => a.status === "working").length;
  document.querySelector("[data-agents-summary]").textContent = state.agents.length
    ? `${busy}/${state.agents.length} agents`
    : "";
}

/* -- machine vitals --------------------------------------------------------- */

function pollStats(intervalSeconds) {
  const run = async () => {
    try {
      renderStats(await Hud.getJSON("/dash/api/stats"));
    } catch (_) {
      /* a missed sample is not worth reporting */
    }
  };
  run();
  if (statsTimer) clearInterval(statsTimer);
  statsTimer = setInterval(run, Math.max(1000, intervalSeconds * 1000));
}

function renderStats(stats) {
  const host = el("stats");
  if (!stats.available) {
    host.innerHTML =
      '<div class="empty">Install psutil for live system stats:<br>' +
      '<code>pip install "jarvis-assistant[dash]"</code></div>';
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
  });

  if (!capability.can_listen) {
    mic.title = "Listening is not installed — text still works";
    mic.addEventListener("click", () =>
      Hud.toast(
        'Listening needs: pip install "jarvis-assistant[listen]"',
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
    try {
      const heard = await Hud.postJSON("/dash/api/command", {
        text,
        submit: true,
      });
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
    localStorage.setItem("jarvis.muted", muted ? "1" : "0");
    paint();
  });
}

/* -- helpers ---------------------------------------------------------------- */

function truncate(text, max) {
  const value = String(text || "");
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function shortenPath(path) {
  const parts = String(path || "").replace(/\\/g, "/").split("/");
  return parts.length > 3 ? `…/${parts.slice(-2).join("/")}` : path;
}
