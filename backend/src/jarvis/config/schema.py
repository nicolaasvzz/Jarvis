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
from typing import Any, Literal

from platformdirs import user_data_path, user_log_path
from pydantic import BaseModel, ConfigDict, Field, model_validator

_APP_NAME = "jarvis"

# The model each provider uses when ``llm.model`` is left unset, so that
# switching providers is a one-line change and never leaves a model name
# pointing at the wrong service.
DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "anthropic": "claude-opus-4-8",
}

# Settings that only ever meant something to the removed Ollama provider.
# Named here so an old config fails with directions instead of a bare
# "extra inputs are not permitted".
_REMOVED_OLLAMA_KEYS = ("context_window", "think", "num_gpu", "keep_alive")


class _Section(BaseModel):
    """Base class for config sections: unknown keys are a validation error."""

    model_config = ConfigDict(extra="forbid")


class ApiServerConfig(_Section):
    """Settings for the local API server the phone client talks to."""

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    # Web pages allowed to call the API from another origin — a copy of the
    # frontend opened on its own, rather than served by this server. Any
    # port on localhost is always allowed; list other origins here, or
    # ["*"] for any. The API token is still required either way: this only
    # decides which pages the browser lets *try*.
    cors_origins: list[str] = Field(default_factory=list)


class LLMConfig(_Section):
    """Settings for the language model behind the Brain module.

    ``provider`` chooses which implementation of the Brain protocol gets
    built. The default is ``gemini``: Google's hosted models, where a free
    API key from https://aistudio.google.com/apikey is enough.
    ``anthropic`` stays available as an optional provider and needs
    ``ANTHROPIC_API_KEY`` instead.

    Leaving ``model`` unset selects the right default for the chosen
    provider (see :data:`DEFAULT_MODELS`).
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider: Literal["gemini", "anthropic"] = "gemini"
    model: str = ""
    max_tokens: int = Field(default=16000, gt=0)

    # Anthropic only: how hard the model should think before answering.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"

    # Gemini only ---------------------------------------------------------
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # Seconds to wait for one model call before giving up on it.
    timeout: float = Field(default=120.0, gt=0)
    # How much the model reasons before answering. Thinking tokens count
    # against the free tier's limits like any other output, so the default
    # is low; null leaves it to the model (Gemini 2.5 models need null).
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = "low"
    # Left unset, the model's own default applies — Google recommends
    # keeping Gemini 3 models at theirs.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    # Retries for a rate-limited (429) or overloaded (5xx) call. The free
    # tier is limited per minute, so a short wait usually gets through.
    max_retries: int = Field(default=3, ge=0, le=10)

    @model_validator(mode="before")
    @classmethod
    def _explain_removed_ollama(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        stale = [key for key in _REMOVED_OLLAMA_KEYS if key in data]
        if data.get("provider") == "ollama" or stale:
            raise ValueError(
                "Ollama support has been removed. Set llm.provider to "
                "'gemini' (free key: https://aistudio.google.com/apikey) or "
                "'anthropic', and delete "
                + (", ".join(f"llm.{key}" for key in stale) or "the Ollama settings")
                + " from config.yaml — also check LLM_PROVIDER / LLM_BASE_URL "
                "in .env."
            )
        return data

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
    """Settings for the plan/execute/observe loop.

    ``pool_size`` is how many agents may work on one plan at the same time.
    Steps only run in parallel when the plan says they are independent (see
    ``PlanStep.depends_on``), so raising this never reorders work that
    genuinely has to happen in sequence — it only widens the plan where the
    planner found separate branches.
    """

    max_step_attempts: int = Field(default=2, ge=1)
    max_plan_revisions: int = Field(default=2, ge=0)
    history_messages: int = Field(default=20, ge=0)
    pool_size: int = Field(default=4, ge=1, le=20)
    parallel: bool = True


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


class DashboardConfig(_Section):
    """The local web dashboard: the heads-up display for a running Jarvis.

    Served by the same API server on the same port, so there is one process,
    one orchestrator, and one live view of it. Everything here is presentation
    tuning; turning it off costs nothing but the UI.
    """

    enabled: bool = True
    #: The frontend folder to serve at /dash/. Unset: the ``frontend/``
    #: folder beside ``backend/`` in this checkout, when there is one.
    #: Without a frontend the API still runs; only the pages are missing.
    web_root: Path | None = None
    #: How many recent events a freshly-opened page is back-filled with, so
    #: the HUD is never blank on load.
    history_limit: int = Field(default=300, ge=10, le=5000)
    #: Depth and node budget for the file constellation. Walking a large
    #: workspace is cheap but drawing 10,000 nodes is not.
    file_tree_depth: int = Field(default=3, ge=1, le=8)
    file_tree_max_nodes: int = Field(default=260, ge=10, le=2000)
    #: Drifting background particles. 0 disables them on slow machines.
    particles: int = Field(default=900, ge=0, le=6000)
    accent: str = "#22d3ee"
    #: Open the browser automatically when the server starts.
    open_browser: bool = False
    #: Poll interval for the system-status panel, in seconds.
    stats_interval: float = Field(default=2.0, ge=0.25, le=60.0)


class VoiceConfig(_Section):
    """Speech: a British voice out, and your voice in.

    Both directions are optional and degrade quietly. If ``edge-tts`` is not
    installed Jarvis falls back to the offline Windows voices; if neither is
    available the dashboard simply shows text. Speech-to-text runs locally
    via faster-whisper — audio never leaves the machine.
    """

    enabled: bool = True
    #: ``edge`` is Microsoft's free neural TTS (needs internet, no API key);
    #: ``sapi`` is the offline Windows voice; ``none`` disables speech out.
    provider: Literal["edge", "sapi", "none"] = "edge"
    #: en-GB-RyanNeural is the crisp British male; en-GB-ThomasNeural is more
    #: clipped, en-GB-SoniaNeural female. Any Edge voice name works.
    voice: str = "en-GB-RyanNeural"
    rate: str = "+8%"
    pitch: str = "-2Hz"
    volume: str = "+0%"
    #: Which event types Jarvis reads aloud unprompted.
    speak_events: list[str] = Field(
        default_factory=lambda: [
            "task.completed",
            "task.failed",
            "approval.required",
        ]
    )
    #: Longest utterance Jarvis will speak in one go; longer text is trimmed
    #: on a sentence boundary so it never monologues for a minute.
    max_spoken_chars: int = Field(default=420, ge=40, le=4000)

    # -- listening ---------------------------------------------------------
    listen_enabled: bool = True
    #: Say this word to address Jarvis. Matched case-insensitively against the
    #: start of a transcript, tolerating common mishearings.
    wake_word: str = "jarvis"
    #: With a wake word required, ambient speech is ignored unless it starts
    #: with the wake word. Turn off to treat every utterance as a command.
    wake_word_required: bool = True
    #: ``whisper`` runs faster-whisper locally on the GPU/CPU; ``none``
    #: disables transcription and leaves the dashboard text-only.
    stt_provider: Literal["whisper", "none"] = "whisper"
    #: ``small.en`` is the sweet spot for a 4 GB GPU. ``base.en`` is faster,
    #: ``medium.en`` more accurate but needs more VRAM.
    stt_model: str = "small.en"
    stt_device: Literal["auto", "cuda", "cpu"] = "auto"
    #: ``auto`` picks float16 on CUDA and int8 on CPU.
    stt_compute_type: Literal["auto", "int8", "int8_float16", "float16", "float32"] = "auto"
    #: Reject clips shorter than this (ms) as coughs, clicks and door slams.
    min_utterance_ms: int = Field(default=350, ge=0, le=5000)
    #: Longest single clip accepted from the browser, in seconds.
    max_utterance_s: float = Field(default=20.0, ge=1.0, le=120.0)
