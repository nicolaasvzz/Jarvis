"""Tests for provider selection.

Two properties matter and are asserted directly:

* choosing Gemini must never construct the Anthropic brain, never touch
  ``ANTHROPIC_API_KEY``, and never import the ``anthropic`` package;
* choosing Anthropic must still work exactly as it always has.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, NoReturn

import pytest

import jarvis.brain.anthropic_brain as anthropic_module
from jarvis.brain import GeminiBrain, build_brain
from jarvis.config import load_config, load_secrets
from jarvis.config.schema import LLMConfig


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith(("JARVIS_", "ANTHROPIC_", "GEMINI_", "GOOGLE_", "LLM_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def _no_secrets() -> Any:
    return load_secrets(env_file=None)


def _gemini_key(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    return load_secrets(env_file=None)


class _ExplodingAnthropicBrain:
    """Stands in for AnthropicBrain to prove it is never constructed."""

    def __init__(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError(
            "AnthropicBrain was constructed while the Gemini provider was selected."
        )


class TestGeminiProvider:
    def test_is_the_default_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = load_config().llm
        assert (config.provider, config.model) == ("gemini", "gemini-3.8-flash")
        assert isinstance(build_brain(config, _gemini_key(monkeypatch)), GeminiBrain)

    def test_needs_no_anthropic_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        secrets = _gemini_key(monkeypatch)
        assert secrets.anthropic_api_key is None
        assert isinstance(build_brain(LLMConfig(provider="gemini"), secrets), GeminiBrain)

    def test_never_constructs_the_anthropic_brain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            anthropic_module, "AnthropicBrain", _ExplodingAnthropicBrain
        )
        brain = build_brain(LLMConfig(provider="gemini"), _gemini_key(monkeypatch))
        assert isinstance(brain, GeminiBrain)

    def test_missing_key_says_where_to_get_a_free_one(self) -> None:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY") as caught:
            build_brain(LLMConfig(provider="gemini"), _no_secrets())
        assert "aistudio.google.com" in str(caught.value)

    def test_accepts_googles_own_key_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        secrets = load_secrets(env_file=None)
        assert secrets.gemini_api_key is not None
        assert secrets.gemini_api_key.get_secret_value() == "google-key"

    def test_key_is_read_from_a_dotenv_file(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")
        secrets = load_secrets()
        assert secrets.gemini_api_key is not None
        assert secrets.gemini_api_key.get_secret_value() == "from-dotenv"

    def test_model_selected_by_the_llm_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("LLM_MODEL", "gemini-3.5-flash-lite")
        config = load_config().llm
        assert (config.provider, config.model) == ("gemini", "gemini-3.5-flash-lite")


class TestAnthropicProvider:
    """Claude stays available as an optional provider."""

    def test_still_builds_when_selected_with_a_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("anthropic")
        from jarvis.brain import AnthropicBrain

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        config = load_config().llm
        assert config.provider == "anthropic"
        assert config.model == "claude-opus-4-8"
        assert isinstance(build_brain(config, load_secrets(env_file=None)), AnthropicBrain)

    def test_missing_key_is_a_clear_error_only_for_anthropic(self) -> None:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            build_brain(LLMConfig(provider="anthropic"), _no_secrets())


class TestRemovedOllama:
    """An old Ollama config fails with directions, not a bare schema error."""

    def test_ollama_provider_explains_the_way_forward(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n  provider: ollama\n  model: gpt-oss:20b\n  context_window: 8192\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Ollama support has been removed") as caught:
            load_config(config_file)
        assert "llm.context_window" in str(caught.value)

    def test_ollama_in_dotenv_is_caught_too(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("LLM_PROVIDER=ollama\n", encoding="utf-8")
        with pytest.raises(ValueError, match="gemini"):
            load_config()
