/*
 * Draws any project's status file — whatever JSON it writes — as a readable
 * panel, without knowing the project. The shape decides the picture:
 *
 *   plain values             → number tiles   (cash, halted, peak equity)
 *   [time, number] pairs     → a line chart   (an equity curve)
 *   a list of objects        → a table        (trades, positions)
 *   {name: number, ...}      → bars           (strategy weights)
 *   {name: {…}, ...}         → a table        (signals per symbol)
 *   anything else            → a key/value list
 */

import { esc, number, lineChart } from "./ui.js";

const label = (key) =>
  String(key)
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^\w/, (c) => c.toUpperCase());

const isScalar = (v) => v === null || ["string", "number", "boolean"].includes(typeof v);
const isObject = (v) => v && typeof v === "object" && !Array.isArray(v);

function value(v) {
  if (v === null || v === undefined) return '<span class="muted">—</span>';
  if (typeof v === "boolean") return `<span class="pill ${v ? "warn" : "ok"}">${v ? "Yes" : "No"}</span>`;
  if (typeof v === "number") return esc(number(v));
  if (Array.isArray(v)) return esc(v.map((x) => (isScalar(x) ? String(x) : "…")).join(", "));
  if (isObject(v)) return `<span class="muted">${Object.keys(v).length} fields</span>`;
  const text = String(v);
  return esc(/^\d{4}-\d\d-\d\d[T ]\d\d:\d\d/.test(text) ? text.slice(0, 16).replace("T", " ") : text);
}

const isSeries = (v) =>
  Array.isArray(v) &&
  v.length > 1 &&
  v.every((p) => (Array.isArray(p) && p.length === 2 && typeof p[1] === "number") || typeof p === "number");

const isRows = (v) => Array.isArray(v) && v.length && v.every(isObject);

const STAMP = /^\d{4}-(\d\d)-(\d\d)(?:[T ](\d\d:\d\d))?/;

/** x labels for a series: dates as "08-10", or times ("14:30") when it all
 * happened on one day; anything else ("round 3") as it is, or 1, 2, 3. */
function timeLabels(keys) {
  const stamps = keys.map((k) => (k == null ? null : STAMP.exec(String(k))));
  if (!stamps.every(Boolean)) return keys.map((k, i) => (k == null ? String(i + 1) : String(k)));
  const oneDay = new Set(stamps.map((m) => m[1] + m[2])).size === 1 && stamps.every((m) => m[3]);
  return stamps.map((m) => (oneDay ? m[3] : `${m[1]}-${m[2]}`));
}

/** Flatten one level of nesting: {votes: {a: 1}} → {"votes · a": 1}. */
function flat(obj, prefix = "", depth = 0) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix} · ${k}` : k;
    if (isObject(v) && depth < 1) Object.assign(out, flat(v, key, depth + 1));
    else out[key] = v;
  }
  return out;
}

function table(rows, names) {
  const flatRows = rows.map((r) => flat(r));
  const columns = [...new Set(flatRows.flatMap((r) => Object.keys(r)))].slice(0, 9);
  return `<div class="table-wrap"><table class="data">
    <thead><tr>${names ? "<th></th>" : ""}${columns.map((c) => `<th>${esc(label(c.split(" · ").pop()))}</th>`).join("")}</tr></thead>
    <tbody>${flatRows
      .map((r, i) => `<tr>${names ? `<th>${esc(names[i])}</th>` : ""}${columns
        .map((c) => `<td class="${typeof r[c] === "number" ? (r[c] < 0 ? "num neg" : "num") : ""}">${value(r[c])}</td>`)
        .join("")}</tr>`)
      .join("")}</tbody></table></div>`;
}

export function renderStatus(data) {
  if (!isObject(data)) return `<pre class="status-text">${esc(JSON.stringify(data, null, 2))}</pre>`;
  const tiles = [];
  const blocks = [];
  for (const [key, v] of Object.entries(data)) {
    if (isScalar(v)) {
      tiles.push(`<div class="tile"><span>${esc(label(key))}</span><b>${value(v)}</b></div>`);
    } else if (isSeries(v)) {
      const points = v.map((p) => (Array.isArray(p) ? p : [null, p]));
      const values = points.map((p) => p[1]);
      const labels = timeLabels(points.map((p) => p[0]));
      const first = values[0];
      const last = values[values.length - 1];
      const change = first ? ((last - first) / Math.abs(first)) * 100 : 0;
      blocks.push(`<div class="status-block wide">
        <h4>${esc(label(key))} <b>${esc(number(last))}</b>
          <span class="${change >= 0 ? "up" : "down"}">${change >= 0 ? "↑" : "↓"} ${Math.abs(change).toFixed(2)}%</span></h4>
        ${lineChart([{ values, tone: "a", area: true }], labels, { height: 180 })}</div>`);
    } else if (Array.isArray(v)) {
      const rows = v.slice(-12).reverse();
      blocks.push(`<div class="status-block wide"><h4>${esc(label(key))} <span class="muted">${v.length}</span></h4>${
        !v.length ? '<p class="muted">None.</p>' : isRows(rows) ? table(rows) : `<p>${value(v)}</p>`
      }</div>`);
    } else if (isObject(v)) {
      const entries = Object.entries(v);
      if (entries.length && entries.every(([, x]) => typeof x === "number")) {
        const top = Math.max(...entries.map(([, x]) => Math.abs(x)), 1e-9);
        blocks.push(`<div class="status-block"><h4>${esc(label(key))}</h4><div class="hbars">${entries
          .map(([k, x]) => `<div class="hbar"><span>${esc(label(k))}</span>
            <i><b class="${x < 0 ? "neg" : ""}" style="width:${(Math.abs(x) / top) * 100}%"></b></i>
            <em>${esc(number(x))}</em></div>`)
          .join("")}</div></div>`);
      } else if (entries.length && entries.every(([, x]) => isObject(x))) {
        blocks.push(`<div class="status-block wide"><h4>${esc(label(key))} <span class="muted">${entries.length}</span></h4>
          ${table(entries.map(([, x]) => x), entries.map(([k]) => label(k)))}</div>`);
      } else {
        blocks.push(`<div class="status-block"><h4>${esc(label(key))}</h4><dl class="kv">${entries
          .map(([k, x]) => `<dt>${esc(label(k))}</dt><dd>${isObject(x) || isRows(x) ? renderNested(x) : value(x)}</dd>`)
          .join("")}</dl></div>`);
      }
    }
  }
  return `${tiles.length ? `<div class="tiles">${tiles.join("")}</div>` : ""}<div class="status-blocks">${blocks.join("")}</div>`;
}

function renderNested(x) {
  if (isObject(x) && Object.values(x).every((v) => typeof v === "number")) {
    return Object.entries(x).map(([k, v]) => `${esc(label(k))} <b>${esc(number(v))}</b>`).join(" · ");
  }
  if (isObject(x) && Object.values(x).every(isObject)) {
    return table(Object.values(x), Object.keys(x).map(label));
  }
  return `<code>${esc(JSON.stringify(x).slice(0, 300))}</code>`;
}
