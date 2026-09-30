/*
 * The file constellation: the workspace as a map, lighting up as Jarvis works.
 *
 * The layout is a radial tree — depth becomes distance from the centre, and
 * each folder is given an angular wedge in proportion to how many files sit
 * beneath it. That keeps big folders visually big without any physics
 * simulation, so the map is stable: a node stays where you last saw it instead
 * of drifting every time something re-renders.
 *
 * When Jarvis touches a file, the node flares and a pulse runs outward from
 * the root along the folder chain that leads to it. The point is to make the
 * *path* legible, not just the destination — you can see it reach into a
 * folder rather than a dot merely blinking somewhere.
 */

import { Hud } from "../lib/hud.js";

const el = (id) => document.getElementById(id);
const TAU = Math.PI * 2;

const RING = 108;          // pixels between depth rings
const FLARE_MS = 2600;     // how long a touched node stays lit
const PULSE_MS = 620;      // travel time along one edge

const canvas = el("map");
const ctx = canvas.getContext("2d");

let nodes = [];
let byPath = new Map();
let links = [];
let view = { x: 0, y: 0, scale: 1 };
let hover = null;
let selected = null;
let pulses = [];
let touches = [];
let ratio = 1;

/* -- boot ------------------------------------------------------------------ */

Hud.start(async () => {
  const snapshot = await Hud.getJSON(Hud.route("snapshot"));
  document.querySelector("[data-workspace]").textContent = shorten(snapshot.workspace);

  await loadTree();
  seedFromSnapshot(snapshot);

  resize();
  window.addEventListener("resize", resize);
  wireInteraction();
  requestAnimationFrame(draw);

  Hud.onEvent(onFrame);
  // The tree only changes when files do, and the event stream tells us when
  // that happens — but a slow rebuild would fight the animation, so it is
  // rate-limited rather than immediate.
  setInterval(() => loadTree().catch(() => {}), 45000);
});

async function loadTree() {
  const tree = await Hud.getJSON(Hud.route("tree"));
  layout(tree);
  el("node-count").textContent = `${nodes.length} nodes${tree.truncated ? " (capped)" : ""}`;
  renderSummary(tree);
  renderFolders();
}

function seedFromSnapshot(snapshot) {
  (snapshot.touched || []).slice(-24).forEach((touch) => {
    touches.push(touch);
    const node = byPath.get(touch.path);
    if (node) node.litAt = performance.now() - FLARE_MS * 0.7;
  });
  renderTouches();
}

/* -- layout ---------------------------------------------------------------- */

function layout(tree) {
  const previous = new Map(nodes.map((n) => [n.path, n]));
  const children = new Map();
  tree.nodes.forEach((node) => {
    if (node.parent === null || node.parent === undefined) return;
    if (!children.has(node.parent)) children.set(node.parent, []);
    children.get(node.parent).push(node.path);
  });

  const index = new Map(tree.nodes.map((n) => [n.path, { ...n }]));

  // Leaf counts drive the angular budget: a folder holding 200 files gets a
  // wider wedge than one holding 2, which is what makes the shape informative.
  const leaves = new Map();
  const countLeaves = (path) => {
    if (leaves.has(path)) return leaves.get(path);
    const kids = children.get(path) || [];
    const total = kids.length ? kids.reduce((sum, k) => sum + countLeaves(k), 0) : 1;
    leaves.set(path, total);
    return total;
  };
  countLeaves(".");

  const assign = (path, start, end, depth) => {
    const node = index.get(path);
    if (!node) return;
    const mid = (start + end) / 2;
    node.angle = mid;
    node.r = depth * RING;
    // A deterministic wobble per path keeps the map organic without making
    // it unstable — the same file always lands in the same place.
    const jitter = hash(path);
    node.x = Math.cos(mid) * node.r + (jitter % 17) - 8;
    node.y = Math.sin(mid) * node.r + ((jitter >> 5) % 17) - 8;
    node.litAt = previous.get(path)?.litAt ?? 0;
    node.litKind = previous.get(path)?.litKind ?? null;

    const kids = children.get(path) || [];
    if (!kids.length) return;
    const span = end - start;
    let cursor = start;
    // Leave a small gap between sibling wedges so branches read separately.
    const padding = Math.min(span * 0.04, 0.05);
    kids.forEach((kid) => {
      const share = (countLeaves(kid) / countLeaves(path)) * (span - padding * 2);
      assign(kid, cursor + padding, cursor + padding + share, depth + 1);
      cursor += share;
    });
  };
  assign(".", 0, TAU, 0);

  nodes = [...index.values()].filter((n) => n.angle !== undefined);
  byPath = new Map(nodes.map((n) => [n.path, n]));
  links = tree.links
    .map((link) => ({ a: byPath.get(link.source), b: byPath.get(link.target) }))
    .filter((link) => link.a && link.b);

  if (view.scale === 1 && view.x === 0 && view.y === 0) fit();
}

