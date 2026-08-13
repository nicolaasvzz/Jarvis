"""Voice module: a British voice out, your microphone in.

Speech out uses free Microsoft neural voices (``edge-tts``), with the offline
Windows voices as a fallback. Speech in runs ``faster-whisper`` locally, so
recordings never leave the machine, and a wake word decides which utterances
were meant for Jarvis at all.

Everything here is optional and fails soft: without the packages installed
the dashboard simply stays text-only.
"""

from jarvis.voice.base import (
    SpeechSynthesizer,
    SpeechTranscriber,
    SpokenAudio,
    Transcript,
)
from jarvis.voice.service import VoiceService
from jarvis.voice.synth import EdgeSpeech, SapiSpeech, build_synthesizer
from jarvis.voice.transcribe import WhisperTranscriber, build_transcriber
from jarvis.voice.wake import WakeMatch, match_wake_word

__all__ = [
    "EdgeSpeech",
    "SapiSpeech",
    "SpeechSynthesizer",
    "SpeechTranscriber",
    "SpokenAudio",
    "Transcript",
    "VoiceService",
    "WakeMatch",
    "WhisperTranscriber",
    "build_synthesizer",
    "build_transcriber",
    "match_wake_word",
]
