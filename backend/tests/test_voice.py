"""Tests for speech: wake-word matching, spoken trimming, and redaction.

The wake word carries real weight — with an always-open microphone it is the
only thing standing between ambient conversation and a dispatched task — so
it is tested against the words a recogniser genuinely produces, in both
directions.
"""

from __future__ import annotations

import pytest

from jarvis.config.schema import VoiceConfig
from jarvis.core.errors import VoiceError
from jarvis.core.redaction import redact_arguments
from jarvis.voice import VoiceService, match_wake_word
from jarvis.voice.base import SpokenAudio
from jarvis.voice.synth import build_synthesizer
from jarvis.voice.transcribe import _resolve_device, build_transcriber


class FakeSynth:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    @property
    def voice(self) -> str:
        return "en-GB-RyanNeural"

    async def synthesize(self, text: str) -> SpokenAudio:
        self.spoken.append(text)
        return SpokenAudio(audio=b"AUDIO", mime="audio/mpeg", voice=self.voice, text=text)


class TestWakeWord:
    @pytest.mark.parametrize(
        "utterance,command",
        [
            ("Jarvis, open my notes", "open my notes"),
            ("jarvis open my notes", "open my notes"),
            ("Hey Jarvis, what is the time?", "what is the time?"),
            ("OK Jarvis please summarise Q4.txt", "summarise Q4.txt"),
            ("Jarvis can you list the downloads folder", "list the downloads folder"),
            ("JARVIS OPEN THE POD BAY DOORS", "OPEN THE POD BAY DOORS"),
            ("Jarvis' report please", "report please"),
        ],
    )
    def test_addressed_utterances_yield_the_command(
        self, utterance: str, command: str
    ) -> None:
        match = match_wake_word(utterance, "jarvis")
        assert match.addressed is True
        assert match.command == command

    @pytest.mark.parametrize(
        "utterance", ["Travis, read the notes", "Jervis, shut down", "Javis stop"]
    )
    def test_common_mishearings_still_wake_it(self, utterance: str) -> None:
        """Recognisers garble the leading consonant; the tail survives."""
        assert match_wake_word(utterance, "jarvis").addressed is True

    @pytest.mark.parametrize(
        "utterance",
        [
            "Marvin said he would call back",
            "Harris is on line two",
            "harvest festival is next week",
            "java is not my favourite language",
            "the traffic was terrible today",
            "service is down again",
            "",
            "   ",
        ],
    )
    def test_ambient_speech_is_ignored(self, utterance: str) -> None:
        """Names as close as Marvin/Harris must not dispatch a task."""
        assert match_wake_word(utterance, "jarvis").addressed is False

    def test_wake_word_can_be_made_optional(self) -> None:
        match = match_wake_word("open my notes", "jarvis", required=False)
        assert match.addressed is True
        assert match.command == "open my notes"

    def test_the_wake_word_is_stripped_even_when_not_required(self) -> None:
        match = match_wake_word("Jarvis, open my notes", "jarvis", required=False)
        assert match.command == "open my notes"

    def test_a_custom_wake_word_works(self) -> None:
        assert match_wake_word("Friday, dim the lights", "friday").addressed is True
        assert match_wake_word("Jarvis, dim the lights", "friday").addressed is False