function fit() {
  if (!nodes.length) return;
  const maxR = Math.max(...nodes.map((n) => n.r)) + RING * 0.5;
  const size = Math.min(canvas.clientWidth, canvas.clientHeight);
  view.scale = Math.max(0.16, Math.min(1.4, (size * 0.44) / Math.max(1, maxR)));
  view.x = 0;
  view.y = 0;
}

/* -- live events ------------------------------------------------------------ */

function onFrame(frame) {
  if (!frame.files) return;
  const { action, paths } = frame.files;
  paths.forEach((path) => {
    touches.push({
      path,
      action,
      at: frame.created_at,
      agent_id: frame.agent_id,
    });
    if (touches.length > 60) touches.shift();

    const node = byPath.get(path) || byPath.get(path.replace(/\/$/, ""));
    if (!node) return;
    node.litAt = performance.now();
    node.litKind = action;
    firePulses(node, action);
  });
  renderTouches();
}

/* Light the chain of folders from the root down to the touched node. */
function firePulses(node, action) {
  const chain = [];
  let cursor = node;
  let guard = 0;
  while (cursor && guard++ < 24) {
    chain.unshift(cursor);
    cursor = cursor.parent ? byPath.get(cursor.parent) : null;
  }
  for (let i = 0; i < chain.length - 1; i++) {
    pulses.push({
      from: chain[i],
      to: chain[i + 1],
      action,
      start: performance.now() + i * PULSE_MS * 0.55,
    });
  }
  if (pulses.length > 200) pulses.splice(0, pulses.length - 200);
}

/* -- interaction ------------------------------------------------------------ */

function resize() {
  ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(canvas.clientWidth * ratio);
  canvas.height = Math.floor(canvas.clientHeight * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function toScreen(node) {
  return {
    x: canvas.clientWidth / 2 + (node.x + view.x) * view.scale,
    y: canvas.clientHeight / 2 + (node.y + view.y) * view.scale,
  };
}

function wireInteraction() {
  let dragging = false;
  let lastX = 0;
  let lastY = 0;

  canvas.addEventListener("mousedown", (event) => {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.classList.add("dragging");
  });

  window.addEventListener("mouseup", () => {
    dragging = false;
    canvas.classList.remove("dragging");
  });

  canvas.addEventListener("mousemove", (event) => {
    if (dragging) {
      view.x += (event.clientX - lastX) / view.scale;
      view.y += (event.clientY - lastY) / view.scale;
      lastX = event.clientX;
      lastY = event.clientY;
      return;
    }
    hover = pick(event.offsetX, event.offsetY);
    canvas.style.cursor = hover ? "pointer" : "grab";
  });

  canvas.addEventListener("click", (event) => {
    const hit = pick(event.offsetX, event.offsetY);
    selected = hit;
    renderFocus(hit);
  });

  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const factor = Math.exp(-event.deltaY * 0.0016);
      view.scale = Math.max(0.08, Math.min(4, view.scale * factor));
    },
    { passive: false }
  );

  canvas.addEventListener("dblclick", fit);
}

