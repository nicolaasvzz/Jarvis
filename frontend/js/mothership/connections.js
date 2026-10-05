/*
 * Connections: which brain Jarvis thinks with, every provider's key and
 * model, voice and listening, and dashboard access. Everything is saved to
 * backend/.env and applied at once — no restart, so open terminals and the
 * conversation carry on. Only host and port wait for a restart, and the
 * page offers one.
 *
 * Keys go one way: typed here, saved there. The backend only ever sends
 * back the last four characters, so a key can be recognised but not read.
 */

import { Hud } from "../lib/hud.js";
import { state, load } from "./state.js";
import { esc, $$, icon, act, showTerminal } from "./ui.js";

const LOGOS = {
  gemini: ["G", 210], anthropic: ["C", 20], openai: ["O", 160],
  groq: ["Gq", 15], openrouter: ["OR", 260], deepseek: ["D", 225],
};
const OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
  "sage", "shimmer", "verse"];
const WHISPER_MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "large-v3"];

const tests = {}; // brain id → {ok, detail}, until the page is left

export function title() {
  return "Connections";
}

export function render(root) {
  const c = state.connections;
  if (!c) {
    root.innerHTML = '<p class="muted pad">Loading…</p>';
    load("connections");
    return;
  }
  const active = c.brain.brains.find((b) => b.id === c.brain.active);
  const restart = Object.entries(c.restart_needed || {});
  root.innerHTML = `
    <div class="page-head">
      <div><div class="kicker">System</div><h1>Connections</h1>
        <p class="lede">Keys and providers, saved to <code>${esc(c.env_file)}</code>. Changes
        apply straight away — no restart, so open terminals and the conversation carry on.</p>
        ${c.overridden.length ? `<p class="lede warn-text">${icon("shield")} Set outside .env (a real
          environment variable wins over it): <code>${esc(c.overridden.join(", "))}</code></p>` : ""}
      </div>
    </div>

    ${restart.length ? `
    <div class="restart-card">
      ${icon("redo")}
      <div><b>${esc(restart.map(([k, v]) => `${k === "host" ? (v === "0.0.0.0" ? "Wi-Fi access on" : "Wi-Fi access off") : `Port ${v}`}`).join(" · "))}</b>
        takes effect after a restart. Restarting closes open terminals.</div>
      <button class="btn primary" data-restart ${c.can_restart ? "" : "disabled"}>${icon("redo")} Restart Jarvis</button>
    </div>` : ""}

    <section class="card">
      <header class="card-head"><div><h2>${icon("bolt")} Brain</h2>
        <span class="muted">Thinking with <b>${esc(active ? active.name : c.brain.active)}</b>
        · ${esc(active ? active.model : "")}. When one runs out, switch here.</span></div></header>
      <div class="conn-grid">${c.brain.brains.map((b) => brainCard(b, c)).join("")}</div>
    </section>

    <section class="card phone-card" id="phone-card">${phoneCard()}</section>

    <div class="cols">
      <section class="card col-main">${voiceForm(c)}</section>
      <section class="card col-side">${accessForm(c)}</section>
    </div>`;

  wireBrains(root, c);
  wireVoice(root, c);
  wireAccess(root, c);
  wirePhone(root);
}

/* -- brains ---------------------------------------------------------------- */

