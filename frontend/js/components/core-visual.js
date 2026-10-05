/*
 * The particle core: the thing that makes Jarvis look alive.
 *
 * Two layers on one canvas — a drifting field of particles across the whole
 * viewport, and a rotating wireframe sphere at the centre that reacts to what
 * Jarvis is doing: resting cyan, amber while thinking, pulsing in time with
 * its own voice while speaking, red on failure.
 *
 * The performance tricks worth knowing:
 *
 * - The sphere is *rigid*, so which points are neighbours never changes.
 *   Those pairs are computed once at startup rather than every frame, turning
 *   an O(n^2) distance check per frame into a fixed list to draw.
 *
 * - Nothing is drawn one dot at a time with its own colour. Setting a canvas
 *   colour from an "rgba(...)" string makes the browser parse CSS, and doing
 *   that 1,500 times a frame was most of the cost. Instead every dot's
 *   brightness is rounded to one of a few dozen levels (finer than the eye
 *   can tell apart on a dot this size), and each level is drawn in one go
 *   with a numeric globalAlpha.
 *
 * - The glow is a gradient painted once into a small sprite per colour and
 *   stretched into place, rather than built anew every frame.
 *
 * - It draws at most ~80 frames a second: a 144 Hz screen would otherwise
 *   ask for twice the work for motion this slow, and nobody could tell.
 */

const TAU = Math.PI * 2;
const FRAME_MS = 12.5; // skip a display refresh that comes sooner than this
const LEVELS = 128; // brightness steps per unit of alpha
const GLOW_SPRITE = 512; // px; the glow is a smooth gradient, so this stretches well

/* Deterministic, cheap surface wobble. Real noise would be overkill: summed
 * sines of the point's own coordinates give an organic undulation that never
 * repeats visibly, for a handful of flops per point. */
function wobble(x, y, z, t) {
  return (
    Math.sin(x * 3.1 + t * 1.30) * 0.5 +
    Math.sin(y * 2.7 - t * 0.90) * 0.35 +
    Math.sin(z * 3.6 + t * 1.70) * 0.25
  );
}

const RINGS = [
  { radius: 1.52, speed: 0.16, arc: 0.72, width: 1.1, offset: 0 },
  { radius: 1.74, speed: -0.1, arc: 0.28, width: 2.2, offset: 0 },
  { radius: 1.74, speed: -0.1, arc: 0.16, width: 2.2, offset: Math.PI },
  { radius: 2.02, speed: 0.06, arc: 0.44, width: 0.8, offset: 0 },
];

/**
 * Dots drawn in brightness batches. add() a dot with its alpha, then draw()
 * fills every dot of one brightness with one globalAlpha — a counting sort,
 * so it stays linear however many dots there are.
 */
class DotBatch {
  constructor(capacity) {
    this.x = new Float32Array(capacity);
    this.y = new Float32Array(capacity);
    this.size = new Float32Array(capacity);
    this.level = new Uint16Array(capacity);
    this.order = new Uint32Array(capacity);
    this.counts = new Uint32Array(LEVELS + 1);
    this.length = 0;
  }

  clear() {
    this.length = 0;
  }

  add(x, y, size, alpha) {
    const level = Math.round(alpha * LEVELS);
    if (level <= 0) return; // invisible either way
    const i = this.length++;
    this.x[i] = x;
    this.y[i] = y;
    this.size[i] = size;
    this.level[i] = level > LEVELS ? LEVELS : level;
  }

  draw(ctx) {
    const { counts, order, level, length } = this;
    counts.fill(0);
    for (let i = 0; i < length; i++) counts[level[i]]++;
    // counts → the index each level's run starts at.
    let start = 0;
    for (let l = 0; l <= LEVELS; l++) {
      const n = counts[l];
      counts[l] = start;
      start += n;
    }
    for (let i = 0; i < length; i++) order[counts[level[i]]++] = i;

    let i = 0;
    while (i < length) {
      const l = level[order[i]];
      ctx.globalAlpha = l / LEVELS;
      for (; i < length && level[order[i]] === l; i++) {
        const k = order[i];
        ctx.fillRect(this.x[k], this.y[k], this.size[k], this.size[k]);
      }
    }
    ctx.globalAlpha = 1;
  }
}

