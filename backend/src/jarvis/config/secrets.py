"""Secret values, kept structurally separate from ordinary configuration.

Secrets can ONLY be supplied via environment variables or a local ``.env``
file (which is gitignored). They are intentionally not fields on
:class:`jarvis.config.settings.AppConfig`, so it is impossible to put an
API key in ``config.yaml`` and accidentally commit it.

Values are wrapped in :class:`pydantic.SecretStr`, so they never appear in
``repr()`` output or logs; call ``.get_secret_value()`` at the exact point
of use.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    """All secrets Jarvis needs, read from the environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # .env.example ships every key as "KEY=" for people to fill in. An
        # unfilled one must read as missing - so require() can say where to
        # get it - not as an empty secret that fails later and less clearly.
        env_ignore_empty=True,
    )

    # Gemini API key for the default Brain (env: GEMINI_API_KEY, or
    # GOOGLE_API_KEY — the name Google's own tools use). Free from
    # https://aistudio.google.com/apikey
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("gemini_api_key", "google_api_key"),
    )

    # Anthropic API key, needed only for llm.provider "anthropic"
    # (env: ANTHROPIC_API_KEY).
    anthropic_api_key: SecretStr | None = None

    # Shared token the phone client must present to the API server
    # (env: JARVIS_API_TOKEN).
    jarvis_api_token: SecretStr | None = None

    # Bot token from @BotFather for the Telegram phone bridge
    # (env: TELEGRAM_BOT_TOKEN).
    telegram_bot_token: SecretStr | None = None

    # Optional auth token for a protected ntfy push server
    # (env: JARVIS_PUSH_TOKEN). Public ntfy.sh topics need none.
    jarvis_push_token: SecretStr | None = None

    def require(self, name: str) -> SecretStr:
        """Return the named secret or raise a clear error if it is unset."""
        value: SecretStr | None = getattr(self, name)
        if value is None:
            env_var = name.upper()
            raise RuntimeError(
                f"Missing required secret {name!r}: set the {env_var} "
                "environment variable or add it to your .env file."
            )
        return value


def load_secrets(env_file: str | Path | None = ".env") -> Secrets:
    """Load secrets from the environment, optionally merging a ``.env`` file."""
    return Secrets(_env_file=env_file)  # type: ignore[call-arg]
