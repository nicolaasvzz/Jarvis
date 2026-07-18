"""Application settings assembly and loading.

:class:`AppConfig` combines the section models from
:mod:`jarvis.config.schema` and layers three sources on top of the coded
defaults (highest precedence first):

1. init kwargs (used by tests)
2. environment variables — ``JARVIS_`` prefix, ``__`` nesting delimiter
3. a YAML config file

Use :func:`load_config` as the single entry point; it resolves which YAML
file to read (explicit argument → ``JARVIS_CONFIG_FILE`` env var →
``./config/config.yaml`` → the per-user config directory) and returns a
fully validated :class:`AppConfig`.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

from platformdirs import user_config_path
from pydantic import Field
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
    FilesConfig,
    LLMConfig,
    LoggingConfig,
    PathsConfig,
    SecurityConfig,
)

CONFIG_FILE_ENV_VAR = "JARVIS_CONFIG_FILE"

# The YAML file for the *current* load_config() call. A ContextVar (rather
# than a plain module global) keeps concurrent loads isolated, e.g. in
# async code or parallel tests.
_active_yaml_file: ContextVar[Path | None] = ContextVar(
    "jarvis_active_yaml_file", default=None
)


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
