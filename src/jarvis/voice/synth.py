"""Speech synthesis: the voice Jarvis answers in.

Two engines, same interface:

* :class:`EdgeSpeech` uses Microsoft's neural voices through ``edge-tts``.
  They are free and need no API key or account, only an outbound HTTPS
  connection. ``en-GB-RyanNeural`` is the crisp British male the dashboard
  defaults to.
* :class:`SapiSpeech` uses the voices already installed in Windows. It is
  offline and instant but audibly synthetic — the fallback for when there is
  no network, not the first choice.

Both are constructed cheaply and import their engine on first use, so a
machine without either dependency still starts, and only discovers the gap
if something actually asks for speech.
"""

from __future__ import annotations

import asyncio
import importlib.util
import tempfile
from pathlib import Path
from typing import Any

from jarvis.config.schema import VoiceConfig
from jarvis.core.errors import VoiceError
from jarvis.logging import get_logger
from jarvis.voice.base import SpokenAudio

_log = get_logger(__name__)


def installed(module: str) -> bool:
    """Whether ``module`` could be imported, without importing it.

    ``find_spec`` only locates the module; it does not execute it. That
    matters because these engines are slow and heavy to import, but the
    dashboard needs to know up front whether the microphone button should
    exist at all — claiming a capability and then failing on first use is a
    worse experience than not offering it.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover - malformed install
        return False


class EdgeSpeech:
    """Microsoft Edge neural voices via ``edge-tts`` — free, no API key."""

    def __init__(self, config: VoiceConfig) -> None:
        self._config = config
        self._communicate: Any | None = None

    @property
    def voice(self) -> str:
        return self._config.voice

    @property
    def available(self) -> bool:
        return installed("edge_tts")

    def _engine(self) -> Any:
        """Import ``edge_tts`` lazily and cache the entry point."""
        if self._communicate is None:
            try:
                from edge_tts import Communicate
            except ImportError as exc:  # pragma: no cover - depends on install
                raise VoiceError(
                    "The neural voice needs the edge-tts package. Install it "
                    'with: pip install "jarvis-assistant[voice]" — or set '
                    "voice.provider: sapi to use the offline Windows voice."
                ) from exc
            self._communicate = Communicate
        return self._communicate

    async def synthesize(self, text: str) -> SpokenAudio:
        communicate_cls = self._engine()
        speech = communicate_cls(
            text,
            self._config.voice,
            rate=self._config.rate,
            pitch=self._config.pitch,
            volume=self._config.volume,
        )
        chunks: list[bytes] = []
        try:
            async for chunk in speech.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])
        except Exception as exc:  # noqa: BLE001 - engine/network errors vary
            raise VoiceError(_friendly_edge_error(exc)) from exc

        if not chunks:
            raise VoiceError(
                f"The voice {self._config.voice!r} returned no audio. Check the "
                "voice name, or switch to voice.provider: sapi."
            )
        return SpokenAudio(
            audio=b"".join(chunks),
            mime="audio/mpeg",
            voice=self._config.voice,
            text=text,
        )


def _friendly_edge_error(exc: Exception) -> str:
    """Translate an edge-tts failure into something actionable."""
    detail = str(exc).lower()
    if "no audio" in detail or "invalid" in detail or "not found" in detail:
        return (
            f"Voice {detail!r} was rejected. Check voice.voice is a real Edge "
            "voice name, e.g. en-GB-RyanNeural."
        )
    if any(marker in detail for marker in ("resolve", "connect", "timeout", "network")):
        return (
            "Could not reach the neural voice service. It needs an internet "
            "connection; set voice.provider: sapi to speak offline instead."
        )
    return f"Speech synthesis failed: {exc}"


class SapiSpeech:
    """The offline Windows voices, via ``pyttsx3``.

    pyttsx3 is synchronous and drives a global engine, so each request runs
    on a worker thread and renders to a temporary WAV file. That is slower
    than streaming, but it keeps the event loop free and avoids the engine's
    habit of deadlocking when driven re-entrantly.
    """

    def __init__(self, config: VoiceConfig) -> None:
        self._config = config

    @property
    def voice(self) -> str:
        return self._config.voice

    @property
    def available(self) -> bool:
        return installed("pyttsx3")

    async def synthesize(self, text: str) -> SpokenAudio:
        audio = await asyncio.to_thread(self._render, text)
        return SpokenAudio(
            audio=audio, mime="audio/wav", voice=self._config.voice, text=text
        )

    def _render(self, text: str) -> bytes:
        try:
            import pyttsx3
        except ImportError as exc:  # pragma: no cover - depends on install
            raise VoiceError(
                "The offline voice needs the pyttsx3 package. Install it with: "
                'pip install "jarvis-assistant[voice-offline]"'
            ) from exc

        engine = pyttsx3.init()
        try:
            self._select_voice(engine)
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "speech.wav"
                engine.save_to_file(text, str(target))
                engine.runAndWait()
                if not target.exists() or target.stat().st_size == 0:
                    raise VoiceError("The offline voice produced no audio.")
                return target.read_bytes()
        finally:
            with _suppressed():
                engine.stop()

    def _select_voice(self, engine: Any) -> None:
        """Pick the closest installed voice to the configured name.

        Windows voice ids are long registry paths, so the configured name is
        matched loosely against the human-readable name — "Hazel", "George"
        and "en-GB" all find the British voices.
        """
        wanted = self._config.voice.lower()
        # An Edge voice name is meaningless to SAPI; fall back to any British
        # voice rather than leaving the default American one.
        hints = [wanted]
        if wanted.startswith("en-gb"):
            hints.extend(["george", "hazel", "susan", "united kingdom", "gb"])
        try:
            voices = engine.getProperty("voices")
        except Exception:  # noqa: BLE001 - driver quirks are not fatal
            return
        for hint in hints:
            for voice in voices or []:
                haystack = f"{getattr(voice, 'name', '')} {getattr(voice, 'id', '')}"
                if hint in haystack.lower():
                    engine.setProperty("voice", voice.id)
                    return
        _log.info("no matching offline voice; using the system default")


class _suppressed:
    """Swallow teardown errors from a C-backed engine."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, *_: object) -> bool:
        return exc_type is not None


def build_synthesizer(config: VoiceConfig) -> EdgeSpeech | SapiSpeech | None:
    """Pick a synthesizer from config, or ``None`` when speech is off."""
    if not config.enabled or config.provider == "none":
        return None
    if config.provider == "sapi":
        return SapiSpeech(config)
    return EdgeSpeech(config)