export class Core {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: true });
    this.accent = options.accent || "#22d3ee";
    this.driftCount = options.particles ?? 900;
    // The element whose classes say "thinking" or "error", which re-tints
    // the HUD around the sphere.
    this.moodRoot = options.root || document.body;

    this.state = "idle";
    this.energy = 0;       // 0..1, eased toward the state's target
    this.amplitude = 0;    // live voice loudness, 0..1
    this.flash = 0;        // brief flare on a notable event

    this.pointCount = options.points ?? 620;
    this.buildSphere();
    this.buildDrift();
    this.dots = new DotBatch(this.pointCount + this.driftCount);
    this.glows = new Map(); // colour → pre-rendered glow sprite

    this.rotation = 0;
    this.tilt = -0.22;
    this.last = performance.now();
    this.wanted = false;   // start() was called and stop() wasn't
    this.running = false;  // a frame is scheduled
    this.frame = 0;        // its requestAnimationFrame id
    this.width = 0;
    this.height = 0;

    this.onResize = this.onResize.bind(this);
    this.tick = this.tick.bind(this);
    window.addEventListener("resize", this.onResize);
    // A hidden tab should not burn battery animating nothing.
    document.addEventListener("visibilitychange", () => this.resume());
  }

  /* -- geometry ---------------------------------------------------------- */

  buildSphere() {
    const n = this.pointCount;
    this.points = new Float32Array(n * 3);
    // Fibonacci lattice: the standard way to spread points evenly over a
    // sphere without the pole crowding of a lat/long grid.
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < n; i++) {
      const y = 1 - (i / (n - 1)) * 2;
      const radius = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * i;
      this.points[i * 3] = Math.cos(theta) * radius;
      this.points[i * 3 + 1] = y;
      this.points[i * 3 + 2] = Math.sin(theta) * radius;
    }

    // Neighbour pairs, computed once.
    //
    // Two subtleties. First the spacing: n points spread over a unit sphere
    // sit about sqrt(4*PI/n) apart, so the threshold has to be derived from
    // that — a fixed guess below the spacing finds almost no pairs and the
    // sphere renders as loose dots instead of a mesh. 1.4x spacing gives
    // each point roughly six neighbours.
    //
    // Second the search: on a Fibonacci lattice consecutive indices are NOT
    // spatially adjacent — each is a further 137.5 degrees around. A point's
    // true neighbours sit at index offsets near Fibonacci numbers
    // (1, 2, 3, 5, 8, 13, 21, 34), so the window has to reach past 34 to
    // find them all.
    const pairs = [];
    const spacing = Math.sqrt((4 * Math.PI) / n);
    const threshold = spacing * 1.4;
    const window = 40;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < Math.min(n, i + window); j++) {
        const dx = this.points[i * 3] - this.points[j * 3];
        const dy = this.points[i * 3 + 1] - this.points[j * 3 + 1];
        const dz = this.points[i * 3 + 2] - this.points[j * 3 + 2];
        if (dx * dx + dy * dy + dz * dz < threshold * threshold) {
          pairs.push(i, j);
        }
      }
    }
    this.edges = new Uint16Array(pairs);
    this.projected = new Float32Array(n * 4); // x, y, scale, depth
  }

  buildDrift() {
    this.drift = [];
    for (let i = 0; i < this.driftCount; i++) this.drift.push(this.newMote(true));
  }

  newMote(anywhere) {
    return {
      x: Math.random(),
      y: anywhere ? Math.random() : 1.06,
      z: 0.25 + Math.random() * 0.75,          // parallax depth
      vx: (Math.random() - 0.5) * 0.00013,
      vy: -(0.00006 + Math.random() * 0.00022),
      size: 0.4 + Math.random() * 1.5,
      phase: Math.random() * TAU,
    };
  }

  /* -- lifecycle --------------------------------------------------------- */

  onResize() {
    // A canvas on a hidden screen measures 0; keep the last real size and
    // measure again when it is shown (start() does).
    if (!this.canvas.clientWidth || !this.canvas.clientHeight) return;
    // Cap the pixel ratio: a 3x retina buffer costs 9x the fill rate for a
    // difference nobody can see on a glow effect.
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    this.width = this.canvas.clientWidth;
    this.height = this.canvas.clientHeight;
    this.canvas.width = Math.floor(this.width * ratio);
    this.canvas.height = Math.floor(this.height * ratio);
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.radius = Math.min(this.width, this.height) * 0.22;
  }

  start() {
    this.wanted = true;
    this.onResize();
    this.resume();
  }

  stop() {
    this.wanted = false;
    this.halt();
  }

  /** Run if wanted and the page is visible; pause while it isn't. */
  resume() {
    if (document.hidden || !this.wanted) {
      this.halt();
      return;
    }
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    this.frame = requestAnimationFrame(this.tick);
  }

  halt() {
    this.running = false;
    cancelAnimationFrame(this.frame);
  }

  /* -- external state ---------------------------------------------------- */

  setState(state) {
    this.state = state;
    this.moodRoot.classList.toggle("is-thinking", state === "thinking");
    this.moodRoot.classList.toggle("is-error", state === "error");
  }

  /** Live voice loudness, 0..1, drives the pulse while speaking. */
  setAmplitude(value) {
    this.amplitude = Math.max(0, Math.min(1, value));
  }

  /** A brief flare — something happened worth noticing. */
  pulse(strength = 1) {
    this.flash = Math.min(1.6, this.flash + strength);
  }

  targetEnergy() {
    if (this.state === "error") return 0.85;
    if (this.state === "speaking") return 0.55 + this.amplitude * 0.45;
    if (this.state === "thinking") return 0.72;
    return 0.16;
  }

  colour() {
    if (this.state === "error") return [244, 63, 94];
    if (this.state === "thinking") return [251, 191, 36];
    return hexToRgb(this.accent);
  }

  /* -- render ------------------------------------------------------------ */

  tick(now) {
    if (!this.running) return;
    this.frame = requestAnimationFrame(this.tick);
    if (now - this.last < FRAME_MS) return; // a high-refresh screen's extra frame
    // Clamped at both ends. The upper bound stops a backgrounded tab from
    // lurching when it resumes; the lower bound matters more than it looks,
    // because a negative delta feeds Math.exp an ever-growing positive
    // exponent, and the resulting Infinity turns every colour into
    // rgba(…, NaN) — which the canvas silently declines to draw. The whole
    // sphere would simply vanish, with nothing in the console to say why.
    const dt = Math.min(64, Math.max(0, now - this.last));
    this.last = now;
    const t = now / 1000;

    // Ease toward the target so state changes glide instead of snapping.
    this.energy += (this.targetEnergy() - this.energy) * (1 - Math.exp(-dt / 260));
    this.flash *= Math.exp(-dt / 340);
    this.rotation += dt * 0.00013 * (0.55 + this.energy * 2.4);

    const ctx = this.ctx;
    const rgb = this.colour();
    ctx.clearRect(0, 0, this.width, this.height);
    ctx.fillStyle = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
    this.dots.clear();
    this.queueDrift(dt, t);
    this.dots.draw(ctx); // the drift sits behind the sphere's wireframe
    this.dots.clear();
    this.drawSphere(t, rgb);
  }

  queueDrift(dt, t) {
    const w = this.width;
    const h = this.height;
    const along = 1 + this.energy * 2;
    const rise = 1 + this.energy * 3;
    const glow = 0.34 * (0.45 + this.energy);
    for (const mote of this.drift) {
      mote.x += mote.vx * dt * along;
      mote.y += mote.vy * dt * rise;
      if (mote.y < -0.06) Object.assign(mote, this.newMote(false));
      if (mote.x < -0.05) mote.x = 1.05;
      if (mote.x > 1.05) mote.x = -0.05;

      // Twinkle, scaled by depth so far motes stay quiet.
      const twinkle = 0.55 + 0.45 * Math.sin(t * 1.7 + mote.phase);
      const size = mote.size * mote.z;
      this.dots.add(mote.x * w, mote.y * h, size, mote.z * glow * twinkle);
    }
  }

  drawSphere(t, rgb) {
    const ctx = this.ctx;
    const [r, g, b] = rgb;
    const cx = this.width / 2;
    const cy = this.height / 2;
    const n = this.pointCount;
    const p = this.projected;

    const breathe = 1 + Math.sin(t * 0.85) * 0.014;
    const surge = this.amplitude * 0.16 + this.flash * 0.07;
    const base = this.radius * (breathe + surge);
    const roughness = 0.045 + this.energy * 0.075;

    const cosR = Math.cos(this.rotation);
    const sinR = Math.sin(this.rotation);
    const cosT = Math.cos(this.tilt);
    const sinT = Math.sin(this.tilt);

    // 1. Transform every point once into the projection buffer.
    for (let i = 0; i < n; i++) {
      const px = this.points[i * 3];
      const py = this.points[i * 3 + 1];
      const pz = this.points[i * 3 + 2];

      const scale = 1 + wobble(px, py, pz, t) * roughness;
      const x = px * scale;
      const y = py * scale;
      const z = pz * scale;

      // Yaw, then pitch.
      const rx = x * cosR - z * sinR;
      const rz = x * sinR + z * cosR;
      const ry = y * cosT - rz * sinT;
      const dz = y * sinT + rz * cosT;

      // Mild perspective: nearer points spread out and brighten.
      const perspective = 1 / (1.85 - dz * 0.55);
      p[i * 4] = cx + rx * base * perspective * 1.85;
      p[i * 4 + 1] = cy + ry * base * perspective * 1.85;
      p[i * 4 + 2] = perspective;
      p[i * 4 + 3] = dz;
    }

    // 2. Edges — the wireframe that gives it structure.
    const edges = this.edges;
    ctx.lineWidth = 0.7;
    ctx.beginPath();
    for (let e = 0; e < edges.length; e += 2) {
      const i = edges[e] * 4;
      const j = edges[e + 1] * 4;
      if (p[i + 3] < -0.15 && p[j + 3] < -0.15) continue; // fully behind: skip
      ctx.moveTo(p[i], p[i + 1]);
      ctx.lineTo(p[j], p[j + 1]);
    }
    ctx.strokeStyle = `rgba(${r},${g},${b},${(0.1 + this.energy * 0.2).toFixed(3)})`;
    ctx.stroke();

    // 3. Points, brighter toward the viewer.
    const grow = 0.85 + this.energy * 1.5;
    for (let i = 0; i < n; i++) {
      const depth = p[i * 4 + 3];
      const size = p[i * 4 + 2] * grow;
      this.dots.add(p[i * 4] - size / 2, p[i * 4 + 1] - size / 2, size,
        ((depth + 1.15) / 2.3) * 0.85);
    }
    this.dots.draw(ctx);

    // 4. The glow at the heart of it: a pre-drawn sprite, faded and sized.
    const glowRadius = base * (1.28 + this.amplitude * 0.3);
    const strength = 0.14 + this.energy * 0.2 + this.flash * 0.18;
    ctx.globalAlpha = Math.min(1, strength);
    ctx.drawImage(this.glowSprite(rgb), cx - glowRadius, cy - glowRadius,
      glowRadius * 2, glowRadius * 2);
    ctx.globalAlpha = 1;

    this.drawRings(cx, cy, base, t, rgb);
  }

  /** The glow gradient at full strength, drawn once per colour. */
  glowSprite([r, g, b]) {
    const key = `${r},${g},${b}`;
    let sprite = this.glows.get(key);
    if (sprite) return sprite;
    sprite = document.createElement("canvas");
    sprite.width = sprite.height = GLOW_SPRITE;
    const c = sprite.getContext("2d");
    const half = GLOW_SPRITE / 2;
    const glow = c.createRadialGradient(half, half, 0, half, half, half);
    glow.addColorStop(0, `rgba(${key},1)`);
    glow.addColorStop(0.55, `rgba(${key},0.28)`);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    c.fillStyle = glow;
    c.beginPath();
    c.arc(half, half, half, 0, TAU);
    c.fill();
    this.glows.set(key, sprite);
    return sprite;
  }

  /* Instrument rings — arcs at fixed radii, each turning at its own rate. */
  drawRings(cx, cy, base, t, [r, g, b]) {
    const ctx = this.ctx;
    ctx.strokeStyle = `rgba(${r},${g},${b},${(0.1 + this.energy * 0.24).toFixed(3)})`;
    for (const ring of RINGS) {
      const start = t * ring.speed + ring.offset;
      ctx.beginPath();
      ctx.arc(cx, cy, base * ring.radius, start, start + ring.arc * TAU);
      ctx.lineWidth = ring.width;
      ctx.stroke();
    }

    // Tick marks around the outermost ring.
    ctx.strokeStyle = `rgba(${r},${g},${b},${(0.08 + this.energy * 0.14).toFixed(3)})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    const inner = base * 2.2;
    for (let i = 0; i < 72; i++) {
      const angle = (i / 72) * TAU - t * 0.03;
      const outer = inner + (i % 6 === 0 ? 9 : 4);
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      ctx.moveTo(cx + cos * inner, cy + sin * inner);
      ctx.lineTo(cx + cos * outer, cy + sin * outer);
    }
    ctx.stroke();
  }
}

const rgbCache = new Map();

function hexToRgb(hex) {
  let rgb = rgbCache.get(hex);
  if (rgb) return rgb;
  const clean = hex.replace("#", "");
  const value = parseInt(
    clean.length === 3
      ? clean
          .split("")
          .map((c) => c + c)
          .join("")
      : clean,
    16
  );
  rgb = [(value >> 16) & 255, (value >> 8) & 255, value & 255];
  rgbCache.set(hex, rgb);
  return rgb;
}
