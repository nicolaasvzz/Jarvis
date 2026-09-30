"""Application settings assembly and loading.

:class:`AppConfig` combines the section models from
:mod:`jarvis.config.schema` and layers three sources on top of the coded
defaults (highest precedence first):

1. init kwargs (used by tests)
2. environment variables — ``JARVIS_`` prefix, ``__`` nesting delimiter
3. the short ``LLM_*`` provider variables (see :class:`LLMEnvSource`)
4. a YAML config file

Use :func:`load_config` as the single entry point; it resolves which YAML
file to read (explicit argument → ``JARVIS_CONFIG_FILE`` env var →
``./config/config.yaml`` → the per-user config directory) and returns a
fully validated :class:`AppConfig`.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from platformdirs import user_config_path
from pydantic import Field, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from jarvis.config.schema import (
    AgentConfig,
    ApiServerConfig,
    BrowserConfig,
    DashboardConfig,
    FilesConfig,
    LLMConfig,
    LoggingConfig,
    PathsConfig,
    PushConfig,
    SecurityConfig,
    TelegramConfig,
    VoiceConfig,
)

CONFIG_FILE_ENV_VAR = "JARVIS_CONFIG_FILE"

# The YAML file for the *current* load_config() call. A ContextVar (rather
# than a plain module global) keeps concurrent loads isolated, e.g. in
# async code or parallel tests.
_active_yaml_file: ContextVar[Path | None] = ContextVar(
    "jarvis_active_yaml_file", default=None
)

# Which model answers is the setting people change most often, and it is
# usually changed next to the API keys in .env rather than in the YAML —
# so the short names are accepted alongside the general
# JARVIS_LLM__PROVIDER / JARVIS_LLM__MODEL form.
_LLM_ENV_VARS = {
    "LLM_PROVIDER": "provider",
    "LLM_MODEL": "model",
    "LLM_BASE_URL": "base_url",
}

_DEFAULT_ENV_FILE = Path(".env")


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines out of a .env file.

    Deliberately minimal — it exists only so the short ``LLM_*`` names work
    in the same file as the secrets. A missing or unreadable file simply
    contributes nothing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip().removeprefix("export ").strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


class LLMEnvSource(PydanticBaseSettingsSource):
    """Reads the short ``LLM_*`` provider variables into the ``llm`` section.

    ``LLM_PROVIDER=ollama`` and ``LLM_MODEL=gpt-oss:20b`` are read from the
    process environment or from ``.env``, and merged into the same section
    as ``JARVIS_LLM__*`` — which, sitting higher in the source order, still
    wins if both are set.
    """

    def __init__(
        self, settings_cls: type[BaseSettings], env_file: Path = _DEFAULT_ENV_FILE
    ) -> None:
        super().__init__(settings_cls)
        self._env_file = env_file

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:  # pragma: no cover - __call__ does the work
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        from_file = _read_env_file(self._env_file)
        section = {
            field: value
            for env_name, field in _LLM_ENV_VARS.items()
            if (value := os.environ.get(env_name) or from_file.get(env_name))
        }
        return {"llm": section} if section else {}


class AppConfig(BaseSettings):
    """All non-secret Jarvis settings, grouped into sections."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    api: ApiServerConfig = Field(default_factory=ApiServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    push: PushConfig = Field(default_factory=PushConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)

    @model_validator(mode="before")
    @classmethod
    def _empty_section_means_defaults(cls, data: Any) -> Any:
        """Treat a section with nothing under it as "use the defaults".

        Writing a heading and then only comments beneath it —

            files:
              # root: C:/Users/you/JarvisWorkspace

        — is a natural way to leave a section alone, but YAML parses it as
        ``None``, which would otherwise fail validation with a type error
        that says nothing about the real cause.
        """
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            LLMEnvSource(settings_cls),
            dotenv_settings,
        ]
        yaml_file = _active_yaml_file.get()
        if yaml_file is not None:
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file))
        sources.append(file_secret_settings)
        return tuple(sources)


def resolve_config_file(explicit: str | Path | None = None) -> Path | None:
    """Determine which YAML config file to load, or ``None`` for defaults only.

    A path given explicitly (argument or ``JARVIS_CONFIG_FILE``) must exist;
    the fallback locations are optional.
    """
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        return path

    env_value = os.environ.get(CONFIG_FILE_ENV_VAR)
    if env_value:
        path = Path(env_value)
        if not path.is_file():
            raise FileNotFoundError(
                f"Config file from {CONFIG_FILE_ENV_VAR} not found: {path}"
            )
        return path

    candidates = (
        Path("config") / "config.yaml",
        user_config_path("jarvis", appauthor=False) / "config.yaml",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config(config_file: str | Path | None = None) -> AppConfig:
    """Load, merge, and validate the full application configuration."""
    resolved = resolve_config_file(config_file)
    token = _active_yaml_file.set(resolved)
    try:
        return AppConfig()
    finally:
        _active_yaml_file.reset(token)
