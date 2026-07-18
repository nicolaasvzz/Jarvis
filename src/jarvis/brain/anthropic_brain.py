"""The Anthropic implementation of the Brain protocol.

Uses the official ``anthropic`` SDK (imported lazily, so the rest of Jarvis
works without the ``llm`` extra installed). Adaptive thinking is enabled and
the effort level comes from configuration. The SDK already retries rate
limits and transient server errors; a safety-classifier refusal is surfaced
as ``stop_reason == "refusal"`` for the orchestrator to handle rather than
raised as an exception.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from jarvis.brain.base import BrainMessage, BrainResponse
from jarvis.config.schema import LLMConfig
from jarvis.core.models import ToolCall
from jarvis.logging import get_logger

_log = get_logger(__name__)


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
        response = await self._client.messages.create(**request)

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
