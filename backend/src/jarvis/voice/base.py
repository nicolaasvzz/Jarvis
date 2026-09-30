"""Interfaces for speech in and speech out.

Two narrow protocols, mirroring how :mod:`jarvis.brain` treats the LLM: the
rest of Jarvis depends on these shapes, never on a particular engine. That
is what lets the neural voice, the offline Windows voice and a silent stub
all be the same thing to the dashboard, and what makes both directions
testable without a microphone, a speaker or a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SpokenAudio:
    """Rendered speech, ready to hand to a browser."""

    audio: bytes
    #: Content type for the HTTP response, e.g. ``audio/mpeg``.
    mime: str
    #: Which voice produced it, for display and debugging.
    voice: str
    text: str


@dataclass(frozen=True)
class Transcript:
    """What Jarvis heard."""

    text: str
    #: Whether the utterance was addressed to Jarvis — either it began with
    #: the wake word, or no wake word is required.
    addressed: bool
    #: The text with any wake word removed: the actual command.
    command: str
    language: str | None = None
    #: Rough model confidence in ``[0, 1]`` where the engine reports one.
    confidence: float | None = None
    details: dict[str, object] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.command.strip()


@runtime_checkable
class SpeechSynthesizer(Protocol):
    """Turns text into audio bytes."""

    @property
    def voice(self) -> str:
        """The voice name this synthesizer speaks with."""
        ...

    async def synthesize(self, text: str) -> SpokenAudio:
        """Render ``text``. Raises :class:`VoiceError` if it cannot."""
        ...


@runtime_checkable
class SpeechTranscriber(Protocol):
    """Turns recorded audio into text."""

    async def transcribe(self, audio: bytes, *, suffix: str = ".webm") -> Transcript:
        """Transcribe one utterance. Raises :class:`VoiceError` if it cannot."""
        ...
