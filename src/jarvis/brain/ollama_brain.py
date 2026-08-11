"""The Ollama implementation of the Brain protocol — a local, offline LLM.

Talks to a local ``ollama serve`` instance over its HTTP API
(``/api/chat``), so no request ever leaves the machine and no internet
connection is required. Uses ``httpx`` (imported lazily, so the rest of
Jarvis works without the ``ollama`` extra installed).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from jarvis.brain.base import BrainMessage, BrainResponse
from jarvis.config.schema import LLMConfig
from jarvis.core.errors import BrainError
from jarvis.core.models import ToolCall
from jarvis.logging import get_logger

if TYPE_CHECKING:
    import httpx

_log = get_logger(__name__)


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-shaped tool schemas -> Ollama's OpenAI-style tool schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get(
                    "input_schema", {"type": "object", "properties": {}}
                ),
            },
        }
        for t in tools
    ]


def _parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function", {})
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        calls.append(ToolCall(tool=str(function.get("name", "")), arguments=arguments))
    return calls


def _friendly_message(exc: httpx.HTTPStatusError, config: LLMConfig) -> str:
    """Turn an Ollama HTTP error into plain, actionable language."""
    status = exc.response.status_code
    detail: str | None = None
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            detail = body.get("error")
    except ValueError:
        detail = exc.response.text or None
    if status == 404:
        return (
            f"Model {config.model!r} was not found on the Ollama server. "
            f"Pull it first: ollama pull {config.model}"
        )
    if status >= 500:
        return "The Ollama server is having problems right now — try again shortly."
    return f"Ollama API error ({status}): {detail or exc.response.text}"


class OllamaBrain:
    """Brain backed by a local Ollama server — runs fully offline."""

    def __init__(self, config: LLMConfig) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "The 'httpx' package is not installed. "
                'Install it with: pip install "jarvis-assistant[ollama]"'
            ) from exc
        self._config = config
        self._httpx = httpx
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=config.base_url, timeout=config.timeout_seconds
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[BrainMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> BrainResponse:
        request: dict[str, Any] = {
            "model": self._config.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
            "options": {"num_predict": self._config.max_tokens},
        }
        if tools:
            request["tools"] = _convert_tools(tools)

        _log.info(
            "calling model",
            extra={"model": self._config.model, "n_messages": len(messages)},
        )
        try:
            response = await self._client.post("/api/chat", json=request)
            response.raise_for_status()
        except self._httpx.ConnectError as exc:
            message = (
                f"Could not reach Ollama at {self._config.base_url} — is "
                "`ollama serve` running?"
            )
            _log.error("model call failed", extra={"error": str(exc)})
            raise BrainError(message) from exc
        except self._httpx.HTTPStatusError as exc:
            message = _friendly_message(exc, self._config)
            _log.error(
                "model call failed",
                extra={"status_code": exc.response.status_code, "error": message},
            )
            raise BrainError(message) from exc
        except self._httpx.HTTPError as exc:
            message = f"Ollama request failed: {exc}"
            _log.error("model call failed", extra={"error": str(exc)})
            raise BrainError(message) from exc

        body = response.json()
        message_obj = body.get("message") or {}
        text = message_obj.get("content") or ""
        tool_calls = _parse_tool_calls(message_obj)
        stop_reason = "tool_use" if tool_calls else "end_turn"

        result = BrainResponse(text=text, tool_calls=tool_calls, stop_reason=stop_reason)
        _log.info(
            "model responded",
            extra={"stop_reason": result.stop_reason, "n_tool_calls": len(tool_calls)},
        )
        return result

    async def aclose(self) -> None:
        await self._client.aclose()
