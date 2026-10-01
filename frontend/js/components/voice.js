/*
 * Voice in the browser: listening for the wake word, and speaking back.
 *
 * Listening is always-on but not always-recording. A voice-activity detector
 * watches the microphone's loudness and only sends a clip once someone has
 * actually said something and then stopped. That keeps traffic to the local
 * transcriber down to real utterances instead of a continuous stream.
 *
 * The awkward part is capturing the *start* of a word: by the time loudness
 * crosses the threshold, the first syllable has already happened. MediaRecorder
 * chunks after the first are not independently decodable, so a rolling buffer
 * of chunks cannot simply be reassembled. Instead the recorder runs
 * continuously and is only stopped when an utterance ends — the resulting blob
 * is always a complete, valid file that includes the run-up. To stop that blob
 * growing without bound during silence, the recorder is cycled every few
 * seconds when nothing is being said.
 *
 * Speaking goes through the Web Audio graph rather than a bare <audio> element,
 * so the core's pulse can be driven by the actual waveform.
 */

const SPEECH_START_RMS = 0.045;   // loudness that counts as "someone is talking"
const SPEECH_END_RMS = 0.028;     // lower, so a brief dip does not cut a word
const SILENCE_HOLD_MS = 850;      // quiet for this long ends the utterance
const MIN_SPEECH_MS = 260;        // shorter than this is a cough or a click
const IDLE_CYCLE_MS = 7000;       // recycle the recorder during silence
const MAX_UTTERANCE_MS = 15000;   // hard stop, so nothing records forever

/**
 * Re-encode a recording as 16 kHz mono 16-bit WAV, which is what Wispr Flow
 * takes (the browser itself records webm/opus).
 */
export async function toWav16k(blob) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const decoder = new Ctx();
  let decoded;
  try {
    decoded = await decoder.decodeAudioData(await blob.arrayBuffer());
  } finally {
    decoder.close();
  }
  const rate = 16000;
  const offline = new OfflineAudioContext(
    1, Math.max(1, Math.ceil(decoded.duration * rate)), rate
  );
  const source = offline.createBufferSource();
  source.buffer = decoded; // channels are mixed down to mono by the context
  source.connect(offline.destination);
  source.start();
  const samples = (await offline.startRendering()).getChannelData(0);

  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (at, value) =>
    [...value].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((x, i) => {
    const v = Math.max(-1, Math.min(1, x));
    view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  });
  return new Blob([view], { type: "audio/wav" });
}

export class Voice {
  constructor({ onTranscript, onStateChange, onAmplitude, onError, wav, browser, language, onNotice, onInterim }) {
    this.onNotice = onNotice || (() => {});
    this.onInterim = onInterim || (() => {});
    this.checkAfterMs = 8000; // how long to listen before judging the microphone
    this.wav = Boolean(wav); // send WAV instead of the browser's webm
    this.browser = Boolean(browser); // let the browser do the recognising
    this.language = language || "en-GB";
    this.recognition = null;
    this.onTranscript = onTranscript || (() => {});
    this.onStateChange = onStateChange || (() => {});
    this.onAmplitude = onAmplitude || (() => {});
    this.onError = onError || (() => {});

    this.listening = false;
    this.speaking = false;
    this.context = null;
    this.stream = null;
    this.recorder = null;
    this.chunks = [];
    this.speechStartedAt = 0;
    this.lastLoudAt = 0;
    this.segmentStartedAt = 0;
    this.busy = false;

    // Phones only let a page make sound after a tap — iPhones only from
    // inside the tap itself — and replies are spoken long after it. So the
    // audio is switched on at the first tap or key, ready for later.
    // (A touch counts as a tap when it ends, not when it starts.)
    const unlock = () => this.ensureContext().catch(() => {});
    for (const event of ["touchend", "click", "keydown"]) {
      window.addEventListener(event, unlock, { once: true, capture: true });
    }
  }

  /* -- audio context ----------------------------------------------------- */

