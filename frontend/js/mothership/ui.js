/*
 * Small pieces every Mothership view uses: icons, formatting, charts, the
 * side drawer and the form dialog. Views build HTML strings and wire events
 * after inserting them; nothing here keeps state of its own beyond the chart
 * specs (so charts can be redrawn at a new width) and the open dialog.
 */

import { Hud } from "../lib/hud.js";

export const esc = Hud.escape;
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/* -- icons ----------------------------------------------------------------- */

const PATHS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  play: '<path d="M8 5v14l11-7z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/>',
  trash: '<path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/>',
  claude: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  link: '<path d="M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1"/><path d="M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1"/>',
  ask: '<path d="M4 5h16v11H9l-5 4z"/>',
  idea: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z"/>',
  command: '<path d="m5 8 4 4-4 4M11 16h8"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  shield: '<path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6z"/>',
  check: '<path d="m5 12 4.5 4.5L19 7"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  list: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  bolt: '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>',
  chart: '<path d="M4 19V5M4 19h16M8 15l4-4 3 3 5-6"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  redo: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>',
  phone: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  report: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  update: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
};

export function icon(name, cls = "") {
  return `<svg class="ic ${cls}" viewBox="0 0 24 24">${PATHS[name] || ""}</svg>`;
}

/* -- formatting -------------------------------------------------------------- */

export const ago = (iso) => Hud.ago(iso);

