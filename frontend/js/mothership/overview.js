/*
 * The overview: the numbers that matter at a glance (requests, response
 * time, approvals, success, controls, projects), a week of requests as a
 * chart, what just happened, the latest requests as a clickable stack, and
 * the machine itself.
 */

import { state } from "./state.js";
import {
  esc, $$, icon, bars, lineChart, drawCharts, took, seconds, delta, percent, ago, initials,
  clock, pill, plain,
} from "./ui.js";
import { wireCards } from "./requests.js";
import { editControl } from "./controls.js";
import { editProject } from "./projects.js";

let range = 7;

export function title() {
  return "Overview";
}

const DAY = 24 * 3600 * 1000;
const startOfDay = (offset = 0) => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.getTime() - offset * DAY;
};
const at = (t) => new Date(t.created_at).getTime();

function kpis() {
  const today = startOfDay();
  const yesterday = startOfDay(1);
  const tasks = state.tasks;
  const todays = tasks.filter((t) => at(t) >= today);
  const yesterdays = tasks.filter((t) => at(t) >= yesterday && at(t) < today);

  // Requests in 2-hour buckets over the last day, for the little bars.
  const now = Date.now();
  const buckets = Array.from({ length: 12 }, (_, i) =>
    tasks.filter((t) => {
      const age = now - at(t);
      return age < (12 - i) * 2 * 3600e3 && age >= (11 - i) * 2 * 3600e3;
    }).length
  );

  const durations = (list) => list.map(took).filter((s) => s !== null);
  const mean = (list) => (list.length ? list.reduce((a, b) => a + b, 0) / list.length : null);
  const avgToday = mean(durations(todays));
  const avgBefore = mean(durations(yesterdays));
  const lastTimes = durations(tasks.slice(0, 12)).reverse();

  const week = tasks.filter((t) => at(t) >= startOfDay(6));
  const finished = week.filter((t) => ["completed", "failed"].includes(t.status));
  const failed = finished.filter((t) => t.status === "failed").length;

  const autoToday = state.events.filter(
    (e) => e.type === "approval.auto" && new Date(e.created_at).getTime() >= today
  ).length;

  const ready = state.controls.filter((c) => c.kind !== "idea" && c.action).length;
  const ideas = state.projects.flatMap((p) => p.ideas || []);
  const openIdeas = ideas.filter((i) => !i.done).length;

  const running = state.terminals.filter((t) => t.running).length;
  return {
    requests: { count: todays.length, delta: delta(todays.length, yesterdays.length), week: week.length, buckets },
    speed: { avg: avgToday, delta: avgToday && avgBefore ? delta(avgBefore, avgToday) : { text: "", tone: "" }, lastTimes },
    approvals: { waiting: state.approvals.length, auto: autoToday },
    success: { rate: percent(finished.length - failed, finished.length), failed, finished: finished.length },
    controls: { ready, total: state.controls.length, ideas: state.controls.length - ready },
    projects: { count: state.projects.length, open: openIdeas, done: ideas.length - openIdeas, total: ideas.length },
    terminals: { open: state.terminals.length, running },
  };
}

function series(days) {
  const labels = [];
  const asked = [];
  const failed = [];
  for (let i = days - 1; i >= 0; i--) {
    const from = startOfDay(i);
    const to = from + DAY;
    const list = state.tasks.filter((t) => at(t) >= from && at(t) < to);
    const d = new Date(from);
    labels.push(days <= 7 ? d.toLocaleDateString([], { weekday: "short" }) : `${d.getDate()}/${d.getMonth() + 1}`);
    asked.push(list.length);
    failed.push(list.filter((t) => t.status === "failed").length);
  }
  return { labels, asked, failed };
}

const ACTIVITY = {
  "task.completed": ["J", "ok"],
  "task.failed": ["J", "bad"],
  "approval.required": ["!", "warn"],
  "approval.resolved": ["✓", "ok"],
  "approval.auto": ["✓", "plan"],
  "terminal.opened": ["T", ""],
  "terminal.finished": ["T", "ok"],
  "terminal.failed": ["T", "bad"],
  "terminal.closed": ["T", ""],
  "mothership.updated": ["M", "plan"],
};

