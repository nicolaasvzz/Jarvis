"""Typed configuration schema for Jarvis.

Every tunable value in the application lives here so that nothing is
hardcoded at call sites. Each section is a small Pydantic model with
sensible defaults, validated on load.

Sections use ``extra="forbid"`` so a typo in the YAML file (e.g. ``prot``
instead of ``port``) fails loudly at startup instead of being silently
ignored.

Secrets are deliberately NOT part of this schema — see
:mod:`jarvis.config.secrets`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from platformdirs import user_data_path, user_log_path
from pydantic import BaseModel, ConfigDict, Field

_APP_NAME = "jarvis"


class _Section(BaseModel):
    """Base class for config sections: unknown keys are a validation error."""

    model_config = ConfigDict(extra="forbid")


class ApiServerConfig(_Section):
    """Settings for the local API server the phone client talks to."""

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)


class LLMConfig(_Section):
    """Settings for the language model behind the Brain module.

    ``provider: ollama`` runs entirely against a local ``ollama serve``
    instance — no API key, no internet connection, fully offline. Set
    ``model`` to a model you have pulled locally (e.g. ``llama3.1``).
    ``base_url`` and ``timeout_seconds`` only apply to the ``ollama``
    provider; ``effort`` only applies to ``anthropic``.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider: Literal["anthropic", "ollama"] = "anthropic"
    model: str = "claude-opus-4-8"
    max_tokens: int = Field(default=16000, gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    base_url: str = "http://localhost:11434"
    timeout_seconds: float = Field(default=120.0, gt=0)


class LoggingConfig(_Section):
    """Settings for structured logging (see :mod:`jarvis.logging`)."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    directory: Path = Field(
        default_factory=lambda: user_log_path(_APP_NAME, appauthor=False)
    )
    console: bool = True
    file_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    file_backup_count: int = Field(default=5, ge=0)


class PathsConfig(_Section):
    """Filesystem locations Jarvis uses for its own persistent data."""

    data_dir: Path = Field(
        default_factory=lambda: user_data_path(_APP_NAME, appauthor=False)
    )


class SecurityConfig(_Section):
    """Permission policy: which action categories require user confirmation.

    Tool implementations declare which category they belong to; the Tool
    Manager consults this list before executing.
    """

    require_confirmation: list[str] = Field(
        default_factory=lambda: [
            "delete_files",
            "send_email",
            "install_software",
            "modify_system_settings",
            "spend_money",
            "elevated_shell",
        ]
    )


class BrowserConfig(_Section):
    """Settings for Playwright-based browser automation."""

    engine: Literal["chromium", "firefox", "webkit"] = "chromium"
    headless: bool = False
    downloads_dir: Path | None = None


class FilesConfig(_Section):
    """Settings for the sandboxed File Manager.

    ``root`` is the only directory Jarvis may touch. When unset it defaults
    to ``<data_dir>/workspace`` — point it at the folder tree you actually
    want the assistant to manage.
    """

    root: Path | None = None


class AgentConfig(_Section):
    """Settings for the plan/execute/observe loop."""

    max_step_attempts: int = Field(default=2, ge=1)
    max_plan_revisions: int = Field(default=2, ge=0)
    history_messages: int = Field(default=20, ge=0)


class PushConfig(_Section):
    """Generic push notifications via an ntfy-compatible server.

    Push-only (no remote control). Subscribe your phone to ``topic`` in the
    ntfy app; Jarvis POSTs each notable notification to
    ``<server>/<topic>``. Needs only outbound HTTPS, so it works without any
    inbound network access. The auth token, if the server needs one, is the
    ``JARVIS_PUSH_TOKEN`` secret.
    """

    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str | None = None


class TelegramConfig(_Section):
    """Telegram bot bridge: push notifications AND full remote control.

    The bridge long-polls Telegram (outbound HTTPS only) for your commands
    and sends results/notifications back — no inbound connection, no LAN, no
    open ports, so it works over any internet connection (even a phone
    tether). Only ``owner_chat_id`` may issue commands; the bot token is the
    ``TELEGRAM_BOT_TOKEN`` secret.
    """

    enabled: bool = False
    owner_chat_id: int | None = None
    poll_timeout: int = Field(default=30, ge=0, le=120)
    api_base: str = "https://api.telegram.org"
