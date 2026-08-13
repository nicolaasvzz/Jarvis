/*
 * The agent office: the pool as pixel characters, working in rooms.
 *
 * Each agent in the pool is one character. When the pool assigns it a step,
 * the tool decides which room the work belongs to, the character walks there
 * through the corridor, sits at a free desk, and shows a typing indicator
 * while the tool runs. When it finishes it heads back to the lobby.
 *
 * All the art is drawn from code — rectangles, a few palettes, a two-frame
 * walk cycle. That keeps it original and dependency-free, and means agent
 * colour can be derived from the agent's own hue rather than baked into an
 * image, so twenty agents stay tellable apart.
 */

import { Hud } from "./hud.js";
import { World, ROOM_BOXES, TILE, COLS, ROWS, FLOOR, WALL, DESK } from "./office-world.js";

const el = (id) => document.getElementById(id);
const TAU = Math.PI * 2;

const WALK_SPEED = 4.2;      // tiles per second
const SIT_SETTLE_MS = 220;

const canvas = el("office");
const ctx = canvas.getContext("2d");

const world = new World();
const agents = new Map();
let rooms = [];
let roomsById = new Map();
let camera = { x: 0, y: 0, scale: 2 };
let ratio = 1;
let hovered = null;

/* -- boot ------------------------------------------------------------------ */

Hud.start(async () => {
  const snapshot = await Hud.getJSON("/dash/api/snapshot");
  rooms = snapshot.rooms || [];
  roomsById = new Map(rooms.map((room) => [room.id, room]));

  (snapshot.agents || []).forEach(adopt);
  renderRoster();
  renderRooms();

  resize();
  window.addEventListener("resize", resize);
  wireInteraction();
  requestAnimationFrame(draw);

  Hud.onEvent(onFrame);
});

/* -- agents ---------------------------------------------------------------- */

function adopt(record) {
  const home = world.rooms.lobby.idleSpot;
  const existing = agents.get(record.id);
  if (existing) {
    Object.assign(existing, {
      name: record.name,
      hue: record.hue,
      sprite: record.sprite,
      status: record.status,
      tool: record.tool,
      description: record.description,
    });
    routeTo(existing, record.room || "lobby", record.status === "working");
    return existing;
  }

  // Spread starting positions so the pool does not begin stacked on one tile.
  const slot = agents.size;
  const agent = {
    id: record.id,
    name: record.name,
    hue: record.hue,
    sprite: record.sprite,
    index: record.index ?? slot,
    status: record.status || "idle",
    tool: record.tool || null,
    description: record.description || null,
    room: record.room || "lobby",
    x: home.x + (slot % 4) - 1.5,
    y: home.y - Math.floor(slot / 4),
    facing: "down",
    path: [],
    state: "idle",
    seat: null,
    sitAt: 0,
    bob: Math.random() * TAU,
  };
  agents.set(agent.id, agent);
  routeTo(agent, agent.room, agent.status === "working");
  return agent;
}

/* Send an agent to a room, either to a desk (working) or to stand (idle). */
function routeTo(agent, roomId, working) {
  const room = world.rooms[roomId] || world.rooms.lobby;
  releaseSeat(agent);

  let target;
  if (working) {
    const desk = claimDesk(room, agent);
    agent.seat = desk ? { room: roomId, desk } : null;
    target = desk ? desk.seat : room.idleSpot;
  } else {
    // Idle agents fan out around the room so they do not overlap.
    const spread = agent.index % 5;
    target = {
      x: room.idleSpot.x + spread - 2,
      y: room.idleSpot.y - (agent.index % 2),
    };
  }

  agent.room = roomId;
  agent.state = "walking";
  agent.path = world.path(
    { x: Math.round(agent.x), y: Math.round(agent.y) },
    target
  );
  agent.goal = target;
  if (!agent.path.length) {
    // Already there (or nowhere to go): settle immediately.
    arrive(agent);
  }
}

function claimDesk(room, agent) {
  const taken = new Set();
  for (const other of agents.values()) {
    if (other !== agent && other.seat && other.seat.room === room.id) {
      taken.add(`${other.seat.desk.x},${other.seat.desk.y}`);
    }
  }
  return room.desks.find((desk) => !taken.has(`${desk.x},${desk.y}`)) || null;
}