function activity() {
  const items = state.events.filter((e) => ACTIVITY[e.type]).slice(-7).reverse();
  if (!items.length) return '<p class="muted pad">Nothing yet — ask Jarvis something.</p>';
  return `<ul class="activity">${items
    .map((e) => {
      const [mark, tone] = ACTIVITY[e.type];
      return `<li ${e.task_id ? `data-task="${esc(e.task_id)}"` : ""}>
        <span class="avatar ${tone}">${esc(mark)}</span>
        <span><span class="act-msg">${esc(plain(e.message)).slice(0, 140)}</span>
        <small class="muted">${esc(ago(e.created_at))}</small></span></li>`;
    })
    .join("")}</ul>`;
}

function system() {
  const s = state.stats;
  if (!s || !s.available) return '<p class="muted pad">Install psutil for machine stats.</p>';
  const row = (name, tone, pct, note) => `
    <div class="sys-row"><span class="sys-dot ${tone}"></span>${esc(name)}
      <span class="grow"></span><b>${Math.round(pct || 0)}%</b>${note ? `<small class="muted">${esc(note)}</small>` : ""}</div>`;
  const gb = (b) => `${(b / 1e9).toFixed(1)} GB`;
  const parts = [
    ["CPU", "a", s.cpu, ""],
    ["Memory", "b", s.memory && s.memory.percent, s.memory ? `${gb(s.memory.used)} of ${gb(s.memory.total)}` : ""],
    ["Disk", "c", s.disk && s.disk.percent, s.disk ? `${gb(s.disk.used)} of ${gb(s.disk.total)}` : ""],
  ];
  if (s.battery) parts.push(["Battery", "d", s.battery.percent, s.battery.plugged ? "charging" : ""]);
  return `
    <div class="stackbar">${parts.map(([, tone, pct]) => `<i class="${tone}" style="width:${(pct || 0) / parts.length}%"></i>`).join("")}</div>
    ${parts.map(([name, tone, pct, note]) => row(name, tone, pct, note)).join("")}
    ${state.system ? `<div class="sys-model">${icon("bolt")} ${esc(state.system.brain?.model || "")}
      <span class="muted">· ${(state.system.tools || []).length} tools</span></div>` : ""}`;
}

