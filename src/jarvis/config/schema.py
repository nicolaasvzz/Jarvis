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
    """Settings for the language model behind the Brain module."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider: Literal["anthropic"] = "anthropic"
    model: str = "claude-opus-4-8"
    max_tokens: int = Field(default=16000, gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"


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
