"""Provider selection: turn configuration into a concrete Brain.

One function, one decision — which implementation of the
:class:`~jarvis.brain.base.Brain` protocol to construct. Everything
downstream (Planner, Orchestrator, Tool Manager, memory, phone bridge, the
API server) sees only the protocol, so this is the sole place that changes
when a provider is added or swapped.

Each provider's requirements are enforced *only* when that provider is
selected: running on Ollama never asks for an Anthropic API key, and the
``anthropic`` package is never even imported.
"""

from __future__ import annotations

from jarvis.brain.base import Brain
from jarvis.config.schema import LLMConfig
from jarvis.config.secrets import Secrets
from jarvis.logging import get_logger

_log = get_logger(__name__)


def build_brain(config: LLMConfig, secrets: Secrets) -> Brain:
    """Build the Brain named by ``config.provider``.

    The imports are deliberately local: selecting one provider must not
    drag the other's dependencies into the process.
    """
    if config.provider == "ollama":
        from jarvis.brain.ollama_brain import OllamaBrain

        _log.info(
            "using ollama brain",
            extra={"model": config.model, "base_url": config.base_url},
        )
        return OllamaBrain(config)

    if config.provider == "anthropic":
        from jarvis.brain.anthropic_brain import AnthropicBrain

        api_key = secrets.require("anthropic_api_key")
        _log.info("using anthropic brain", extra={"model": config.model})
        return AnthropicBrain(config, api_key)

    # Unreachable while provider is a Literal, but a wrong value from a
    # future edit should fail loudly rather than silently pick a provider.
    raise ValueError(  # pragma: no cover - guarded by the config schema
        f"Unknown LLM provider {config.provider!r}. Supported: ollama, anthropic."
    )