export function clock(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return time;
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`;
  return `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

export function dayLabel(iso) {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
}

/** Seconds a task took, or null while it runs. */
export function took(task) {
  if (!["completed", "failed"].includes(task.status)) return null;
  const s = (new Date(task.updated_at) - new Date(task.created_at)) / 1000;
  return Number.isFinite(s) && s >= 0 ? s : null;
}

export function seconds(s) {
  if (s === null || s === undefined) return "—";
  if (s < 10) return `${s.toFixed(1)}s`;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${String(Math.round(s % 60)).padStart(2, "0")}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function number(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (abs >= 1 || value === 0) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return value.toLocaleString(undefined, { maximumSignificantDigits: 3 });
}

export function percent(part, whole) {
  return whole ? Math.round((part / whole) * 100) : 0;
}

/** "+12%" / "-3%" against a previous value, with a tone for colour. */
export function delta(now, before) {
  if (!before) return now ? { text: "new", tone: "up" } : { text: "", tone: "" };
  const change = Math.round(((now - before) / before) * 100);
  return { text: `${change >= 0 ? "↑" : "↓"} ${Math.abs(change)}%`, tone: change >= 0 ? "up" : "down" };
}

export function initials(text) {
  const words = String(text || "?").replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/);
  return ((words[0] || "?")[0] + (words[1] ? words[1][0] : "")).toUpperCase();
}

/** Just enough Markdown for answers: paragraphs, lists, bold, code, links. */
export function markdown(text) {
  const inline = (line) =>
    esc(line)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  const html = [];
  let list = false;
  let code = null;
  for (const raw of String(text || "").split(/\n/)) {
    if (raw.trim().startsWith("```")) {
      if (code === null) code = [];
      else {
        html.push(`<pre>${esc(code.join("\n"))}</pre>`);
        code = null;
      }
      continue;
    }
    if (code !== null) {
      code.push(raw);
      continue;
    }
    const line = raw.trimEnd();
    const item = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (item) {
      if (!list) html.push("<ul>");
      list = true;
      html.push(`<li>${inline(item[1])}</li>`);
      continue;
    }
    if (list) html.push("</ul>");
    list = false;
    if (line.trim()) html.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
  }
  if (list) html.push("</ul>");
  if (code !== null) html.push(`<pre>${esc(code.join("\n"))}</pre>`);
  return html.join("");
}

/** One line of plain text from a reply. */
export function plain(text) {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\*\*|__|`/g, "")
    .replace(/^\s*(?:#+|[-*•])\s+/gm, "")
    .replace(/\s*\n+\s*/g, " — ")
    .trim();
}

const STATUS = {
  completed: ["Done", "ok"],
  failed: ["Failed", "bad"],
  running: ["Running", "warn"],
  pending: ["Queued", "warn"],
  waiting_approval: ["Waiting", "warn"],
};

export function pill(status) {
  const [label, tone] = STATUS[status] || [status, ""];
  return `<span class="pill ${tone}">${esc(label)}</span>`;
}

export const KIND_LABEL = {
  asked: "Asked",
  notice: "Notice",
  explain: "Explain",
  control: "Control",
};

/* -- charts ---------------------------------------------------------------- */

/** Little bars, like a KPI card's corner. */
export function bars(values, tone = "") {
  const list = values.length ? values : [0];
  const top = Math.max(...list, 1);
  const width = 6;
  const gap = 3;
  const height = 30;
  const rects = list
    .map((v, i) => {
      const h = Math.max(2, (v / top) * height);
      const fade = 0.35 + 0.65 * ((i + 1) / list.length);
      return `<rect x="${i * (width + gap)}" y="${height - h}" width="${width}" height="${h}" rx="1.5" opacity="${fade.toFixed(2)}"/>`;
    })
    .join("");
  return `<svg class="bars ${tone}" viewBox="0 0 ${list.length * (width + gap)} ${height}" preserveAspectRatio="none">${rects}</svg>`;
}

const charts = new Map();
let chartCount = 0;

/**
 * A line chart that fills its container. Returns a placeholder; call
 * drawCharts() once it is in the page (and it redraws itself on resize).
 * series: [{values: [...], tone: "a"|"b"|..., dashed, area}], labels: [...]
 */
export function lineChart(series, labels, { height = 220, format = number, counts = false } = {}) {
  const id = `chart-${++chartCount}`;
  charts.set(id, { series, labels, height, format, counts });
  return `<div class="chart" id="${id}" style="height:${height}px"></div>`;
}

function niceMax(value) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (value <= step * power) return step * power;
  }
  return 10 * power;
}

// Axis text is 10.5px monospace (mothership.css): about this wide per character.
const CHAR = 6.6;

/** Round values across [low, high] for the y axis — 99,850 / 99,900 / 99,950,
 * not 99,806.99 — and how many decimals they need. */
function ticks(low, high) {
  let out;
  for (const parts of [4, 6, 8]) {  // a finer step until there are at least 4 lines
    const step = Number(niceMax((high - low) / parts).toPrecision(6));
    const decimals = Math.min(4, (String(step).split(".")[1] || "").length);
    const values = [];
    for (let v = Math.ceil(low / step) * step; v <= high + step / 1e6; v += step) {
      values.push(Number(v.toFixed(decimals)) + 0);  // + 0: no "-0.00"
    }
    out = { values, decimals };
    if (values.length >= 4) break;
  }
  return out;
}

export function drawCharts() {
  for (const [id, spec] of charts) {
    const host = document.getElementById(id);
    if (!host) {
      charts.delete(id);
      continue;
    }
    const width = Math.max(200, host.clientWidth);
    const { series, labels, height, format, counts } = spec;
    const all = series.flatMap((s) => s.values).filter((v) => Number.isFinite(v));
    // Values like an equity curve sit far from zero: frame their range.
    // Counts start at zero and step in whole numbers (0, 1, 2, 3, 4 at least).
    let low = counts ? 0 : Math.min(...all);
    let high = Math.max(...all, low);
    let yTicks;
    if (counts) {
      high = Math.max(4, Math.ceil(niceMax(high) / 4) * 4);
      yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => [high * f, format(high * f)]);
    } else {
      const room = (high - low) * 0.1 || Math.abs(high) * 0.01 || 1;
      low -= room;
      high += room;
      const { values, decimals } = ticks(low, high);
      const fixed = (v) =>
        v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
      yTicks = values.map((v) => [v, format === number ? fixed(v) : format(v)]);
    }
    // Room on the left for the widest y label, so none is cut off.
    const yWidth = Math.max(...yTicks.map(([, text]) => String(text).length)) * CHAR;
    const pad = { left: Math.ceil(Math.max(28, yWidth + 12)), right: 10, top: 10, bottom: 26 };
    const count = Math.max(...series.map((s) => s.values.length), 2);
    const x = (i) => pad.left + (i / (count - 1)) * (width - pad.left - pad.right);
    const y = (v) => pad.top + (1 - (v - low) / (high - low || 1)) * (height - pad.top - pad.bottom);
    const grid = yTicks
      .map(([value, text]) => {
        const gy = y(value);
        return `<line x1="${pad.left}" x2="${width - pad.right}" y1="${gy}" y2="${gy}"/>
          <text x="${pad.left - 8}" y="${gy + 4}" text-anchor="end">${esc(text)}</text>`;
      })
      .join("");
    // x labels: as many as fit without touching, never the same one twice in a row,
    // and the ones at the edges kept inside the chart.
    const xWidth = Math.max(1, ...labels.map((l) => String(l).length)) * CHAR;
    const step = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor((width - pad.left) / (xWidth + 16)))));
    let shown = null;
    let shownAt = -Infinity;
    const xLabels = labels
      .map((label, i) => {
        const at = x(i);
        const last = i === labels.length - 1;
        if ((i % step !== 0 && !last) || String(label) === shown || at - shownAt < xWidth + 8) return "";
        shown = String(label);
        shownAt = at;
        const anchor = at + xWidth / 2 > width ? "end" : at - xWidth / 2 < 0 ? "start" : "middle";
        return `<text x="${at}" y="${height - 6}" text-anchor="${anchor}">${esc(label)}</text>`;
      })
      .join("");
    const lines = series
      .map((s) => {
        const points = s.values.map((v, i) => [x(i), y(Number.isFinite(v) ? v : low)]);
        if (!points.length) return "";
        const path = smooth(points);
        const area = s.area
          ? `<path class="area ${s.tone}" d="${path} L${points[points.length - 1][0]},${y(low)} L${points[0][0]},${y(low)} Z"/>`
          : "";
        return `${area}<path class="line ${s.tone} ${s.dashed ? "dashed" : ""}" d="${path}"/>`;
      })
      .join("");
    host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">
      <g class="grid">${grid}</g><g class="xlabels">${xLabels}</g>${lines}</svg>`;
  }
}

