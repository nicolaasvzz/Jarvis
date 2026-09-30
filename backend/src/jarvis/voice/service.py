"""The voice of Jarvis: what it says, and what it hears.

This is the module the rest of the application talks to. It owns three
decisions that neither engine should make for itself:

* **whether** to speak — only the event types listed in config, so Jarvis
  narrates outcomes rather than muttering through every step;
* **how much** to speak — a spoken paragraph is far longer than a read one,
  so long text is cut at a sentence boundary;
* **whether an utterance was meant for Jarvis** — delegated to the wake-word
  matcher, applied consistently wherever audio arrives.

Synthesis happens here; *playback* happens in the browser. Handing audio to
the page rather than the speakers is what lets the dashboard visualise the
waveform in time with the voice, and means a headless server stays silent
instead of talking to an empty room.
"""

from __future__ import annotations

import re

from jarvis.config.schema import VoiceConfig
from jarvis.core.errors import VoiceError
from jarvis.logging import get_logger
from jarvis.voice.base import SpeechSynthesizer, SpeechTranscriber, SpokenAudio, Transcript
from jarvis.voice.synth import build_synthesizer
from jarvis.voice.transcribe import build_transcriber
from jarvis.voice.wake import match_wake_word

_log = get_logger(__name__)

#: Sentence-ish boundaries, used to trim long text without cutting mid-word.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

#: Markdown decoration that should never be read aloud. Spoken asterisks and
#: backticks are noise; the words between them are the point.
_MARKUP = re.compile(r"[*_`#>]+")

#: Collapses the whitespace left behind by stripping markup and newlines.
_WHITESPACE = re.compile(r"\s+")


class _Unset:
    """Distinguishes "not supplied" from an explicit ``None``.

    ``None`` is a meaningful value here — it means "no engine, stay silent" —
    so it cannot double as the marker for "build one from config".
    """


_UNSET = _Unset()


def _usable(engine: object | None) -> bool:
    """Whether an engine exists and its package is actually installed.

    Injected fakes have no ``available`` property and are taken at their
    word; real engines report whether their dependency is present, so the
    dashboard can hide a microphone button that could only ever fail.
    """
    return engine is not None and bool(getattr(engine, "available", True))


class VoiceService:
    """Speech in and out, or a quiet no-op when either is unavailable."""

    def __init__(
        self,
        config: VoiceConfig,
        *,
        synthesizer: SpeechSynthesizer | None | _Unset = _UNSET,
        transcriber: SpeechTranscriber | None | _Unset = _UNSET,
    ) -> None:
        self._config = config
        # Engines may be injected for tests; otherwise they come from config.
        self._synth: SpeechSynthesizer | None = (
            build_synthesizer(config)
            if isinstance(synthesizer, _Unset)
            else synthesizer
        )
        self._transcriber: SpeechTranscriber | None = (
            build_transcriber(config)
            if isinstance(transcriber, _Unset)
            else transcriber
        )

    # -- capability -------------------------------------------------------
    @property
    def config(self) -> VoiceConfig:
        return self._config

    @property
    def can_speak(self) -> bool:
        return _usable(self._synth)

    @property
    def can_listen(self) -> bool:
        return _usable(self._transcriber)

    @property
    def voice_name(self) -> str | None:
        return self._synth.voice if self._synth is not None else None

    def describe(self) -> dict[str, object]:
        """Capability report for the dashboard to configure itself from."""
        return {
            "enabled": self._config.enabled,
            "can_speak": self.can_speak,
            "can_listen": self.can_listen,
            "voice": self.voice_name,
            "provider": self._config.provider,
            "wake_word": self._config.wake_word,
            "wake_word_required": self._config.wake_word_required,
            "listen_enabled": self._config.listen_enabled,
            "speak_events": list(self._config.speak_events),
            "max_utterance_s": self._config.max_utterance_s,
            "min_utterance_ms": self._config.min_utterance_ms,
        }

    # -- speaking ---------------------------------------------------------
    def should_speak(self, event_type: str) -> bool:
        """Whether an event of this type is one Jarvis announces aloud."""
        return (
            self.can_speak
            and self._config.enabled
            and event_type in self._config.speak_events
        )

    def spoken_form(self, text: str) -> str:
        """Trim and clean ``text`` into something worth listening to."""
        cleaned = _WHITESPACE.sub(" ", _MARKUP.sub("", text)).strip()
        limit = self._config.max_spoken_chars
        if len(cleaned) <= limit:
            return cleaned

        # Prefer to stop at the last sentence that fits; fall back to a word
        # boundary so the voice never cuts off mid-syllable.
        kept: list[str] = []
        used = 0
        for sentence in _SENTENCE_END.split(cleaned):
            if used + len(sentence) > limit:
                break
            kept.append(sentence)
            used += len(sentence) + 1
        if kept:
            return " ".join(kept).strip()
        return cleaned[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"

    async def speak(self, text: str) -> SpokenAudio:
        """Render ``text`` as audio. Raises :class:`VoiceError` if it cannot."""
        if self._synth is None:
            raise VoiceError(
                "Speech is switched off. Set voice.enabled: true and "
                "voice.provider to edge or sapi."
            )
        spoken = self.spoken_form(text)
        if not spoken:
            raise VoiceError("There was nothing to say.")
        audio = await self._synth.synthesize(spoken)
        _log.info(
            "spoke", extra={"voice": audio.voice, "chars": len(spoken)}
        )
        return audio

    # -- listening --------------------------------------------------------
    async def listen(self, audio: bytes, *, suffix: str = ".webm") -> Transcript:
        """Transcribe one recorded utterance from the browser."""
        if self._transcriber is None:
            raise VoiceError(
                "Listening is switched off. Set voice.listen_enabled: true and "
                "voice.stt_provider: whisper."
            )
        transcript = await self._transcriber.transcribe(audio, suffix=suffix)
        _log.info(
            "heard",
            extra={
                "addressed": transcript.addressed,
                "chars": len(transcript.text),
            },
        )
        return transcript

    def interpret(self, text: str) -> Transcript:
        """Apply the wake-word rules to text that was typed, not spoken.

        Keeps one definition of "was this addressed to Jarvis" for both
        input paths, so a typed "Jarvis, open my notes" behaves exactly like
        the spoken one.
        """
        match = match_wake_word(
            text,
            self._config.wake_word,
            required=self._config.wake_word_required,
        )
        return Transcript(
            text=text,
            addressed=match.addressed,
            command=match.command,
            details={"wake_score": round(match.score, 3), "source": "text"},
        )