function releaseSeat(agent) {
  agent.seat = null;
}

function arrive(agent) {
  agent.state = agent.seat ? "sitting" : "idle";
  agent.sitAt = performance.now();
  if (agent.goal) {
    agent.x = agent.goal.x;
    agent.y = agent.goal.y;
  }
  agent.facing = agent.seat ? "up" : "down";
}

/* -- live events ------------------------------------------------------------ */

function onFrame(frame) {
  if (!frame.type.startsWith("agent.") && !frame.type.startsWith("step.")) {
    if (frame.type.startsWith("task.")) renderActivity(frame);
    return;
  }

  const record = frame.data && frame.data.agent;
  if (record) {
    const agent = adopt({ ...record, room: frame.room || record.room });
    agent.status = record.status;
    agent.tool = record.tool;
    agent.description = record.description;
    routeTo(agent, frame.room || "lobby", record.status === "working");
  } else if (frame.agent_id && frame.room) {
    const agent = agents.get(frame.agent_id);
    if (agent) {
      agent.tool = frame.tool || agent.tool;
      routeTo(agent, frame.room, true);
    }
  }

  renderRoster();
  renderRooms();
  renderActivity(frame);
}

/* -- simulation ------------------------------------------------------------- */

function step(dt) {
  for (const agent of agents.values()) {
    if (agent.state !== "walking" || !agent.path.length) continue;
    const next = agent.path[0];
    const dx = next.x - agent.x;
    const dy = next.y - agent.y;
    const distance = Math.hypot(dx, dy);
    const travel = WALK_SPEED * dt;

    if (distance <= travel) {
      agent.x = next.x;
      agent.y = next.y;
      agent.path.shift();
      if (!agent.path.length) arrive(agent);
      continue;
    }

    agent.x += (dx / distance) * travel;
    agent.y += (dy / distance) * travel;
    agent.facing =
      Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up";
  }
}

/* -- rendering -------------------------------------------------------------- */

