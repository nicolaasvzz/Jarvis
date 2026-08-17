"""Configuration module.

Loads and validates all Jarvis settings from three layers, highest
precedence first:

1. Environment variables (``JARVIS_``-prefixed, ``__`` for nesting,
   e.g. ``JARVIS_API__PORT=9000``)
2. The short provider variables ``LLM_PROVIDER``, ``LLM_MODEL`` and
   ``LLM_BASE_URL``, read from the environment or ``.env``
3. A YAML config file (``config/config.yaml`` by default)
4. Typed defaults defined in :mod:`jarvis.config.schema`

Secrets (API keys, tokens) are handled separately in
:mod:`jarvis.config.secrets` and can ONLY come from environment
variables or a local ``.env`` file — never from the YAML config.
"""

from jarvis.config.schema import (
    AgentConfig,
    ApiServerConfig,
    BrowserConfig,
    FilesConfig,
    LLMConfig,
    LoggingConfig,
    PathsConfig,
    PushConfig,
    SecurityConfig,
    TelegramConfig,
)
from jarvis.config.secrets import Secrets, load_secrets
from jarvis.config.settings import AppConfig, load_config, resolve_config_file

__all__ = [
    "AgentConfig",
    "ApiServerConfig",
    "AppConfig",
    "BrowserConfig",
    "FilesConfig",
    "LLMConfig",
    "LoggingConfig",
    "PathsConfig",
    "PushConfig",
    "Secrets",
    "SecurityConfig",
    "TelegramConfig",
    "load_config",
    "load_secrets",
    "resolve_config_file",
]
