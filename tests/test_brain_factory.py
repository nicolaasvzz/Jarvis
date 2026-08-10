"""Tests for provider selection.

Two properties matter and are asserted directly, because getting either
wrong turns a local, private setup into a hosted one (or a broken one):

* choosing Ollama must never construct the Anthropic brain, never touch
  ``ANTHROPIC_API_KEY``, and never import the ``anthropic`` package;
* choosing Anthropic must still work exactly as it always has.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, NoReturn

import pytest

import jarvis.brain.anthropic_brain as anthropic_module
from jarvis.brain import OllamaBrain, build_brain
from jarvis.config import load_config, load_secrets
from jarvis.config.schema import LLMConfig


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith(("JARVIS_", "ANTHROPIC_", "LLM_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def _no_secrets() -> Any:
    return load_secrets(env_file=None)


class _ExplodingAnthropicBrain:
    """Stands in for AnthropicBrain to prove it is never constructed."""

    def __init__(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError(
            "AnthropicBrain was constructed while the Ollama provider was selected."
        )


class TestOllamaProvider:
    def test_is_the_default_provider(self) -> None:
        brain = build_brain(load_config().llm, _no_secrets())
        assert isinstance(brain, OllamaBrain)

    def test_needs_no_anthropic_api_key(self) -> None:
        assert "ANTHROPIC_API_KEY" not in os.environ
        secrets = _no_secrets()
        assert secrets.anthropic_api_key is None
        # The point of the test: this must not raise.
        assert isinstance(build_brain(LLMConfig(provider="ollama"), secrets), OllamaBrain)

    def test_never_constructs_the_anthropic_brain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            anthropic_module, "AnthropicBrain", _ExplodingAnthropicBrain
        )
        brain = build_brain(LLMConfig(provider="ollama"), _no_secrets())
        assert isinstance(brain, OllamaBrain)

    def test_selected_by_the_llm_provider_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
        config = load_config().llm
        assert config.provider == "ollama"
        assert config.model == "qwen3:8b"
        assert isinstance(build_brain(config, _no_secrets()), OllamaBrain)

    def test_selected_from_a_dotenv_file(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text(
            "LLM_PROVIDER=ollama\nLLM_MODEL=qwen3:8b\n", encoding="utf-8"
        )
        config = load_config().llm
        assert (config.provider, config.model) == ("ollama", "qwen3:8b")

    def test_yaml_can_still_select_it(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n  provider: ollama\n  model: qwen3:8b\n", encoding="utf-8"
        )
        assert load_config(config_file).llm.provider == "ollama"


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