function resize() {
  ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(canvas.clientWidth * ratio);
  canvas.height = Math.floor(canvas.clientHeight * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.imageSmoothingEnabled = false;
  fit();
}

function fit() {
  const scaleX = canvas.clientWidth / (COLS * TILE);
  const scaleY = canvas.clientHeight / (ROWS * TILE);
  camera.scale = Math.max(0.6, Math.min(scaleX, scaleY) * 0.94);
  camera.x = (canvas.clientWidth - COLS * TILE * camera.scale) / 2;
  camera.y = (canvas.clientHeight - ROWS * TILE * camera.scale) / 2;
}

let lastFrame = performance.now();

function draw(now) {
  requestAnimationFrame(draw);
  // Clamped at both ends: the ceiling stops agents teleporting across the
  // office when a backgrounded tab resumes, the floor stops a backwards
  // timestamp walking them in reverse.
  const dt = Math.min(0.05, Math.max(0, (now - lastFrame) / 1000));
  lastFrame = now;
  step(dt);

  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  ctx.save();
  ctx.translate(camera.x, camera.y);
  ctx.scale(camera.scale, camera.scale);

  drawFloor(now);
  drawFurniture(now);

  // Painter's algorithm: lower on screen draws later, so characters overlap
  // furniture and each other correctly.
  const ordered = [...agents.values()].sort((a, b) => a.y - b.y);
  for (const agent of ordered) drawAgent(agent, now);

  drawRoomLabels();
  ctx.restore();
}

function drawFloor(now) {
  for (let y = 0; y < ROWS; y++) {
    for (let x = 0; x < COLS; x++) {
      const kind = world.at(x, y);
      const roomId = world.roomAt(x, y);
      const px = x * TILE;
      const py = y * TILE;

      if (kind === WALL) {
        const inRoom = Boolean(roomId);
        ctx.fillStyle = inRoom ? "#132029" : "#0b141b";
        ctx.fillRect(px, py, TILE, TILE);
        // A lighter cap on the top edge reads as a wall rather than a hole.
        if (world.at(x, y + 1) === FLOOR) {
          ctx.fillStyle = "#1d3340";
          ctx.fillRect(px, py + TILE - 4, TILE, 4);
        }
        continue;
      }

      const room = roomId ? roomsById.get(roomId) : null;
      const hue = room ? room.hue : 200;
      const checker = (x + y) % 2 === 0;
      ctx.fillStyle = room
        ? `hsl(${hue} 26% ${checker ? 11 : 9}%)`
        : `hsl(205 18% ${checker ? 9 : 7.5}%)`;
      ctx.fillRect(px, py, TILE, TILE);
    }
  }

  // Tint the room an agent is actively working in, so the eye goes there.
  for (const agent of agents.values()) {
    if (agent.state !== "sitting") continue;
    const box = ROOM_BOXES[agent.room];
    if (!box) continue;
    const room = roomsById.get(agent.room);
    const glow = 0.05 + 0.025 * Math.sin(now / 480);
    ctx.fillStyle = `hsla(${room ? room.hue : 190} 90% 55% / ${glow})`;
    ctx.fillRect(
      box.x1 * TILE,
      box.y1 * TILE,
      (box.x2 - box.x1 + 1) * TILE,
      (box.y2 - box.y1 + 1) * TILE
    );
  }
}

function drawFurniture(now) {
  for (const [id, room] of Object.entries(world.rooms)) {
    const meta = roomsById.get(id);
    const hue = meta ? meta.hue : 200;

    for (const desk of room.desks) {
      const px = desk.x * TILE;
      const py = desk.y * TILE;
      // Desk top
      ctx.fillStyle = "#3b2a1d";
      ctx.fillRect(px, py + 3, TILE * 2, TILE - 5);
      ctx.fillStyle = "#4d3826";
      ctx.fillRect(px, py + 3, TILE * 2, 3);
      // Monitor, lit if someone is sitting here
      const occupied = [...agents.values()].some(
        (a) => a.seat && a.seat.desk.x === desk.x && a.seat.desk.y === desk.y &&
               a.state === "sitting"
      );
      ctx.fillStyle = "#1b1b22";
      ctx.fillRect(px + 4, py - 4, 10, 8);
      ctx.fillStyle = occupied
        ? `hsl(${hue} 80% ${52 + Math.sin(now / 260) * 8}%)`
        : "#243039";
      ctx.fillRect(px + 5, py - 3, 8, 6);
    }

    // A plant in the corner of every room — small thing, but it makes the
    // rooms read as rooms rather than boxes.
    plant(room.box.x2 - 1, room.box.y1 + 1);
    if (id === "lobby") cooler(room.box.x1 + 1, room.box.y1 + 1);
  }
}

function plant(tx, ty) {
  const px = tx * TILE;
  const py = ty * TILE;
  ctx.fillStyle = "#5a3a22";
  ctx.fillRect(px + 5, py + 9, 6, 5);
  ctx.fillStyle = "#2f7d4f";
  ctx.fillRect(px + 3, py + 3, 10, 6);
  ctx.fillRect(px + 6, py, 4, 4);
}

function cooler(tx, ty) {
  const px = tx * TILE;
  const py = ty * TILE;
  ctx.fillStyle = "#cfe9f2";
  ctx.fillRect(px + 4, py, 8, 6);
  ctx.fillStyle = "#8fa6b1";
  ctx.fillRect(px + 4, py + 6, 8, 9);
}

/* -- characters -------------------------------------------------------------- */

const SKIN = ["#e8b48c", "#c98a5e", "#8d5a37", "#f0c9a5", "#a86f45", "#6f4426"];
const HAIR = ["#2b2118", "#4a2f1c", "#141414", "#6b4a2a", "#3b3b45", "#7a3b22"];

function drawAgent(agent, now) {
  const px = agent.x * TILE;
  const py = agent.y * TILE;
  const walking = agent.state === "walking";
  // Two-frame walk cycle, plus a gentle idle bob so nobody looks frozen.
  const frame = walking ? Math.floor(now / 130) % 2 : 0;
  const bob = walking ? 0 : Math.sin(now / 620 + agent.bob) * 0.6;

  const shirt = `hsl(${agent.hue} 62% 52%)`;
  const shirtDark = `hsl(${agent.hue} 62% 38%)`;
  const skin = SKIN[agent.sprite % SKIN.length];
  const hair = HAIR[agent.sprite % HAIR.length];

  ctx.save();
  ctx.translate(px, py + bob);

  // Shadow grounds the character on the floor.
  ctx.fillStyle = "rgba(0,0,0,0.32)";
  ctx.beginPath();
  ctx.ellipse(8, 15, 5, 2.2, 0, 0, TAU);
  ctx.fill();

  const seated = agent.state === "sitting";
  const legLift = seated ? 0 : frame === 0 ? 0 : 1;

  // Legs
  ctx.fillStyle = "#2a3a48";
  if (!seated) {
    ctx.fillRect(4, 11 + legLift, 3, 4 - legLift);
    ctx.fillRect(9, 11 + (1 - legLift), 3, 4 - (1 - legLift));
  } else {
    ctx.fillRect(4, 12, 8, 3);
  }

  // Torso
  ctx.fillStyle = shirt;
  ctx.fillRect(4, 5, 8, 7);
  ctx.fillStyle = shirtDark;
  ctx.fillRect(4, 10, 8, 2);

  // Arms — forward when seated, at the sides when standing.
  ctx.fillStyle = skin;
  if (seated) {
    ctx.fillRect(3, 8, 2, 3);
    ctx.fillRect(11, 8, 2, 3);
  } else {
    ctx.fillRect(2, 6, 2, 5);
    ctx.fillRect(12, 6, 2, 5);
  }

  // Head
  ctx.fillStyle = skin;
  ctx.fillRect(4, 0, 8, 6);

  // Hair, varying by sprite so the roster is visually distinct.
  ctx.fillStyle = hair;
  const style = agent.sprite % 3;
  ctx.fillRect(4, 0, 8, 2);
  if (style === 0) {
    ctx.fillRect(3, 1, 1, 3);
    ctx.fillRect(12, 1, 1, 3);
  } else if (style === 1) {
    ctx.fillRect(4, 2, 2, 2);
    ctx.fillRect(10, 2, 2, 2);
  } else {
    ctx.fillRect(3, 1, 10, 1);
  }

  // Eyes, only when facing the camera.
  if (agent.facing === "down" || seated === false) {
    if (agent.facing !== "up") {
      ctx.fillStyle = "#1a1a22";
      if (agent.facing === "left") ctx.fillRect(5, 3, 1, 2);
      else if (agent.facing === "right") ctx.fillRect(10, 3, 1, 2);
      else {
        ctx.fillRect(6, 3, 1, 2);
        ctx.fillRect(9, 3, 1, 2);
      }
    }
  }

  ctx.restore();

  if (seated && performance.now() - agent.sitAt > SIT_SETTLE_MS) {
    drawTypingDots(px + 8, py - 8, now, agent.hue);
  }
  drawNameplate(agent, px + 8, py + 20);
}

/* The messenger-style typing indicator: three dots that rise in sequence.
 * This is the signal that an agent is mid-tool-call. */
function drawTypingDots(cx, cy, now, hue) {
  const width = 20;
  const height = 11;
  ctx.fillStyle = "rgba(6, 18, 26, 0.9)";
  roundRect(cx - width / 2, cy - height, width, height, 4);
  ctx.fill();
  ctx.strokeStyle = `hsla(${hue} 70% 60% / 0.55)`;
  ctx.lineWidth = 0.8;
  ctx.stroke();

  for (let i = 0; i < 3; i++) {
    const phase = (now / 260 + i * 0.55) % TAU;
    const lift = Math.max(0, Math.sin(phase)) * 2;
    ctx.fillStyle = `hsl(${hue} 85% ${62 + lift * 8}%)`;
    ctx.beginPath();
    ctx.arc(cx - 5 + i * 5, cy - height / 2 - lift, 1.5, 0, TAU);
    ctx.fill();
  }
}

function drawNameplate(agent, cx, cy) {
  ctx.font = "6px ui-monospace, monospace";
  ctx.textAlign = "center";
  const label = agent.name;
  const width = ctx.measureText(label).width + 6;
  ctx.fillStyle = "rgba(4, 12, 18, 0.78)";
  roundRect(cx - width / 2, cy - 7, width, 9, 2);
  ctx.fill();
  ctx.fillStyle = agent.state === "sitting"
    ? `hsl(${agent.hue} 80% 70%)`
    : "rgba(190, 220, 232, 0.75)";
  ctx.fillText(label, cx, cy);
}

function roundRect(x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
}

function drawRoomLabels() {
  ctx.textAlign = "center";
  for (const [id, box] of Object.entries(ROOM_BOXES)) {
    const meta = roomsById.get(id);
    if (!meta) continue;
    const cx = ((box.x1 + box.x2) / 2) * TILE;
    const cy = box.y1 * TILE + 11;
    ctx.font = "bold 7px ui-monospace, monospace";
    ctx.fillStyle = `hsl(${meta.hue} 65% 62%)`;
    ctx.fillText(meta.name.toUpperCase(), cx, cy);
    ctx.font = "5px ui-monospace, monospace";
    ctx.fillStyle = "rgba(160, 190, 205, 0.5)";
    ctx.fillText(meta.subtitle, cx, cy + 7);
  }
}

/* -- interaction ------------------------------------------------------------- */

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
      camera.x += event.clientX - lastX;
      camera.y += event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      return;
    }
    const tx = (event.offsetX - camera.x) / camera.scale / TILE;
    const ty = (event.offsetY - camera.y) / camera.scale / TILE;
    hovered = [...agents.values()].find(
      (a) => Math.abs(a.x + 0.5 - tx) < 0.9 && Math.abs(a.y + 0.5 - ty) < 1.2
    );
    canvas.style.cursor = hovered ? "pointer" : "grab";
  });

  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      camera.scale = Math.max(
        0.5,
        Math.min(6, camera.scale * Math.exp(-event.deltaY * 0.0016))
      );
    },
    { passive: false }
  );

  canvas.addEventListener("dblclick", fit);
}