  async ensureContext() {
    if (!this.context) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.context = new Ctx();
    }
    // Browsers start contexts suspended until a real user gesture. Outside
    // one an iPhone may never answer, so don't wait on it forever.
    if (this.context.state === "suspended") {
      await Promise.race([
        this.context.resume(),
        new Promise((resolve) => setTimeout(resolve, 1500)),
      ]);
    }
    return this.context;
  }

  /* -- listening --------------------------------------------------------- */

  async startListening() {
    if (this.listening) return;
    if (this.browser) return this.startBrowserListening();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      this.onError(
        err && err.name === "NotAllowedError"
          ? "Microphone access was refused. Allow it in the browser to talk to Jarvis."
          : `Could not open the microphone: ${err.message || err}`
      );
      return;
    }

    const context = await this.ensureContext();
    const source = context.createMediaStreamSource(this.stream);
    this.micAnalyser = context.createAnalyser();
    this.micAnalyser.fftSize = 1024;
    this.micAnalyser.smoothingTimeConstant = 0.6;
    source.connect(this.micAnalyser);
    this.micBuffer = new Float32Array(this.micAnalyser.fftSize);

    this.listening = true;
    this.onStateChange("armed");
    this.startSegment();
    this.monitor();
  }

  /**
   * The browser's own speech recognition: free, no key, no upload of audio to
   * Jarvis. Chrome and Edge ship it; it stops by itself every so often, so it
   * is restarted while listening is on.
   */
  startBrowserListening() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      this.onError(
        "This browser has no speech recognition. Open the dashboard in Chrome or Edge, or type."
      );
      return;
    }
    const recognition = new Recognition();
    recognition.lang = this.language;
    recognition.continuous = true;
    recognition.interimResults = true; // live captions, so you can see it hearing you
    recognition.onspeechstart = () => {
      this.recognised = true;
      if (!this.speaking) this.onStateChange("hearing");
    };
    recognition.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        this.recognised = true;
        // Ignore the room while Jarvis is talking, so it does not hear itself.
        if (this.speaking) continue;
        if (result.isFinal) this.sendText(result[0].transcript);
        else this.onInterim(result[0].transcript);
      }
    };
    recognition.onerror = (event) => {
      if (event.error === "no-speech" || event.error === "aborted") return;
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        this.stopListening();
        this.onError("Microphone access was refused. Allow it in the browser to talk to Jarvis.");
      } else if (event.error === "network") {
        this.stopListening();
        this.onError("Speech recognition needs an internet connection in this browser.");
      } else {
        // audio-capture (no microphone), language-not-supported, ... —
        // never fail silently, and do not restart in a loop.
        this.stopListening();
        this.onError(
          event.error === "audio-capture"
            ? "No microphone was found. Plug one in or pick it in the browser's site settings."
            : `Speech recognition stopped: ${event.error}`
        );
      }
    };
    recognition.onend = () => {
      if (!this.listening) return;
      try {
        recognition.start();
      } catch (_) {
        /* already running */
      }
      if (!this.busy) this.onStateChange("armed");
    };
    this.recognition = recognition;
    this.listening = true;
    try {
      recognition.start();
    } catch (err) {
      this.listening = false;
      this.onError(`Could not start listening: ${err.message || err}`);
      return;
    }
    this.onStateChange("armed");
    this.onNotice("Listening — speak now.");
    this.watchMicrophone();
  }

  /**
   * Browser recognition never says what it is hearing, so open the microphone
   * a second time just to read its level. That drives the core's pulse, and
   * after a few seconds it tells you if the problem is the microphone (silent:
   * wrong input device or muted) or the recogniser (sound but no words).
   */
  async watchMicrophone() {
    this.recognised = false;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (_) {
      return; // recognition may still work; the level is only a diagnostic
    }
    if (!this.listening) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    this.stream = stream;
    // Not awaited: a context that cannot resume (no user gesture yet) must not
    // stop the check from judging the microphone.
    const context = this.context || new (window.AudioContext || window.webkitAudioContext)();
    this.context = context;
    if (context.state === "suspended") context.resume().catch(() => {});
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const buffer = new Float32Array(analyser.fftSize);
    const startedAt = performance.now();
    let loudest = 0;
    let judged = false;
    // A timer, not requestAnimationFrame: frames stop in a background tab,
    // and a microphone that is listening should still be judged.
    const timer = setInterval(() => {
      if (!this.listening || this.stream !== stream) {
        clearInterval(timer);
        return;
      }
      analyser.getFloatTimeDomainData(buffer);
      let peak = 0;
      for (let i = 0; i < buffer.length; i++) peak = Math.max(peak, Math.abs(buffer[i]));
      loudest = Math.max(loudest, peak);
      if (!this.speaking) this.onAmplitude(Math.min(1, peak * 2.4));
      if (!judged && performance.now() - startedAt > this.checkAfterMs) {
        judged = true;
        if (loudest < 0.01) {
          this.onError(
            "The microphone is open but silent. Check which input Chrome is using " +
              "(padlock in the address bar, then Site settings, or the mic icon) and that it isn't muted."
          );
        } else if (!this.recognised) {
          this.onError(
            "I can hear sound, but the browser's speech recogniser returns no words. " +
              "Use Chrome or Edge (not Brave or a built-in browser) and check you're online."
          );
        }
      }
    }, 60);
  }

  async sendText(text) {
    const said = (text || "").trim();
    if (!said) return;
    this.busy = true;
    this.onStateChange("thinking");
    try {
      const response = await fetch(window.HudConnection.endpoint("command"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${window.HudConnection.token()}`,
        },
        body: JSON.stringify({ text: said, submit: true }),
      });
      if (response.ok) this.onTranscript(await response.json());
      else this.onError(`Jarvis could not take that (${response.status}).`);
    } catch (err) {
      this.onError(`Could not send what you said: ${err.message || err}`);
    } finally {
      this.busy = false;
      if (this.listening) this.onStateChange("armed");
    }
  }

  stopListening() {
    this.listening = false;
    if (this.recognition) {
      this.recognition.onend = null;
      try {
        this.recognition.stop();
      } catch (_) {
        /* already stopped */
      }
      this.recognition = null;
    }
    if (this.recorder && this.recorder.state !== "inactive") {
      this.recorder.onstop = null;
      this.recorder.stop();
    }
    this.recorder = null;
    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
    }
    this.onStateChange("off");
    this.onAmplitude(0);
  }

  pickMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
  }

  startSegment() {
    if (!this.stream || !this.listening) return;
    const mimeType = this.pickMimeType();
    this.chunks = [];
    this.recorder = new MediaRecorder(
      this.stream,
      mimeType ? { mimeType } : undefined
    );
    this.recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) this.chunks.push(event.data);
    };
    this.recorder.start();
    this.segmentStartedAt = performance.now();
    this.speechStartedAt = 0;
  }

  /** Stop the current segment; deliver it only if it held real speech. */
  cycleSegment(deliver) {
    if (!this.recorder || this.recorder.state === "inactive") {
      if (this.listening) this.startSegment();
      return;
    }
    const chunks = this.chunks;
    const type = this.recorder.mimeType || "audio/webm";
    this.recorder.onstop = () => {
      if (deliver && chunks.length) {
        this.send(new Blob(chunks, { type }));
      }
      if (this.listening) this.startSegment();
    };
    this.recorder.stop();
  }

  monitor() {
    if (!this.listening) return;
    requestAnimationFrame(() => this.monitor());
    if (!this.micAnalyser) return;

    this.micAnalyser.getFloatTimeDomainData(this.micBuffer);
    let sum = 0;
    for (let i = 0; i < this.micBuffer.length; i++) {
      sum += this.micBuffer[i] * this.micBuffer[i];
    }
    const rms = Math.sqrt(sum / this.micBuffer.length);
    const now = performance.now();

    // Only visualise the microphone when Jarvis is not talking over it.
    if (!this.speaking) this.onAmplitude(Math.min(1, rms * 7));

    // Ignore our own voice coming out of the speakers.
    if (this.speaking || this.busy) {
      this.speechStartedAt = 0;
      return;
    }

    if (rms > SPEECH_START_RMS) {
      if (!this.speechStartedAt) {
        this.speechStartedAt = now;
        this.onStateChange("hearing");
      }
      this.lastLoudAt = now;
      return;
    }

    if (this.speechStartedAt) {
      const speaking = rms > SPEECH_END_RMS;
      if (speaking) this.lastLoudAt = now;
      const quietFor = now - this.lastLoudAt;
      const spokenFor = this.lastLoudAt - this.speechStartedAt;
      const tooLong = now - this.speechStartedAt > MAX_UTTERANCE_MS;

      if (quietFor > SILENCE_HOLD_MS || tooLong) {
        const worthSending = spokenFor >= MIN_SPEECH_MS;
        this.speechStartedAt = 0;
        this.onStateChange("armed");
        this.cycleSegment(worthSending);
      }
      return;
    }

    // Nothing said for a while: restart the segment so the buffered blob
    // does not accumulate minutes of silence.
    if (now - this.segmentStartedAt > IDLE_CYCLE_MS) this.cycleSegment(false);
  }

  /** Force a capture, for the push-to-talk button. */
  async captureNow() {
    if (!this.listening) {
      await this.startListening();
      return;
    }
    if (this.speechStartedAt) {
      this.speechStartedAt = 0;
      this.onStateChange("armed");
      this.cycleSegment(true);
    }
  }

  async send(blob) {
    this.busy = true;
    this.onStateChange("thinking");
    try {
      const form = new FormData();
      if (this.wav) form.append("audio", await toWav16k(blob), "utterance.wav");
      else form.append("audio", blob, "utterance.webm");
      form.append("submit", "true");
      const response = await fetch(window.HudConnection.endpoint("listen"), {
        method: "POST",
        headers: { Authorization: `Bearer ${window.HudConnection.token()}` },
        body: form,
      });
      if (!response.ok) {
        let detail = `${response.status}`;
        try {
          detail = (await response.json()).detail || detail;
        } catch (_) {
          /* status alone will do */
        }
        // A transcriber that is not installed should say so once, not on
        // every utterance.
        if (response.status === 503) {
          this.stopListening();
          this.onError(detail);
        }
        return;
      }
      this.onTranscript(await response.json());
    } catch (err) {
      this.onError(`Could not send the recording: ${err.message || err}`);
    } finally {
      this.busy = false;
      if (this.listening) this.onStateChange("armed");
    }
  }

  /* -- speaking ---------------------------------------------------------- */

  /**
   * Speak `text` sentence by sentence: every sentence is requested at once and
   * played in order, so the first one starts as soon as it is ready instead of
   * after the whole answer has been synthesised. Calls queue up rather than
   * talking over each other.
   */
  say(text) {
    if (!text || !text.trim()) return Promise.resolve();
    this.speechQueue = (this.speechQueue || Promise.resolve()).then(() =>
      this.speakNow(text)
    );
    return this.speechQueue;
  }

  async fetchClip(text) {
    try {
      const response = await fetch(window.HudConnection.endpoint("speak"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${window.HudConnection.token()}`,
        },
        body: JSON.stringify({ text }),
      });
      return response.ok ? await response.blob() : null;
    } catch (_) {
      return null; // speech is a nicety; never interrupt for it
    }
  }

  async speakNow(text) {
    // The first sentence is requested alone so nothing slows it down; the
    // rest follow as soon as it is back, well ahead of when they are needed.
    const [first, ...rest] = splitSpeech(text);
    const head = this.fetchClip(first);
    const tail = head.then(() => rest.map((part) => this.fetchClip(part)));
    this.speaking = true;
    try {
      for (let i = 0; i <= rest.length; i++) {
        const blob = await (i === 0 ? head : (await tail)[i - 1]);
        if (!blob) continue;
        await this.playClip(blob);
      }
    } finally {
      this.speaking = false;
      this.onAmplitude(0);
      this.onStateChange(this.listening ? "armed" : "off");
    }
  }

  async playClip(blob) {
    const context = await this.ensureContext();
    if (context.state !== "running") return; // no tap yet, so no sound: not fatal
    // Played through the audio context rather than an <audio> element: a
    // phone allows an element to play only straight after a tap, but a
    // context, once started, can play whenever the reply comes in.
    let clip;
    try {
      clip = await context.decodeAudioData(await blob.arrayBuffer());
    } catch (_) {
      return; // not audio after all — skip it
    }
    const source = context.createBufferSource();
    source.buffer = clip;

    // Route through an analyser so the core can pulse with the waveform.
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    analyser.connect(context.destination);
    const buffer = new Float32Array(analyser.fftSize);
    let playing = true;

    this.onStateChange("speaking");
    const follow = () => {
      if (!playing) return;
      analyser.getFloatTimeDomainData(buffer);
      let peak = 0;
      for (let i = 0; i < buffer.length; i++) {
        const value = Math.abs(buffer[i]);
        if (value > peak) peak = value;
      }
      this.onAmplitude(Math.min(1, peak * 2.4));
      requestAnimationFrame(follow);
    };

    await new Promise((resolve) => {
      source.onended = resolve;
      source.start();
      follow();
    });
    playing = false;
    try {
      source.disconnect();
      analyser.disconnect();
    } catch (_) {
      /* already torn down */
    }
  }
}

/**
 * Cut text into sentence-sized pieces for speaking. Short sentences are merged
 * into the next one so the voice is not asked for a clip of two words, and the
 * total stays within what the server will speak (1500 characters).
 */
export function splitSpeech(text, minLength = 40) {
  const sentences = text
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 1500)
    .split(/(?<=[.!?…])\s+/);
  const parts = [];
  let carry = "";
  for (const sentence of sentences) {
    carry = carry ? `${carry} ${sentence}` : sentence;
    if (carry.length >= minLength) {
      parts.push(carry);
      carry = "";
    }
  }
  if (carry) {
    if (parts.length) parts[parts.length - 1] += ` ${carry}`;
    else parts.push(carry);
  }
  return parts;
}