function brainCard(b, c) {
  const [letters, hue] = LOGOS[b.id] || [b.name[0], 190];
  const on = b.id === c.brain.active;
  const tested = tests[b.id];
  const key = b.id === "gemini"
    ? `<div class="slots">${c.brain.gemini_keys
        .map((k) => `<label class="slot">
          <input type="radio" name="gemini-slot" value="${k.slot}" ${k.slot === c.brain.gemini_slot ? "checked" : ""} title="Use this key">
          <span class="slot-name">Key ${k.slot}</span>
          <input type="password" data-env="GEMINI_API_KEY${k.slot === 1 ? "" : `_${k.slot}`}" autocomplete="off"
            placeholder="${k.configured ? `${esc(k.key_hint)} — paste to replace` : "Paste a key (another Google account?)"}">
        </label>`)
        .join("")}</div>`
    : `<label class="field"><span class="label">API key ${b.configured ? `<span class="tag ok-tag">${esc(b.key_hint)}</span>` : ""}</span>
        <input type="password" data-env="${esc(b.key_env)}" autocomplete="off"
          placeholder="${b.configured ? "Paste a new key to replace it" : "Paste your API key"}"></label>`;
  return `
    <article class="conn ${on ? "on" : ""}" data-brain="${esc(b.id)}">
      <header>
        <span class="conn-logo" style="--hue:${hue}">${esc(letters)}</span>
        <span class="conn-name"><b>${esc(b.name)}</b><small>${esc(b.maker)}</small></span>
        ${on ? '<span class="pill ok">In use</span>' : b.configured ? '<span class="pill">Ready</span>' : ""}
      </header>
      <p class="conn-note">${esc(b.note)}${b.configured ? "" : ` <a href="${esc(b.key_url)}" target="_blank" rel="noopener">Get a key ${icon("external")}</a>`}</p>
      ${key}
      <label class="field"><span class="label">Model</span>
        <input type="text" data-env="${esc(b.model_env)}" value="${esc(b.model)}" list="models-${esc(b.id)}" autocomplete="off">
        <datalist id="models-${esc(b.id)}">${b.models.map((m) => `<option value="${esc(m)}">`).join("")}</datalist></label>
      ${b.id === "gemini" ? `<label class="field"><span class="label">Thinking</span>
        <select data-env="GEMINI_THINKING">${["", "minimal", "low", "medium", "high"]
          .map((t) => `<option value="${t}" ${t === c.brain.thinking ? "selected" : ""}>${t || "model default (fastest)"}</option>`)
          .join("")}</select></label>` : ""}
      ${tested ? `<p class="conn-test ${tested.ok ? "ok" : "bad"}">${icon(tested.ok ? "check" : "x")} ${esc(tested.detail)}</p>` : ""}
      <footer>
        <button class="btn small" data-save>${icon("check")} Save</button>
        <button class="btn small ghost" data-test>Test</button>
        <span class="grow"></span>
        ${on ? "" : `<button class="btn small primary" data-use>Use this brain</button>`}
        ${b.configured && b.id !== "gemini" ? `<button class="icon-btn" data-forget title="Remove the key">${icon("trash")}</button>` : ""}
      </footer>
    </article>`;
}

async function save(values, done) {
  const view = await act(Hud.postJSON(Hud.route("connections"), { values }), done);
  if (view) {
    state.connections = view;
    load("system");
  }
  return view;
}

function wireBrains(root, c) {
  $$("[data-brain]", root).forEach((card) => {
    const id = card.dataset.brain;
    const brain = c.brain.brains.find((b) => b.id === id);
    const values = () => {
      const out = {};
      card.querySelectorAll("[data-env]").forEach((input) => {
        const v = input.value.trim();
        if (input.type === "password") {
          if (v) out[input.dataset.env] = v; // blank: keep the saved key
        } else if (input.dataset.env === "GEMINI_THINKING" ? v !== c.brain.thinking : v !== brain.model) {
          out[input.dataset.env] = v;
        }
      });
      const slot = card.querySelector('[name="gemini-slot"]:checked');
      if (slot && Number(slot.value) !== c.brain.gemini_slot) out.GEMINI_KEY = slot.value;
      return out;
    };
    card.querySelector("[data-save]").addEventListener("click", async () => {
      const v = values();
      if (!Object.keys(v).length) return Hud.toast("Nothing changed.", "");
      delete tests[id];
      if (await save(v, `Saved ${brain.name}.`)) render(root);
    });
    const testButton = card.querySelector("[data-test]");
    testButton.addEventListener("click", async () => {
      testButton.disabled = true;
      const v = values();
      if (Object.keys(v).length && !(await save(v))) {
        testButton.disabled = false; // test what's typed
        return;
      }
      const result = await act(Hud.postJSON(Hud.route("connectionsTest"), { brain: id }));
      if (result) tests[id] = result;
      render(root);
    });
    const use = card.querySelector("[data-use]");
    if (use)
      use.addEventListener("click", async () => {
        const v = { ...values(), JARVIS_BRAIN: id };
        const willHaveKey = brain.configured || Object.keys(v).some((k) => k.endsWith("API_KEY"));
        if (!willHaveKey) return Hud.toast(`Paste a ${brain.name} key first.`, "bad");
        if (await save(v, `Jarvis now thinks with ${brain.name}.`)) render(root);
      });
    const forget = card.querySelector("[data-forget]");
    if (forget)
      forget.addEventListener("click", async () => {
        if (!window.confirm(`Remove the saved ${brain.name} key from .env?`)) return;
        if (await save({ [brain.key_env]: null }, "Key removed.")) render(root);
      });
  });
}

