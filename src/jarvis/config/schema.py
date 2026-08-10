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
from pydantic import BaseModel, ConfigDict, Field, model_validator

_APP_NAME = "jarvis"

# The model each provider uses when ``llm.model`` is left unset, so that
# switching providers is a one-line change and never leaves a model name
# pointing at the wrong service.
DEFAULT_MODELS = {
    "ollama": "qwen3:8b",
    "anthropic": "claude-opus-4-8",
}


class _Section(BaseModel):
    """Base class for config sections: unknown keys are a validation error."""

    model_config = ConfigDict(extra="forbid")


class ApiServerConfig(_Section):
    """Settings for the local API server the phone client talks to."""

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)


class LLMConfig(_Section):
    """Settings for the language model behind the Brain module.

    ``provider`` chooses which implementation of the Brain protocol gets
    built. The default is ``ollama``: a model running on this machine, so
    no API key is needed and no conversation leaves the computer.
    ``anthropic`` stays available as an optional provider — picking it is
    the only thing that requires ``ANTHROPIC_API_KEY``.

    Leaving ``model`` unset selects the right default for the chosen
    provider (see :data:`DEFAULT_MODELS`).
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider: Literal["ollama", "anthropic"] = "ollama"
    model: str = ""
    max_tokens: int = Field(default=16000, gt=0)

    # Anthropic only: how hard the model should think before answering.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"

    # Ollama only ---------------------------------------------------------
    # Where the local Ollama server listens.
    base_url: str = "http://localhost:11434"
    # Local models generate far slower than a hosted API, and the first
    # request also pays for loading the weights into memory.
    timeout: float = Field(default=180.0, gt=0)
    # Ollama's own default context window is small enough that the
    # planner's tool catalogue can overflow it — and an overflowing prompt
    # is silently truncated rather than rejected, so set it explicitly.
    context_window: int = Field(default=8192, gt=0)
    # Left unset, the model's own defaults apply. Lower values make the
    # planner's JSON output more reliable on small local models.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    # Turn a hybrid reasoning model's thinking on or off (qwen3 supports
    # both). Unset means "whatever the model does by default".
    think: bool | None = None

    @model_validator(mode="after")
    def _default_model_for_provider(self) -> LLMConfig:
        if not self.model:
            self.model = DEFAULT_MODELS[self.provider]
        return self


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
