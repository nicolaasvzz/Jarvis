"""The Anthropic implementation of the Brain protocol.

Uses the official ``anthropic`` SDK (imported lazily, so the rest of Jarvis
works without the ``llm`` extra installed). Adaptive thinking is enabled and
the effort level comes from configuration. The SDK already retries rate
limits and transient server errors; a safety-classifier refusal is surfaced
as ``stop_reason == "refusal"`` for the orchestrator to handle rather than
raised as an exception.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from jarvis.brain.base import BrainMessage, BrainResponse
from jarvis.config.schema import LLMConfig
from jarvis.core.errors import BrainError
from jarvis.core.models import ToolCall
from jarvis.logging import get_logger

if TYPE_CHECKING:
    import anthropic

_log = get_logger(__name__)


def _error_detail(exc: anthropic.APIStatusError) -> str | None:
    """Pull the provider's own ``error.message`` out of the response body."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
    return None


def _friendly_message(exc: anthropic.APIStatusError) -> str:
    """Turn an Anthropic API status error into plain, actionable language."""
    detail = _error_detail(exc)
    status = exc.status_code
    if status == 401:
        return (
            "Anthropic rejected the API key — check ANTHROPIC_API_KEY in your "
            ".env file."
        )
    if status == 400 and detail and "credit balance" in detail.lower():
        return (
            "Your Anthropic account has insufficient credit. Add credits at "
            "https://console.anthropic.com/settings/billing — note a "
            "Claude.ai Pro/Max subscription is separate from API billing and "
            "does not include API credit."
        )
    if status == 429:
        return "Anthropic rate-limited this request — wait a moment and try again."
    if status is not None and status >= 500:
        return "Anthropic's API is having problems right now — try again shortly."
    return f"Anthropic API error: {detail or exc.message}"


class AnthropicBrain:
    """Brain backed by the Anthropic Messages API."""

    def __init__(self, config: LLMConfig, api_key: SecretStr) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "The 'anthropic' package is not installed. "
                'Install it with: pip install "jarvis-assistant[llm]"'
            ) from exc
        self._config = config
        self._client = anthropic.AsyncAnthropic(api_key=api_key.get_secret_value())
        self._anthropic = anthropic

    async def complete(
        self,
        *,
        system: str,
        messages: list[BrainMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> BrainResponse:
        request: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._config.effort},
        }
        if tools:
            request["tools"] = tools

        _log.info(
            "calling model",
            extra={"model": self._config.model, "n_messages": len(messages)},
        )
        try:
            response = await self._client.messages.create(**request)
        except self._anthropic.APIStatusError as exc:
            message = _friendly_message(exc)
            _log.error(
                "model call failed",
                extra={"status_code": exc.status_code, "error": message},
            )
            raise BrainError(message) from exc
        except self._anthropic.APIConnectionError as exc:
            message = (
                "Could not reach the Anthropic API — check the internet "
                "connection on this machine."
            )
            _log.error("model call failed", extra={"error": str(exc)})
            raise BrainError(message) from exc

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(tool=block.name, arguments=dict(block.input)))

        result = BrainResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
        )
        if result.refused:
            _log.warning("model refused the request")
        _log.info(
            "model responded",
            extra={
                "stop_reason": result.stop_reason,
                "n_tool_calls": len(tool_calls),
                "output_tokens": response.usage.output_tokens,
            },
        )
        return result