/** A gently curved path through points (monotone-ish Catmull-Rom). */
function smooth(points) {
  if (points.length < 3) return `M${points.map((p) => p.join(",")).join(" L")}`;
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 0; i < points.length - 1; i++) {
    const [p0, p1, p2, p3] = [points[i - 1] || points[i], points[i], points[i + 1], points[i + 2] || points[i + 1]];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    const top = Math.min(p1[1], p2[1]);
    const bottom = Math.max(p1[1], p2[1]);
    c1[1] = Math.min(bottom, Math.max(top, c1[1]));
    c2[1] = Math.min(bottom, Math.max(top, c2[1]));
    d += ` C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
  }
  return d;
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(drawCharts, 120);
});

/* -- report viewer ------------------------------------------------------------ */

/**
 * A page — a report — over the dashboard. HTML is shown in a sandboxed frame:
 * its scripts run (for charts) but in an origin of their own, away from this
 * page's token. Anything else is shown as plain text.
 */
export function openViewer({ kicker = "", title = "", html = null, text = "" }) {
  $("#ms-viewer-kicker").innerHTML = kicker;
  $("#ms-viewer-title").textContent = title;
  const body = $("#ms-viewer-body");
  body.innerHTML = "";
  if (html !== null) {
    const frame = document.createElement("iframe");
    frame.setAttribute("sandbox", "allow-scripts allow-popups");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.title = title;
    frame.srcdoc = html;
    body.appendChild(frame);
  } else {
    const pre = document.createElement("pre");
    pre.className = "viewer-text";
    pre.textContent = text;
    body.appendChild(pre);
  }
  $("#ms-viewer").hidden = false;
}

export function closeViewer() {
  $("#ms-viewer").hidden = true;
  $("#ms-viewer-body").innerHTML = "";
}

/* -- drawer ------------------------------------------------------------------ */

let drawerRefresh = null;

/** Open the side drawer. `render` returns {kicker, title, body} and may run again. */
export function openDrawer(render, wire) {
  drawerRefresh = () => {
    const view = render();
    if (!view) return closeDrawer();
    $("#ms-drawer-kicker").innerHTML = view.kicker || "";
    $("#ms-drawer-title").textContent = view.title || "";
    $("#ms-drawer-body").innerHTML = view.body || "";
    if (wire) wire($("#ms-drawer-body"));
  };
  drawerRefresh();
  $("#ms-drawer").hidden = false;
  $("#ms-shade").hidden = false;
  requestAnimationFrame(() => $("#ms-drawer").classList.add("open"));
}

export function refreshDrawer() {
  if (drawerRefresh && !$("#ms-drawer").hidden) drawerRefresh();
}

export function closeDrawer() {
  drawerRefresh = null;
  $("#ms-drawer").classList.remove("open");
  $("#ms-drawer").hidden = true;
  $("#ms-shade").hidden = true;
}

/* -- form dialog --------------------------------------------------------------- */

let formSubmit = null;

/**
 * A dialog with fields. fields: [{name, label, type: text|textarea|select|checkbox,
 * value, options: [[value, label]], placeholder, hint, list: [...], show: (values) => bool,
 * label: string | (values) => string}]. onSubmit(values) may throw to show an error.
 */
export function openForm({ title, fields, submit = "Save", onSubmit, extra = "" }) {
  $("#ms-modal-title").textContent = title;
  const body = $("#ms-modal-body");
  body.innerHTML = fields
    .map((f) => {
      const id = `f-${f.name}`;
      const hint = f.hint ? `<small class="hint">${esc(f.hint)}</small>` : "";
      let input;
      if (f.type === "textarea") {
        input = `<textarea id="${id}" name="${f.name}" rows="${f.rows || 3}" placeholder="${esc(f.placeholder || "")}">${esc(f.value || "")}</textarea>`;
      } else if (f.type === "select") {
        input = `<select id="${id}" name="${f.name}">${f.options
          .map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(f.value ?? "") ? "selected" : ""}>${esc(l)}</option>`)
          .join("")}</select>`;
      } else if (f.type === "checkbox") {
        return `<label class="field check" data-field="${f.name}"><input type="checkbox" id="${id}" name="${f.name}" ${f.value ? "checked" : ""}><span>${esc(typeof f.label === "function" ? "" : f.label)}</span>${hint}</label>`;
      } else {
        const list = f.list ? `list="${id}-list"` : "";
        const datalist = f.list ? `<datalist id="${id}-list">${f.list.map((v) => `<option value="${esc(v)}">`).join("")}</datalist>` : "";
        input = `<input id="${id}" name="${f.name}" type="${f.type || "text"}" value="${esc(f.value || "")}" placeholder="${esc(f.placeholder || "")}" autocomplete="off" ${list}>${datalist}`;
      }
      return `<label class="field" data-field="${f.name}"><span class="label" data-label="${f.name}"></span>${input}${hint}</label>`;
    })
    .join("");

  const values = () => {
    const out = {};
    for (const f of fields) {
      const node = body.querySelector(`[name="${f.name}"]`);
      out[f.name] = f.type === "checkbox" ? node.checked : node.value.trim();
    }
    return out;
  };
  const sync = () => {
    const v = values();
    for (const f of fields) {
      const wrap = body.querySelector(`[data-field="${f.name}"]`);
      wrap.hidden = f.show ? !f.show(v) : false;
      const label = body.querySelector(`[data-label="${f.name}"]`);
      if (label) label.textContent = typeof f.label === "function" ? f.label(v) : f.label;
    }
  };
  body.addEventListener("input", sync);
  body.addEventListener("change", sync);
  sync();

  $("#ms-modal-foot").innerHTML = `${extra}<span class="grow"></span>
    <button type="button" class="btn ghost" data-close>Cancel</button>
    <button type="submit" class="btn primary">${esc(submit)}</button>`;
  $("#ms-modal-foot").querySelector("[data-close]").addEventListener("click", closeForm);
  $("#ms-modal-err").textContent = "";
  formSubmit = async () => {
    const button = $("#ms-modal-foot").querySelector("[type=submit]");
    button.disabled = true;
    try {
      await onSubmit(values());
      closeForm();
    } catch (err) {
      $("#ms-modal-err").textContent = String(err.message || err);
    } finally {
      button.disabled = false;
    }
  };
  $("#ms-modal").hidden = false;
  const first = body.querySelector("input, textarea, select");
  if (first) setTimeout(() => first.focus(), 30);
  return $("#ms-modal-foot");
}

export function closeForm() {
  formSubmit = null;
  $("#ms-modal").hidden = true;
}

document.addEventListener("DOMContentLoaded", () => {
  $("#ms-modal-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (formSubmit) formSubmit();
  });
  $("#ms-modal-close").addEventListener("click", closeForm);
  $("#ms-drawer-close").addEventListener("click", closeDrawer);
  $("#ms-viewer-close").addEventListener("click", closeViewer);
  $("#ms-viewer").addEventListener("click", (event) => {
    if (event.target.id === "ms-viewer") closeViewer();
  });
  $("#ms-shade").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("#ms-modal").hidden) closeForm();
    else if (!$("#ms-viewer").hidden) closeViewer();
    else closeDrawer();
  });
});

/* -- actions ------------------------------------------------------------------- */

/** Run an API call; toast its error rather than throw. Returns the result or null. */
export async function act(promise, done) {
  try {
    const result = await promise;
    if (done) Hud.toast(done, "ok");
    return result;
  } catch (err) {
    Hud.toast(String(err.message || err), "bad");
    return null;
  }
}

/** Open a terminal on the Terminal page. */
export function showTerminal(id) {
  if (id) Hud.save("terminal", id);
  window.location.href = "terminal.html";
}
