"""Tests for the Anthropic Brain: response parsing and error translation.

Anthropic API failures (bad key, no credit, rate limits, outages, network
errors) must never reach the user as a raw SDK exception dump — they are
caught in :class:`AnthropicBrain.complete` and re-raised as a
:class:`~jarvis.core.errors.BrainError` with a plain-language, actionable
message.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jarvis.brain.anthropic_brain import AnthropicBrain, _friendly_message
from jarvis.brain.base import BrainMessage
from jarvis.config.schema import LLMConfig
from jarvis.core.errors import BrainError

anthropic = pytest.importorskip("anthropic")


def _status_error(
    error_type: str, status_code: int, message: str
) -> anthropic.APIStatusError:
    """Build a real Anthropic SDK exception, the way the SDK itself would."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code, request=request, json={"type": "error"})
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    cls: type[anthropic.APIStatusError] = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
    }[status_code]
    return cls(f"Error code: {status_code} - {body}", response=response, body=body)


class _FailingClientMessages:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def create(self, **_kwargs: Any) -> Any:
        raise self._exc


class _FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.messages = _FailingClientMessages(exc)


def _brain_with_failure(exc: Exception) -> AnthropicBrain:
    brain = AnthropicBrain.__new__(AnthropicBrain)
    brain._config = LLMConfig()
    brain._client = _FailingClient(exc)  # type: ignore[attr-defined]
    brain._anthropic = anthropic
    return brain


class TestFriendlyMessage:
    def test_insufficient_credit(self) -> None:
        exc = _status_error(
            "invalid_request_error",
            400,
            "Your credit balance is too low to access the Anthropic API. "
            "Please go to Plans & Billing to upgrade or purchase credits.",
        )
        message = _friendly_message(exc)
        assert "insufficient credit" in message
        assert "console.anthropic.com/settings/billing" in message

    def test_bad_api_key(self) -> None:
        exc = _status_error("authentication_error", 401, "invalid x-api-key")
        message = _friendly_message(exc)
        assert "API key" in message
        assert "ANTHROPIC_API_KEY" in message

    def test_rate_limited(self) -> None:
        exc = _status_error("rate_limit_error", 429, "rate limited")
        assert "rate-limited" in _friendly_message(exc)

    def test_server_error(self) -> None:
        exc = _status_error("api_error", 500, "internal error")
        assert "having problems" in _friendly_message(exc)

    def test_other_bad_request_falls_back_to_provider_detail(self) -> None:
        exc = _status_error("invalid_request_error", 400, "max_tokens too large")
        message = _friendly_message(exc)
        assert "max_tokens too large" in message


class TestAnthropicBrainErrorHandling:
    async def test_status_error_becomes_brain_error(self) -> None:
        exc = _status_error(
            "invalid_request_error",
            400,
            "Your credit balance is too low to access the Anthropic API.",
        )
        brain = _brain_with_failure(exc)
        with pytest.raises(BrainError, match="insufficient credit"):
            await brain.complete(
                system="s", messages=[BrainMessage(role="user", content="hi")]
            )

    async def test_connection_error_becomes_brain_error(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        exc = anthropic.APIConnectionError(request=request)
        brain = _brain_with_failure(exc)
        with pytest.raises(BrainError, match="Could not reach the Anthropic API"):
            await brain.complete(
                system="s", messages=[BrainMessage(role="user", content="hi")]
            )