class TestVoiceService:
    async def test_speaking_trims_long_text_on_a_sentence_boundary(self) -> None:
        synth = FakeSynth()
        service = VoiceService(
            VoiceConfig(max_spoken_chars=60), synthesizer=synth, transcriber=None
        )
        await service.speak(
            "First sentence here. Second sentence here. "
            "Third one that pushes well past the limit and should be dropped."
        )
        spoken = synth.spoken[0]
        assert spoken == "First sentence here. Second sentence here."

    async def test_markdown_is_not_read_aloud(self) -> None:
        synth = FakeSynth()
        service = VoiceService(VoiceConfig(), synthesizer=synth, transcriber=None)
        await service.speak("**Done** — wrote `notes/todo.txt`\n\nAll good.")
        assert synth.spoken[0] == "Done — wrote notes/todo.txt All good."

    async def test_speaking_without_an_engine_says_why(self) -> None:
        service = VoiceService(VoiceConfig(), synthesizer=None, transcriber=None)
        with pytest.raises(VoiceError, match="switched off"):
            await service.speak("hello")

    async def test_listening_without_an_engine_says_why(self) -> None:
        service = VoiceService(VoiceConfig(), synthesizer=None, transcriber=None)
        with pytest.raises(VoiceError, match="switched off"):
            await service.listen(b"audio")

    def test_should_speak_follows_the_configured_events(self) -> None:
        service = VoiceService(
            VoiceConfig(speak_events=["task.completed"]),
            synthesizer=FakeSynth(),
            transcriber=None,
        )
        assert service.should_speak("task.completed") is True
        assert service.should_speak("step.started") is False

    def test_a_mute_service_never_offers_to_speak(self) -> None:
        service = VoiceService(VoiceConfig(), synthesizer=None, transcriber=None)
        assert service.should_speak("task.completed") is False

    def test_typed_text_obeys_the_same_wake_rules_as_speech(self) -> None:
        service = VoiceService(
            VoiceConfig(wake_word_required=True), synthesizer=None, transcriber=None
        )
        assert service.interpret("Jarvis, do the thing").command == "do the thing"
        assert service.interpret("do the thing").addressed is False

    def test_capabilities_are_reported_honestly(self) -> None:
        service = VoiceService(VoiceConfig(), synthesizer=FakeSynth(), transcriber=None)
        described = service.describe()
        assert described["can_speak"] is True
        assert described["can_listen"] is False
        assert described["voice"] == "en-GB-RyanNeural"


class TestEngineSelection:
    def test_speech_is_off_when_disabled(self) -> None:
        assert build_synthesizer(VoiceConfig(enabled=False)) is None
        assert build_synthesizer(VoiceConfig(provider="none")) is None

    def test_the_configured_provider_is_chosen(self) -> None:
        assert type(build_synthesizer(VoiceConfig(provider="edge"))).__name__ == "EdgeSpeech"
        assert type(build_synthesizer(VoiceConfig(provider="sapi"))).__name__ == "SapiSpeech"

    def test_listening_is_off_when_disabled(self) -> None:
        assert build_transcriber(VoiceConfig(listen_enabled=False)) is None
        assert build_transcriber(VoiceConfig(stt_provider="none")) is None
        assert build_transcriber(VoiceConfig(enabled=False)) is None

    def test_auto_device_picks_a_sane_precision(self) -> None:
        assert _resolve_device("cpu", "auto") == ("cpu", "int8")
        assert _resolve_device("cuda", "auto") == ("cuda", "float16")
        assert _resolve_device("cpu", "float32") == ("cpu", "float32")


class TestRedaction:
    def test_identifying_fields_survive(self) -> None:
        """Paths and URLs are what the dashboard draws; they must come through."""
        shown = redact_arguments({"path": "notes/todo.txt", "url": "https://x.dev"})
        assert shown == {"path": "notes/todo.txt", "url": "https://x.dev"}

    def test_payloads_are_reduced_to_their_size(self) -> None:
        shown = redact_arguments({"path": "a.txt", "content": "x" * 5000})
        assert shown["path"] == "a.txt"
        assert shown["content"] == "<5000 chars>"

    @pytest.mark.parametrize(
        "key", ["password", "api_key", "auth_token", "client_secret", "credential"]
    )
    def test_secrets_never_pass_through(self, key: str) -> None:
        shown = redact_arguments({key: "hunter2"})
        assert "hunter2" not in str(shown)

    def test_long_strings_are_clipped(self) -> None:
        shown = redact_arguments({"pattern": "y" * 500})
        assert len(str(shown["pattern"])) < 200

    def test_nesting_is_bounded(self) -> None:
        deep: dict[str, object] = {"a": {"b": {"c": {"d": {"e": "too far"}}}}}
        assert "too far" not in str(redact_arguments(deep))

    def test_lists_are_truncated_with_a_count(self) -> None:
        shown = redact_arguments({"sources": [f"f{i}.txt" for i in range(20)]})
        assert len(shown["sources"]) == 9  # 8 items plus the "+12 more" marker
        assert "more" in shown["sources"][-1]

    def test_empty_arguments_stay_empty(self) -> None:
        assert redact_arguments(None) == {}
        assert redact_arguments({}) == {}