function pick(px, py) {
  let best = null;
  let bestDistance = 16;
  for (const node of nodes) {
    const point = toScreen(node);
    const distance = Math.hypot(point.x - px, point.y - py);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = node;
    }
  }
  return best;
}

/* -- rendering -------------------------------------------------------------- */

function draw() {
  requestAnimationFrame(draw);
  const now = performance.now();
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  ctx.clearRect(0, 0, width, height);

  // Links first, so nodes always sit on top of them.
  ctx.lineWidth = Math.max(0.4, 0.7 * view.scale);
  ctx.strokeStyle = "rgba(34,211,238,0.13)";
  ctx.beginPath();
  for (const link of links) {
    const a = toScreen(link.a);
    const b = toScreen(link.b);
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
  }
  ctx.stroke();

  drawPulses(now);

  for (const node of nodes) {
    const point = toScreen(node);
    if (
      point.x < -60 || point.x > width + 60 ||
      point.y < -60 || point.y > height + 60
    ) {
      continue;
    }
    const lit = node.litAt ? Math.max(0, 1 - (now - node.litAt) / FLARE_MS) : 0;
    const isDir = node.type === "dir";
    const base = isDir ? 3.1 : 1.9;
    const size = (base + lit * 5) * Math.max(0.55, Math.min(1.7, view.scale));

    if (lit > 0.01) {
      const [r, g, b] = actionRgb(node.litKind);
      const halo = ctx.createRadialGradient(point.x, point.y, 0, point.x, point.y, size * 6);
      halo.addColorStop(0, `rgba(${r},${g},${b},${(lit * 0.55).toFixed(3)})`);
      halo.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(point.x, point.y, size * 6, 0, TAU);
      ctx.fill();
    }

    const isFocus = node === hover || node === selected;
    ctx.fillStyle = lit > 0.01
      ? rgba(actionRgb(node.litKind), 0.55 + lit * 0.45)
      : isDir
        ? "rgba(167,139,250,0.72)"
        : "rgba(125,211,252,0.5)";
    if (isFocus) ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(point.x, point.y, size, 0, TAU);
    ctx.fill();

    // Label only what is readable: shallow folders, plus whatever is lit,
    // hovered or selected. Labelling everything is unreadable at any zoom.
    const label =
      isFocus || lit > 0.25 || (isDir && node.depth <= 1 && view.scale > 0.3);
    if (label) {
      ctx.font = `${Math.max(9, Math.min(13, 11 * view.scale))}px ui-monospace, monospace`;
      ctx.fillStyle = isFocus
        ? "rgba(255,255,255,0.95)"
        : `rgba(215,242,248,${(0.45 + lit * 0.55).toFixed(2)})`;
      ctx.textAlign = "center";
      ctx.fillText(node.name, point.x, point.y - size - 6);
    }
  }
}

function drawPulses(now) {
  pulses = pulses.filter((pulse) => now - pulse.start < PULSE_MS);
  for (const pulse of pulses) {
    const progress = (now - pulse.start) / PULSE_MS;
    if (progress < 0) continue;
    const a = toScreen(pulse.from);
    const b = toScreen(pulse.to);
    const eased = progress * progress * (3 - 2 * progress); // smoothstep
    const x = a.x + (b.x - a.x) * eased;
    const y = a.y + (b.y - a.y) * eased;
    const [r, g, b2] = actionRgb(pulse.action);
    const fade = 1 - progress;

    ctx.strokeStyle = `rgba(${r},${g},${b2},${(fade * 0.55).toFixed(3)})`;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(x, y);
    ctx.stroke();

    ctx.fillStyle = `rgba(${r},${g},${b2},${fade.toFixed(3)})`;
    ctx.beginPath();
    ctx.arc(x, y, 2.6, 0, TAU);
    ctx.fill();
  }
}

/* -- panels ----------------------------------------------------------------- */

