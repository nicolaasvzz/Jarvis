"""Tests for the Ollama Brain: response parsing and error translation.

Mirrors ``tests/test_brain.py`` for the Anthropic implementation. Ollama
failures (server not running, unknown model, server error) must never reach
the user as a raw exception — they are caught in
:class:`OllamaBrain.complete` and re-raised as a
:class:`~jarvis.core.errors.BrainError` with a plain-language message.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jarvis.brain.base import BrainMessage
from jarvis.brain.ollama_brain import OllamaBrain, _friendly_message
from jarvis.config.schema import LLMConfig
from jarvis.core.errors import BrainError

pytest.importorskip("httpx")


def _brain() -> OllamaBrain:
    return OllamaBrain(LLMConfig(provider="ollama", model="llama3.1"))


def _status_error(status_code: int, body: dict[str, Any] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    response = httpx.Response(status_code, request=request, json=body or {})
    return httpx.HTTPStatusError("error", request=request, response=response)


class TestFriendlyMessage:
    def test_model_not_found(self) -> None:
        exc = _status_error(404, {"error": "model 'llama3.1' not found"})
        message = _friendly_message(exc, LLMConfig(provider="ollama", model="llama3.1"))
        assert "was not found" in message
        assert "ollama pull llama3.1" in message

    def test_server_error(self) -> None:
        exc = _status_error(500, {"error": "boom"})
        assert "having problems" in _friendly_message(exc, LLMConfig())

    def test_other_error_falls_back_to_detail(self) -> None:
        exc = _status_error(400, {"error": "bad request"})
        message = _friendly_message(exc, LLMConfig())
        assert "bad request" in message


class TestOllamaBrainErrorHandling:
    async def test_connection_error_becomes_brain_error(self) -> None:
        brain = _brain()

        async def _raise(*_args: Any, **_kwargs: Any) -> httpx.Response:
            request = httpx.Request("POST", "http://localhost:11434/api/chat")
            raise httpx.ConnectError("refused", request=request)

        brain._client.post = _raise  # type: ignore[method-assign]
        with pytest.raises(BrainError, match="Could not reach Ollama"):
            await brain.complete(
                system="s", messages=[BrainMessage(role="user", content="hi")]
            )

    async def test_status_error_becomes_brain_error(self) -> None:
        brain = _brain()

        async def _respond(*_args: Any, **_kwargs: Any) -> httpx.Response:
            request = httpx.Request("POST", "http://localhost:11434/api/chat")
            return httpx.Response(
                404, request=request, json={"error": "model not found"}
            )

        brain._client.post = _respond  # type: ignore[method-assign]
        with pytest.raises(BrainError, match="was not found"):
            await brain.complete(
                system="s", messages=[BrainMessage(role="user", content="hi")]
            )

    async def test_success_parses_text_response(self) -> None:
        brain = _brain()

        async def _respond(*_args: Any, **_kwargs: Any) -> httpx.Response:
            request = httpx.Request("POST", "http://localhost:11434/api/chat")
            return httpx.Response(
                200,
                request=request,
                json={"message": {"role": "assistant", "content": "hello there"}},
            )

        brain._client.post = _respond  # type: ignore[method-assign]
        result = await brain.complete(
            system="s", messages=[BrainMessage(role="user", content="hi")]
        )
        assert result.text == "hello there"
        assert result.tool_calls == []
        assert result.stop_reason == "end_turn"

    async def test_success_parses_tool_calls(self) -> None:
        brain = _brain()

        async def _respond(*_args: Any, **_kwargs: Any) -> httpx.Response:
            request = httpx.Request("POST", "http://localhost:11434/api/chat")
            return httpx.Response(
                200,
                request=request,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "read_file",
                                    "arguments": {"path": "a.txt"},
                                }
                            }
                        ],
                    }
                },
            )

        brain._client.post = _respond  # type: ignore[method-assign]
        result = await brain.complete(
            system="s", messages=[BrainMessage(role="user", content="hi")]
        )
        assert result.stop_reason == "tool_use"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool == "read_file"
        assert result.tool_calls[0].arguments == {"path": "a.txt"}
