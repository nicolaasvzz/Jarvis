/*
 * The particle core: the thing that makes Jarvis look alive.
 *
 * Two layers on one canvas — a drifting field of particles across the whole
 * viewport, and a rotating wireframe sphere at the centre that reacts to what
 * Jarvis is doing: resting cyan, amber while thinking, pulsing in time with
 * its own voice while speaking, red on failure.
 *
 * The one performance trick worth knowing: the sphere is *rigid*, so which
 * points are neighbours never changes. Those pairs are computed once at
 * startup rather than every frame, turning an O(n^2) distance check per frame
 * into a fixed list to draw. That is the difference between 600 points at 60fps
 * and a slideshow.
 */

const TAU = Math.PI * 2;

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

export class Core {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: true });
    this.accent = options.accent || "#22d3ee";
    this.driftCount = options.particles ?? 900;

    this.state = "idle";
    this.energy = 0;       // 0..1, eased toward the state's target
    this.amplitude = 0;    // live voice loudness, 0..1
    this.flash = 0;        // brief flare on a notable event

    this.pointCount = options.points ?? 620;
    this.buildSphere();
    this.buildDrift();

    this.rotation = 0;
    this.tilt = -0.22;
    this.last = performance.now();
    this.running = false;

    this.onResize = this.onResize.bind(this);
    this.tick = this.tick.bind(this);
    window.addEventListener("resize", this.onResize);
    document.addEventListener("visibilitychange", () => {
      // A hidden tab should not burn battery animating nothing.
      if (document.hidden) this.stop();
      else this.start();
    });
    this.onResize();
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
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    requestAnimationFrame(this.tick);
  }

  stop() {
    this.running = false;
  }

  /* -- external state ---------------------------------------------------- */

  setState(state) {
    this.state = state;
    document.body.classList.toggle("is-thinking", state === "thinking");
    document.body.classList.toggle("is-error", state === "error");
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
    ctx.clearRect(0, 0, this.width, this.height);
    this.drawDrift(dt, t);
    this.drawSphere(t);
    requestAnimationFrame(this.tick);
  }

  drawDrift(dt, t) {
    if (!this.drift.length) return;
    const ctx = this.ctx;
    const [r, g, b] = this.colour();
    ctx.save();
    for (const mote of this.drift) {
      mote.x += mote.vx * dt * (1 + this.energy * 2);
      mote.y += mote.vy * dt * (1 + this.energy * 3);
      if (mote.y < -0.06) Object.assign(mote, this.newMote(false));
      if (mote.x < -0.05) mote.x = 1.05;
      if (mote.x > 1.05) mote.x = -0.05;

      // Twinkle, scaled by depth so far motes stay quiet.
      const twinkle = 0.55 + 0.45 * Math.sin(t * 1.7 + mote.phase);
      const alpha = mote.z * 0.34 * twinkle * (0.45 + this.energy);
      ctx.fillStyle = `rgba(${r},${g},${b},${alpha.toFixed(3)})`;
      ctx.fillRect(
        mote.x * this.width,
        mote.y * this.height,
        mote.size * mote.z,
        mote.size * mote.z
      );
    }
    ctx.restore();
  }

  drawSphere(t) {
    const ctx = this.ctx;
    const cx = this.width / 2;
    const cy = this.height / 2;
    const [r, g, b] = this.colour();
    const n = this.pointCount;

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
      let x = px * scale;
      let y = py * scale;
      let z = pz * scale;

      // Yaw, then pitch.
      const rx = x * cosR - z * sinR;
      const rz = x * sinR + z * cosR;
      const ry = y * cosT - rz * sinT;
      const dz = y * sinT + rz * cosT;

      // Mild perspective: nearer points spread out and brighten.
      const perspective = 1 / (1.85 - dz * 0.55);
      this.projected[i * 4] = cx + rx * base * perspective * 1.85;
      this.projected[i * 4 + 1] = cy + ry * base * perspective * 1.85;
      this.projected[i * 4 + 2] = perspective;
      this.projected[i * 4 + 3] = dz;
    }

    // 2. Edges — the wireframe that gives it structure.
    ctx.lineWidth = 0.7;
    ctx.beginPath();
    for (let e = 0; e < this.edges.length; e += 2) {
      const i = this.edges[e];
      const j = this.edges[e + 1];
      const di = this.projected[i * 4 + 3];
      const dj = this.projected[j * 4 + 3];
      if (di < -0.15 && dj < -0.15) continue; // fully behind: skip
      ctx.moveTo(this.projected[i * 4], this.projected[i * 4 + 1]);
      ctx.lineTo(this.projected[j * 4], this.projected[j * 4 + 1]);
    }
    ctx.strokeStyle = `rgba(${r},${g},${b},${(0.1 + this.energy * 0.2).toFixed(3)})`;
    ctx.stroke();

    // 3. Points, brighter toward the viewer.
    for (let i = 0; i < n; i++) {
      const depth = this.projected[i * 4 + 3];
      const alpha = (depth + 1.15) / 2.3;
      const size = this.projected[i * 4 + 2] * (0.85 + this.energy * 1.5);
      ctx.fillStyle = `rgba(${r},${g},${b},${(alpha * 0.85).toFixed(3)})`;
      ctx.fillRect(
        this.projected[i * 4] - size / 2,
        this.projected[i * 4 + 1] - size / 2,
        size,
        size
      );
    }

    // 4. The glow at the heart of it.
    const glowRadius = base * (1.28 + this.amplitude * 0.3);
    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowRadius);
    const strength = 0.14 + this.energy * 0.2 + this.flash * 0.18;
    glow.addColorStop(0, `rgba(${r},${g},${b},${strength.toFixed(3)})`);
    glow.addColorStop(0.55, `rgba(${r},${g},${b},${(strength * 0.28).toFixed(3)})`);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, glowRadius, 0, TAU);
    ctx.fill();

    this.drawRings(cx, cy, base, t, [r, g, b]);
  }

  /* Instrument rings — arcs at fixed radii, each turning at its own rate. */
  drawRings(cx, cy, base, t, [r, g, b]) {
    const ctx = this.ctx;
    const rings = [
      { radius: 1.52, speed: 0.16, arc: 0.72, width: 1.1 },
      { radius: 1.74, speed: -0.1, arc: 0.28, width: 2.2 },
      { radius: 1.74, speed: -0.1, arc: 0.16, width: 2.2, offset: Math.PI },
      { radius: 2.02, speed: 0.06, arc: 0.44, width: 0.8 },
    ];
    for (const ring of rings) {
      const start = t * ring.speed + (ring.offset || 0);
      ctx.beginPath();
      ctx.arc(cx, cy, base * ring.radius, start, start + ring.arc * TAU);
      ctx.strokeStyle = `rgba(${r},${g},${b},${(0.1 + this.energy * 0.24).toFixed(3)})`;
      ctx.lineWidth = ring.width;
      ctx.stroke();
    }

    // Tick marks around the outermost ring.
    ctx.strokeStyle = `rgba(${r},${g},${b},${(0.08 + this.energy * 0.14).toFixed(3)})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < 72; i++) {
      const angle = (i / 72) * TAU - t * 0.03;
      const inner = base * 2.2;
      const outer = inner + (i % 6 === 0 ? 9 : 4);
      ctx.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
      ctx.lineTo(cx + Math.cos(angle) * outer, cy + Math.sin(angle) * outer);
    }
    ctx.stroke();
  }
}

function hexToRgb(hex) {
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
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}
