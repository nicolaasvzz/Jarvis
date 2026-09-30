"""Tests for the configuration module."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.config import load_config, load_secrets, resolve_config_file
from jarvis.config.settings import CONFIG_FILE_ENV_VAR


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate every test from the host environment and working directory."""
    for key in list(os.environ):
        if key.startswith(("JARVIS_", "ANTHROPIC_", "GEMINI_", "GOOGLE_", "LLM_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def write_yaml(tmp_path: Path, content: str) -> Path:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(content, encoding="utf-8")
    return config_file


class TestDefaults:
    def test_loads_with_no_file_and_no_env(self) -> None:
        config = load_config()
        assert config.api.host == "127.0.0.1"
        assert config.api.port == 8765
        # Gemini by default: a free API key is all a new install needs.
        assert config.llm.provider == "gemini"
        assert config.llm.model == "gemini-3.8-flash"
        assert config.llm.base_url == "https://generativelanguage.googleapis.com/v1beta"
        assert config.llm.thinking_level == "low"
        assert config.logging.level == "INFO"
        assert config.browser.engine == "chromium"
        assert "delete_files" in config.security.require_confirmation


class TestYamlLayer:
    def test_yaml_values_override_defaults(self, tmp_path: Path) -> None:
        config_file = write_yaml(
            tmp_path,
            """
            api:
              port: 9000
            llm:
              max_tokens: 4096
            logging:
              level: DEBUG
            """,
        )
        config = load_config(config_file)
        assert config.api.port == 9000
        assert config.llm.max_tokens == 4096
        assert config.logging.level == "DEBUG"
        # Untouched sections keep their defaults.
        assert config.api.host == "127.0.0.1"

    def test_unknown_key_in_section_is_rejected(self, tmp_path: Path) -> None:
        config_file = write_yaml(
            tmp_path,
            """
            api:
              prot: 9000
            """,
        )
        with pytest.raises(ValidationError):
            load_config(config_file)

    def test_missing_explicit_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nope.yaml")

    def test_file_discovered_in_default_location(self, tmp_path: Path) -> None:
        default_location = tmp_path / "config"
        default_location.mkdir()
        (default_location / "config.yaml").write_text(
            "api:\n  port: 9111\n", encoding="utf-8"
        )
        config = load_config()
        assert config.api.port == 9111

    def test_file_from_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = write_yaml(tmp_path, "api:\n  port: 9222\n")
        monkeypatch.setenv(CONFIG_FILE_ENV_VAR, str(config_file))
        assert resolve_config_file() == config_file
        assert load_config().api.port == 9222


class TestShippedExample:
    """The example file is what everyone copies — it must actually load."""

    def test_config_example_yaml_loads_verbatim(self, tmp_path: Path) -> None:
        example = Path(__file__).resolve().parent.parent / "config" / "config.example.yaml"
        copied = tmp_path / "config.yaml"
        copied.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        config = load_config(copied)
        assert config.llm.provider == "gemini"
        assert config.llm.model == "gemini-3.8-flash"

    def test_a_section_with_only_comments_falls_back_to_defaults(
        self, tmp_path: Path
    ) -> None:
        """YAML parses a bare heading as None; that must mean "defaults"."""
        config_file = write_yaml(
            tmp_path,
            """
            files:
              # root: C:/somewhere
            browser:
            api:
              port: 9300
            """,
        )
        config = load_config(config_file)
        assert config.files.root is None
        assert config.browser.engine == "chromium"
        assert config.api.port == 9300


class TestProviderSelection:
    """The short LLM_* names, which is how the provider is usually set."""

    def test_short_env_vars_select_the_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5-5")
        config = load_config()
        assert config.llm.provider == "anthropic"
        assert config.llm.model == "claude-sonnet-5-5"

    def test_short_env_vars_are_read_from_dotenv(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text(
            "# a comment\nLLM_PROVIDER=gemini\nLLM_MODEL='gemini-3.5-flash-lite'\n",
            encoding="utf-8",
        )
        config = load_config()
        assert config.llm.provider == "gemini"
        assert config.llm.model == "gemini-3.5-flash-lite"

    def test_short_env_vars_override_yaml(self, tmp_path: Path, monkeypatch) -> None:
        config_file = write_yaml(tmp_path, "llm:\n  provider: anthropic\n")
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        assert load_config(config_file).llm.provider == "gemini"

    def test_prefixed_env_var_wins_over_the_short_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_MODEL", "gemini-3.5-flash")
        monkeypatch.setenv("JARVIS_LLM__MODEL", "gemini-3.8-flash")
        assert load_config().llm.model == "gemini-3.8-flash"

    def test_model_defaults_to_the_providers_own_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        assert load_config().llm.model == "claude-opus-4-8"

    def test_unknown_provider_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        with pytest.raises(ValidationError):
            load_config()


class TestEnvLayer:
    def test_env_overrides_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = write_yaml(tmp_path, "api:\n  port: 9000\n")
        monkeypatch.setenv("JARVIS_API__PORT", "9100")
        config = load_config(config_file)
        assert config.api.port == 9100

    def test_env_overrides_defaults_without_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("JARVIS_LOGGING__LEVEL", "WARNING")
        config = load_config()
        assert config.logging.level == "WARNING"


class TestSecrets:
    def test_secrets_read_from_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        secrets = load_secrets(env_file=None)
        assert secrets.anthropic_api_key is not None
        assert secrets.anthropic_api_key.get_secret_value() == "test-key-123"

    def test_secret_values_never_appear_in_repr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        secrets = load_secrets(env_file=None)
        assert "test-key-123" not in repr(secrets)
        assert "test-key-123" not in str(secrets)

    def test_missing_secrets_default_to_none(self) -> None:
        secrets = load_secrets(env_file=None)
        assert secrets.anthropic_api_key is None
        assert secrets.jarvis_api_token is None

    def test_require_raises_helpful_error_when_unset(self) -> None:
        secrets = load_secrets(env_file=None)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            secrets.require("anthropic_api_key")

    def test_secrets_read_from_dotenv_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("JARVIS_API_TOKEN=dotenv-token\n", encoding="utf-8")
        secrets = load_secrets(env_file=env_file)
        assert secrets.jarvis_api_token is not None
        assert secrets.jarvis_api_token.get_secret_value() == "dotenv-token"