/* -- panels ------------------------------------------------------------------ */

function renderRoster() {
  const host = el("roster");
  const list = [...agents.values()].sort((a, b) => a.index - b.index);
  const busy = list.filter((a) => a.status === "working").length;
  el("agent-count").textContent = `${busy}/${list.length}`;

  if (!list.length) {
    host.innerHTML = '<div class="empty">No agents yet.</div>';
    return;
  }
  host.innerHTML = list
    .map((agent) => {
      const meta = roomsById.get(agent.room);
      return `
      <div class="agent-chip ${agent.status === "working" ? "" : "idle"}">
        <span class="swatch" style="background:hsl(${agent.hue} 70% 55%);color:hsl(${agent.hue} 70% 55%)"></span>
        <span class="who">
          ${Hud.escape(agent.name)}
          <span class="doing">${Hud.escape(agent.description || (agent.status === "working" ? agent.tool || "" : "idle"))}</span>
        </span>
        <span class="where">${Hud.escape(meta ? meta.name : agent.room)}</span>
      </div>`;
    })
    .join("");
}

function renderRooms() {
  const host = el("rooms");
  const tally = new Map();
  for (const agent of agents.values()) {
    tally.set(agent.room, (tally.get(agent.room) || 0) + 1);
  }
  host.innerHTML = rooms
    .map(
      (room) => `
      <div class="room-row">
        <span class="pipdot" style="background:hsl(${room.hue} 65% 55%)"></span>
        <span>${Hud.escape(room.name)}</span>
        <span class="tally">${tally.get(room.id) || 0}</span>
      </div>`
    )
    .join("");
}

const activity = [];

function renderActivity(frame) {
  activity.push(frame);
  if (activity.length > 40) activity.shift();
  el("activity").innerHTML = activity
    .slice(-20)
    .reverse()
    .map((item) => {
      const who = item.data && item.data.agent ? item.data.agent.name : null;
      return `
      <div class="feed-item">
        <span class="pip ${Hud.tone(item.type)}"></span>
        <span class="txt">
          <span class="msg">${Hud.escape(item.message)}</span>
          <span class="meta">${Hud.escape([who, item.tool, Hud.ago(item.created_at)].filter(Boolean).join(" · "))}</span>
        </span>
      </div>`;
    })
    .join("");
}