/* -- voice and listening ---------------------------------------------------------- */

function select(env, current, options) {
  return `<select data-env="${env}">${options
    .map(([v, l]) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(l)}</option>`)
    .join("")}</select>`;
}

function voiceForm(c) {
  const v = c.voice;
  const l = c.listen;
  const speak = v.voice.toLowerCase() === "off" ? "off" : v.provider;
  return `
    <header class="card-head"><div><h2>${icon("ask")} Voice &amp; listening</h2>
      <span class="muted">Speaking: <b>${esc(v.speaking || "off")}</b> · Listening: <b>${esc(l.listening || "off")}</b></span></div></header>
    <form class="conn-form" id="voice-form">
      <label class="field"><span class="label">Jarvis speaks with</span>${select("SPEAK", speak, [
        ["edge", `Edge voices — free${v.edge_installed ? "" : " (pip install edge-tts)"}`],
        ["openai", `OpenAI voices — uses the OpenAI key${v.openai_configured ? "" : " (none saved yet)"}`],
        ["off", "Silent"]])}</label>
      <label class="field" data-show="edge"><span class="label">Edge voice</span>
        <input type="text" data-env="JARVIS_VOICE" value="${esc(speak === "off" ? "en-GB-RyanNeural" : v.voice)}"></label>
      <label class="field" data-show="openai"><span class="label">OpenAI voice</span>${select("OPENAI_TTS_VOICE", v.openai_voice, OPENAI_VOICES.map((x) => [x, x]))}</label>
      <label class="field" data-show="openai"><span class="label">OpenAI speech model</span>
        <input type="text" data-env="OPENAI_TTS_MODEL" value="${esc(v.openai_model)}"></label>
      <label class="field wide" data-show="openai"><span class="label">How it should sound</span>
        <textarea rows="2" data-env="JARVIS_VOICE_STYLE">${esc(v.style)}</textarea></label>

      <label class="field"><span class="label">Jarvis listens with</span>${select("JARVIS_LISTEN_PROVIDER", l.provider, [
        ["browser", "This browser — free, Chrome/Edge"],
        ["whisper", `Whisper on this PC — free, private${l.whisper_installed ? "" : " (not installed)"}`],
        ["wispr", "Wispr Flow — needs a key"], ["off", "Off — type instead"]])}</label>
      <label class="field"><span class="label">Language</span>
        <input type="text" data-env="JARVIS_LISTEN_LANGUAGE" value="${esc(l.language)}"></label>
      <label class="field" data-show-listen="whisper"><span class="label">Whisper model</span>${select("WHISPER_MODEL", l.whisper_model, WHISPER_MODELS.map((x) => [x, x]))}</label>
      <label class="field" data-show-listen="wispr"><span class="label">Wispr Flow key ${l.wispr_configured ? `<span class="tag ok-tag">${esc(l.wispr_hint)}</span>` : ""}</span>
        <input type="password" data-env="WISPR_API_KEY" autocomplete="off" placeholder="${l.wispr_configured ? "Paste to replace" : "Paste your key"}"></label>
      <footer class="wide"><button class="btn primary" type="submit">${icon("check")} Save voice &amp; listening</button></footer>
    </form>`;
}

function wireVoice(root, c) {
  const form = root.querySelector("#voice-form");
  const sync = () => {
    const speak = form.querySelector('[data-env="SPEAK"]').value;
    const listen = form.querySelector('[data-env="JARVIS_LISTEN_PROVIDER"]').value;
    $$("[data-show]", form).forEach((el) => (el.hidden = el.dataset.show !== speak));
    $$("[data-show-listen]", form).forEach((el) => (el.hidden = el.dataset.showListen !== listen));
  };
  form.addEventListener("change", sync);
  sync();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = {};
    form.querySelectorAll("[data-env]").forEach((input) => {
      const v = input.value.trim();
      if (input.dataset.env === "SPEAK" || input.closest("[hidden]")) return;
      if (input.type === "password" && !v) return; // keep the saved key
      values[input.dataset.env] = v;
    });
    const speak = form.querySelector('[data-env="SPEAK"]').value;
    if (speak === "off") values.JARVIS_VOICE = "off";
    else {
      values.JARVIS_VOICE_PROVIDER = speak;
      if (!values.JARVIS_VOICE) values.JARVIS_VOICE = c.voice.voice.toLowerCase() === "off" ? "en-GB-RyanNeural" : c.voice.voice;
    }
    if (await save(values, "Saved — the next reply uses it.")) render(root);
  });
}

/* -- your phone, from anywhere ------------------------------------------------------------

   Tailscale puts this PC and the phone on one private network, wherever the
   phone is, and `tailscale serve` gives Jarvis an https address on it — which
   is what lets the phone install the dashboard as an app. The card checks
   each step and offers the next; the backend only reports what it finds. The
   links come without the token: this page adds its own when you ask for the
   QR code or the link. */

let phoneQr = false; // shown until the page is left: it carries the token

function phoneCard() {
  const p = state.phone;
  const head = (note) => `
    <header class="card-head"><div><h2>${icon("phone")} Your phone</h2>
      <span class="muted">${note}</span></div>
      <button class="btn small ghost" data-phone-check>${icon("redo")} Check again</button></header>`;
  if (!p) {
    load("phone");
    return head("Checking how a phone can reach Jarvis…");
  }
  const ts = p.tailscale;
  const best = p.links[0];
  const phones = ts.phones || [];
  const step = (done, title, body) => `
    <li class="${done ? "done" : ""}">
      <span class="step-mark">${done ? icon("check") : ""}</span>
      <div><b>${title}</b><div class="muted">${body}</div></div></li>`;
  const pc = ts.signed_in
    ? `Signed in — this PC is <code>${esc(ts.name)}</code>.`
    : !ts.installed
      ? `Free for personal use. <a href="${esc(ts.download)}" target="_blank" rel="noopener">Get Tailscale ${icon("external")}</a>, install it and sign in.`
      : ts.state === "NotRunning"
        ? "Installed, but not running — start Tailscale from the Start menu."
        : "Installed, but signed out — open Tailscale and sign in.";
  const phone = phones.length
    ? `Found ${phones.map((x) => `<b>${esc(x.name)}</b>${x.online ? "" : " (offline)"}`).join(", ")}.`
    : "Install Tailscale from the App Store or Google Play and sign in with the same account.";
  const https = ts.url
    ? `<code>${esc(ts.url)}</code> — only your own devices can open it.
       <button class="btn small ghost" data-phone-share="off">Turn off</button>`
    : `Runs <code>tailscale serve --bg ${esc(String(p.port))}</code> in a terminal. The first time,
       it shows a link to allow https in your Tailscale account — open it, then come back.
       <div class="phone-actions"><button class="btn small primary" data-phone-share="on"
         ${ts.signed_in ? "" : "disabled"}>${icon("bolt")} Turn it on</button></div>`;
  const open = best
    ? `Scan the code with your phone's camera, or send yourself the link. Then
       <b>iPhone:</b> Share → Add to Home Screen. <b>Android:</b> ⋮ → Install app.
       ${best.anywhere ? "" : '<br><span class="warn-text">This link only works on the same Wi-Fi.</span>'}
       ${best.secure ? "" : '<br><span class="warn-text">Plain http: it works, but a phone only installs it as an app, and lets it use the microphone, over https (step 3).</span>'}
       <div class="phone-actions">
         <button class="btn small" data-phone-qr>${phoneQr ? "Hide the code" : "Show QR code"}</button>
         <button class="btn small ghost" data-phone-copy>${icon("copy")} Copy link</button></div>
       <div class="phone-qr" id="phone-qr" ${phoneQr ? "" : "hidden"}></div>
       <small class="hint">The code and link hold your token: anyone who has them can use Jarvis.</small>`
    : "Once the steps above are done, a QR code to scan appears here.";
  return `${head("Use Jarvis from anywhere — it installs on your phone like an app.")}
    <ol class="phone-steps">
      ${step(ts.signed_in, "Tailscale on this PC", pc)}
      ${step(phones.length > 0, "Tailscale on your phone", phone)}
      ${step(Boolean(ts.url), "A private https address for Jarvis", https)}
      ${step(false, "Open it on your phone", open)}
    </ol>
    ${p.links.length > 1 ? `<small class="hint">Other ways in: ${p.links
      .slice(1)
      .map((l) => `<code>${esc(l.url)}</code> (${esc(WAYS[l.via] || l.via)})`)
      .join(" · ")}</small>` : ""}`;
}

const WAYS = {
  tailscale: "anywhere",
  "tailscale-ip": "anywhere, plain http",
  wifi: "same Wi-Fi",
};

/** The phone's link, with this page's token so it opens logged in. */
function phoneLink() {
  const best = state.phone && state.phone.links[0];
  return best ? `${best.url}?token=${encodeURIComponent(Hud.token)}` : "";
}

function wirePhone(root) {
  const card = root.querySelector("#phone-card");
  if (phoneQr) drawQr(card.querySelector("#phone-qr"));
  const on = (selector, fn) => {
    const el = card.querySelector(selector);
    if (el) el.addEventListener("click", fn);
  };
  on("[data-phone-check]", () => {
    state.phone = null;
    render(root);
  });
  card.querySelectorAll("[data-phone-share]").forEach((button) =>
    button.addEventListener("click", async () => {
      const share = button.dataset.phoneShare === "on";
      if (!share && !window.confirm("Turn off Jarvis's https address? Your phone can't reach it until it's back on.")) return;
      const terminal = await act(Hud.postJSON(Hud.route("phoneTailscale"), { share }));
      if (terminal) showTerminal(terminal.id); // it may have a link to show you
    })
  );
  on("[data-phone-qr]", () => {
    phoneQr = !phoneQr;
    render(root);
  });
  on("[data-phone-copy]", async () => {
    const link = phoneLink();
    try {
      await navigator.clipboard.writeText(link);
      Hud.toast("Link copied — it holds your token, so send it only to yourself.", "ok");
    } catch (_) {
      window.prompt("Copy this link:", link); // no clipboard over plain http
    }
  });
}

/** QR codes come from a small library, fetched the first time one is needed. */
let qrLibrary = null;

function drawQr(host) {
  qrLibrary =
    qrLibrary ||
    new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js";
      script.integrity = "sha384-8FWZA6BGMXhsfO+BLtrJK0We6gg5o1JyO8xQm6peWDEUs17ACA5ziE/NIAkl9z2k";
      script.crossOrigin = "anonymous";
      script.onload = () => resolve(window.qrcode);
      script.onerror = () => {
        qrLibrary = null;
        reject(new Error("The QR code library couldn't load — copy the link instead."));
      };
      document.head.appendChild(script);
    });
  qrLibrary
    .then((qrcode) => {
      const qr = qrcode(0, "M");
      qr.addData(phoneLink());
      qr.make();
      host.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 4, scalable: true });
    })
    .catch((err) => {
      host.innerHTML = `<p class="muted">${esc(err.message)}</p>`;
    });
}

/* -- dashboard access ------------------------------------------------------------------- */

function accessForm(c) {
  const a = c.access;
  return `
    <header class="card-head"><div><h2>${icon("shield")} Dashboard access</h2>
      <span class="muted">Who can open this dashboard.</span></div></header>
    <div class="conn-form single">
      <div class="field"><span class="label">Token</span>
        <div class="token-row"><code>${esc(a.token_hint)}</code>
          <button class="btn small" data-rotate>${icon("redo")} Make a new token</button></div>
        <small class="hint">Anyone with the token can use Jarvis. A new one logs out every other
          page and your phone; this page switches over by itself.</small></div>
      <label class="field check"><input type="checkbox" id="phone" ${a.phone ? "checked" : ""}>
        <span>Reachable from other devices on my Wi-Fi</span></label>
      <small class="hint">Not needed for your phone through Tailscale — see <b>Your phone</b>.</small>
      <label class="field"><span class="label">Port</span>
        <input type="text" id="port" value="${esc(String(a.port))}" inputmode="numeric"></label>
      <footer><button class="btn" data-save-access>${icon("check")} Save</button></footer>
      <small class="hint">These two take a restart, offered at the top once saved.</small>
    </div>`;
}

function wireAccess(root, c) {
  root.querySelector("[data-rotate]").addEventListener("click", async () => {
    if (!window.confirm("Make a new dashboard token? Other open pages and your phone will need it.")) return;
    const result = await act(Hud.postJSON(Hud.route("connectionsToken"), {}), "New token saved — this page is using it.");
    if (result && result.token) {
      Hud.save("token", result.token);
      load("connections");
    }
  });
  root.querySelector("[data-save-access]").addEventListener("click", async () => {
    const host = root.querySelector("#phone").checked ? "0.0.0.0" : "127.0.0.1";
    const port = root.querySelector("#port").value.trim();
    const values = {};
    if (host !== c.access.host) values.JARVIS_HOST = host;
    if (port !== String(c.access.port)) values.JARVIS_PORT = port;
    if (!Object.keys(values).length) return Hud.toast("Nothing changed.", "");
    if (await save(values, "Saved — restart to apply.")) render(root);
  });
  const restart = root.querySelector("[data-restart]");
  if (restart)
    restart.addEventListener("click", async () => {
      if (!window.confirm("Restart Jarvis now? Open terminals close.")) return;
      const port = (c.restart_needed || {}).port || c.access.port;
      if (!(await act(Hud.postJSON(Hud.route("restart"), {})))) return;
      root.innerHTML = `<div class="empty-card big"><h3>Restarting…</h3><p>Back in a few seconds.</p></div>`;
      comeBack(port, c.access.port);
    });
}

/** Wait for Jarvis to answer again — on its new port, if that changed. A
    page opened through Tailscale's https address isn't on Jarvis's port at
    all, so "the same port" means "the same address", not this host + port. */
function comeBack(port, oldPort) {
  const here = new URL(window.location.href);
  const moved = String(port) !== String(oldPort);
  const target = moved ? `${here.protocol}//${here.hostname}:${port}` : here.origin;
  let tries = 0;
  const poll = async () => {
    tries += 1;
    try {
      // Another port is another origin: an opaque request still says "it's up".
      const response = await fetch(`${target}/health`, {
        cache: "no-store",
        mode: moved ? "no-cors" : "cors",
      });
      if (moved || response.ok) {
        // A new port is a new origin, which can't see this one's stored token.
        window.location.href = moved
          ? `${target}${here.pathname}?token=${encodeURIComponent(Hud.token)}#/connections`
          : `${here.pathname}#/connections`;
        if (!moved) window.location.reload();
        return;
      }
    } catch (_) {
      /* still down */
    }
    if (tries < 60) setTimeout(poll, 1000);
    else Hud.toast("Jarvis hasn't come back — check its console window.", "bad");
  };
  setTimeout(poll, 2500);
}
