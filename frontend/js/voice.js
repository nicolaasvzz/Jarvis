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

export class Voice {
  constructor({ onTranscript, onStateChange, onAmplitude, onError }) {
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
  }

  /* -- audio context ----------------------------------------------------- */

  async ensureContext() {
    if (!this.context) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.context = new Ctx();
    }
    // Browsers start contexts suspended until a real user gesture.
    if (this.context.state === "suspended") await this.context.resume();
    return this.context;
  }

  /* -- listening --------------------------------------------------------- */

  async startListening() {
    if (this.listening) return;
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

  stopListening() {
    this.listening = false;
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
      form.append("audio", blob, "utterance.webm");
      form.append("submit", "true");
      const response = await fetch(window.JarvisConnection.url("/dash/api/listen"), {
        method: "POST",
        headers: { Authorization: `Bearer ${window.JarvisConnection.token()}` },
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

  async say(text) {
    if (!text || !text.trim()) return;
    let blob;
    try {
      const response = await fetch(window.JarvisConnection.url("/dash/api/speak"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${window.JarvisConnection.token()}`,
        },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) return; // speech is a nicety; never interrupt for it
      blob = await response.blob();
    } catch (_) {
      return;
    }

    const context = await this.ensureContext();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.crossOrigin = "anonymous";

    // Route through an analyser so the core can pulse with the waveform.
    const source = context.createMediaElementSource(audio);
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    analyser.connect(context.destination);
    const buffer = new Float32Array(analyser.fftSize);

    this.speaking = true;
    this.onStateChange("speaking");

    const follow = () => {
      if (!this.speaking) return;
      analyser.getFloatTimeDomainData(buffer);
      let peak = 0;
      for (let i = 0; i < buffer.length; i++) {
        const value = Math.abs(buffer[i]);
        if (value > peak) peak = value;
      }
      this.onAmplitude(Math.min(1, peak * 2.4));
      requestAnimationFrame(follow);
    };

    const finish = () => {
      this.speaking = false;
      this.onAmplitude(0);
      this.onStateChange(this.listening ? "armed" : "off");
      URL.revokeObjectURL(url);
      try {
        source.disconnect();
        analyser.disconnect();
      } catch (_) {
        /* already torn down */
      }
    };

    audio.addEventListener("ended", finish);
    audio.addEventListener("error", finish);
    try {
      await audio.play();
      follow();
    } catch (_) {
      finish(); // autoplay blocked until the user interacts; not fatal
    }
  }
}