function renderSummary(tree) {
  const files = tree.nodes.filter((n) => n.type === "file");
  const dirs = tree.nodes.filter((n) => n.type === "dir");
  const total = files.reduce((sum, f) => sum + (f.size || 0), 0);
  el("summary").innerHTML = `
    <div class="row"><span class="name">Files</span><span class="val">${files.length}</span></div>
    <div class="row"><span class="name">Folders</span><span class="val">${Math.max(0, dirs.length - 1)}</span></div>
    <div class="row"><span class="name">Total size</span><span class="val">${Hud.bytes(total)}</span></div>
    <div class="row"><span class="name">Depth scanned</span><span class="val">${tree.depth}</span></div>
    ${tree.truncated ? '<div class="hint">Node cap reached — only the first few hundred entries are drawn.</div>' : ""}
  `;
}

function renderFolders() {
  const sizes = new Map();
  for (const node of nodes) {
    if (node.type !== "file" || !node.size) continue;
    const owner = node.parent || ".";
    sizes.set(owner, (sizes.get(owner) || 0) + node.size);
  }
  const top = [...sizes.entries()].sort((a, b) => b[1] - a[1]).slice(0, 9);
  el("folders").innerHTML = top.length
    ? top
        .map(
          ([path, size]) =>
            `<div class="row"><span class="name">${Hud.escape(path)}</span><span class="val">${Hud.bytes(size)}</span></div>`
        )
        .join("")
    : '<div class="empty">No files found in the workspace yet.</div>';
}

function renderTouches() {
  const host = el("touches");
  el("touch-count").textContent = touches.length ? `${touches.length}` : "";
  if (!touches.length) {
    host.innerHTML = '<div class="empty">No files touched yet.</div>';
    return;
  }
  host.innerHTML = touches
    .slice(-22)
    .reverse()
    .map(
      (touch) => `
      <div class="feed-item">
        <span class="pip ${toneFor(touch.action)}"></span>
        <span class="txt">
          <span class="msg">${Hud.escape(touch.path)}</span>
          <span class="meta">${Hud.escape(touch.action)} · ${Hud.ago(touch.at)}</span>
        </span>
      </div>`
    )
    .join("");
}

function renderFocus(node) {
  const panel = el("focus-panel");
  if (!node) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "";
  const recent = touches.filter((t) => t.path === node.path).slice(-4).reverse();
  el("focus").innerHTML = `
    <div class="focus-path">${Hud.escape(node.path)}</div>
    <div class="row"><span class="name">Type</span><span class="val">${node.type}</span></div>
    <div class="row"><span class="name">Size</span><span class="val">${node.type === "file" ? Hud.bytes(node.size) : "—"}</span></div>
    <div class="row"><span class="name">Depth</span><span class="val">${node.depth}</span></div>
    ${
      recent.length
        ? `<div class="hint">Recent: ${recent
            .map((t) => `${Hud.escape(t.action)} ${Hud.ago(t.at)}`)
            .join(", ")}</div>`
        : '<div class="hint">Not touched in this session.</div>'
    }
  `;
}

/* -- helpers ---------------------------------------------------------------- */

function actionRgb(action) {
  if (action === "write") return [52, 211, 153];
  if (action === "delete") return [244, 63, 94];
  return [34, 211, 238];
}

const rgba = ([r, g, b], alpha) => `rgba(${r},${g},${b},${alpha.toFixed(3)})`;

function toneFor(action) {
  if (action === "write") return "ok";
  if (action === "delete") return "bad";
  return "";
}

function hash(text) {
  let value = 2166136261;
  for (let i = 0; i < text.length; i++) {
    value ^= text.charCodeAt(i);
    value = Math.imul(value, 16777619);
  }
  return Math.abs(value);
}

function shorten(path) {
  const parts = String(path || "").replace(/\\/g, "/").split("/");
  return parts.length > 3 ? `…/${parts.slice(-2).join("/")}` : path;
}