export function render(root) {
  const k = kpis();
  const s = series(range);
  const total = s.asked.reduce((a, b) => a + b, 0);
  const prev = series(range * 2).asked.slice(0, range).reduce((a, b) => a + b, 0);
  const trend = delta(total, prev);
  const latest = state.tasks.slice(0, 8);

  root.innerHTML = `
    <div class="page-head">
      <div><div class="kicker">Overview</div><h1>Mothership</h1></div>
      <div class="head-actions">
        <button class="btn" data-new-project>${icon("plus")} New project</button>
        <button class="btn primary" data-new-control>${icon("plus")} New control</button>
      </div>
    </div>

    <div class="kpis">
      <a class="kpi" href="#/requests">
        <span class="kpi-icon a">${icon("list")}</span>
        <span class="kpi-body"><span class="kpi-label">Requests today</span>
          <span class="kpi-value">${k.requests.count}<em class="${k.requests.delta.tone}">${esc(k.requests.delta.text)}</em></span>
          <span class="kpi-sub">${k.requests.week} this week</span></span>
        ${bars(k.requests.buckets, "a")}
      </a>
      <div class="kpi">
        <span class="kpi-icon b">${icon("clock")}</span>
        <span class="kpi-body"><span class="kpi-label">Avg answer</span>
          <span class="kpi-value">${k.speed.avg === null ? "—" : esc(seconds(k.speed.avg))}<em class="${k.speed.delta.tone}">${esc(k.speed.delta.text)}</em></span>
          <span class="kpi-sub">today · faster is ↑</span></span>
        ${bars(k.speed.lastTimes, "b")}
      </div>
      <a class="kpi" href="#/approvals">
        <span class="kpi-icon c">${icon("shield")}</span>
        <span class="kpi-body"><span class="kpi-label">Approvals waiting</span>
          <span class="kpi-value">${k.approvals.waiting}</span>
          <span class="kpi-sub">${k.approvals.auto} ran without asking today</span></span>
        <span class="kpi-big ${k.approvals.waiting ? "hot" : ""}">${k.approvals.waiting ? "!" : "✓"}</span>
      </a>
      <a class="kpi tall" href="#/requests">
        <span class="kpi-icon d">${icon("check")}</span>
        <span class="kpi-body"><span class="kpi-label">Success · 7 days</span>
          <span class="kpi-value">${k.success.finished ? `${k.success.rate}%` : "—"}</span>
          <span class="kpi-sub">${k.success.failed} failed of ${k.success.finished}</span></span>
        <span class="kpi-bar"><i class="ok" style="width:${k.success.rate}%"></i></span>
      </a>
      <a class="kpi tall" href="#/controls">
        <span class="kpi-icon e">${icon("bolt")}</span>
        <span class="kpi-body"><span class="kpi-label">Controls</span>
          <span class="kpi-value">${k.controls.ready}<small>/${k.controls.total} ready</small></span>
          <span class="kpi-sub">${k.controls.ideas} ${k.controls.ideas === 1 ? "idea" : "ideas"} to build with Claude</span></span>
        <span class="kpi-bar"><i class="blue" style="width:${percent(k.controls.ready, k.controls.total)}%"></i></span>
      </a>
      <div class="kpi tall">
        <span class="kpi-icon f">${icon("folder")}</span>
        <span class="kpi-body"><span class="kpi-label">Projects</span>
          <span class="kpi-value">${k.projects.count}<small> · ${k.terminals.open} terminals</small></span>
          <span class="kpi-sub">${k.projects.open} open ideas · ${k.terminals.running} commands running</span></span>
        <span class="kpi-bar"><i class="violet" style="width:${percent(k.projects.done, k.projects.total)}%"></i></span>
      </div>
    </div>

    <div class="cols">
      <section class="card col-main">
        <header class="card-head">
          <div><h2>Requests</h2>
            <div class="big-num">${total}<em class="${trend.tone}">${esc(trend.text)}</em></div>
            <span class="muted">asked in the last ${range} days</span></div>
          <div class="seg">${[7, 30, 90].map((d) => `<button class="${range === d ? "on" : ""}" data-range="${d}">${d} days</button>`).join("")}</div>
        </header>
        ${lineChart([
          { values: s.asked, tone: "a", area: true },
          { values: s.failed, tone: "bad", dashed: true },
        ], s.labels, { counts: true, format: (v) => String(Math.round(v)) })}
        <div class="legend"><span class="key a"></span>Requests <span class="key bad dashed"></span>Failed</div>
      </section>
      <section class="card col-side">
        <header class="card-head"><h2>Recent activity</h2></header>
        ${activity()}
      </section>
    </div>

    <div class="cols">
      <section class="card col-main">
        <header class="card-head"><div><h2>Request stack</h2><span class="muted">latest ${latest.length} — click for the whole story</span></div>
          <a class="btn small" href="#/requests">View all →</a></header>
        ${latest.length ? `<div class="table-wrap"><table class="data rows">
          <thead><tr><th>Time</th><th>Request</th><th>Output</th><th>Status</th><th>Took</th></tr></thead>
          <tbody>${latest
            .map((t) => `<tr data-task="${esc(t.id)}">
              <td class="nowrap muted">${esc(clock(t.created_at))}</td>
              <td><span class="who"><span class="avatar small">${esc(initials(t.kind === "asked" ? "You" : t.kind))}</span>${esc(t.request.slice(0, 80))}</span></td>
              <td class="muted clip">${esc(plain(t.status === "failed" ? t.error : t.result).slice(0, 90))}</td>
              <td>${pill(t.status)}</td>
              <td class="nowrap">${esc(seconds(took(t)))}</td></tr>`)
            .join("")}</tbody></table></div>` : '<p class="muted pad">No requests yet.</p>'}
      </section>
      <section class="card col-side">
        <header class="card-head"><h2>This PC</h2></header>
        ${system()}
      </section>
    </div>`;

  $$("[data-range]", root).forEach((b) =>
    b.addEventListener("click", () => {
      range = Number(b.dataset.range);
      render(root);
    })
  );
  root.querySelector("[data-new-control]").addEventListener("click", () => editControl());
  root.querySelector("[data-new-project]").addEventListener("click", () => editProject());
  wireCards(root); // every [data-task] row opens that request
  drawCharts();
}
