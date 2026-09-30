"""The Gemini implementation of the Brain protocol.

Talks to Google's Gemini API (``models.generateContent``) over plain HTTPS
with ``httpx`` — no Google SDK, so the only thing a user needs is an API
key. A free one from https://aistudio.google.com/apikey is enough: the
default model sits on Gemini's free tier.

Tools are sent as real function declarations, and the calls that come back
are translated into the same :class:`~jarvis.core.models.ToolCall` objects
every other module already speaks. Nothing downstream can tell which
provider answered.

Two facts of life about the free tier are absorbed here rather than allowed
to leak into the Planner:

* it is rate-limited per minute, so a ``429`` is retried after the delay
  Google asks for (capped), instead of failing a task that would have
  succeeded a few seconds later;
* thinking tokens are billed and counted against the limits like any
  other output, so the thinking level is configurable and defaults low.

``httpx`` is imported lazily, the same way the Anthropic brain imports its
SDK, so importing this module never fails on a machine that lacks it.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from jarvis.brain.base import BrainMessage, BrainResponse
from jarvis.config.schema import LLMConfig
from jarvis.core.errors import BrainError
from jarvis.core.models import ToolCall
from jarvis.logging import get_logger

if TYPE_CHECKING:
    import httpx

_log = get_logger(__name__)

API_KEY_URL = "https://aistudio.google.com/apikey"

# Gemini's finishReason values, mapped onto the vocabulary the rest of
# Jarvis already uses. Anything that means "a safety system stopped this"
# becomes "refusal", which the Planner reports instead of retrying.
_STOP_REASONS = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "refusal",
    "RECITATION": "refusal",
    "BLOCKLIST": "refusal",
    "PROHIBITED_CONTENT": "refusal",
    "SPII": "refusal",
}

# Statuses worth another attempt: rate limits and an overloaded service.
_RETRYABLE = {429, 500, 503}
# Never sleep longer than this for one retry, whatever the server asks for.
# A per-day quota answers "retry in 20 hours", which is a failure, not a wait.
_MAX_RETRY_WAIT = 60.0

_DELAY = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")


def _to_gemini_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Jarvis tool schemas into Gemini function declarations.

    Jarvis carries tool definitions in Anthropic's shape (``name``,
    ``description``, ``input_schema``). The argument schema goes across
    untouched as ``parametersJsonSchema``, which takes full JSON Schema —
    the older ``parameters`` field only accepts an OpenAPI subset and
    rejects keywords the tool schemas use. Every tool is still defined in
    exactly one place (:meth:`jarvis.tools.base.Tool.schema`).
    """
    declarations: list[dict[str, Any]] = []
    for tool in tools:
        schema = tool.get("input_schema") or tool.get("parameters")
        declarations.append(
            {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parametersJsonSchema": schema
                or {"type": "object", "properties": {}},
            }
        )
    return [{"functionDeclarations": declarations}]


def _contents(messages: list[BrainMessage]) -> list[dict[str, Any]]:
    """Jarvis turns → Gemini ``contents``; Gemini calls the assistant "model"."""
    return [
        {
            "role": "model" if m.role == "assistant" else "user",
            "parts": [{"text": m.content}],
        }
        for m in messages
    ]


def _retry_delay(response: httpx.Response) -> float | None:
    """The wait Google asked for in a ``RetryInfo`` detail, in seconds."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        match = _DELAY.match(str(detail.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None


def _error_detail(response: httpx.Response) -> str:
    """Pull Google's own ``error.message`` out of an error response body."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
    return response.text.strip()


class GeminiBrain:
    """Brain backed by the Gemini API — a free API key is enough."""

    def __init__(
        self,
        config: LLMConfig,
        api_key: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "The 'httpx' package is needed to talk to Gemini. "
                "Install it with: pip install httpx"
            ) from exc
        self._config = config
        self._httpx = httpx
        self._base_url = config.base_url.rstrip("/")
        # The key travels in a header, never the URL, so it cannot end up in
        # a proxy log or an error message that quotes the request.
        self._headers = {"x-goog-api-key": api_key.get_secret_value()}
        self._client = client or httpx.AsyncClient(timeout=config.timeout)
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self._config.model

    async def complete(
        self,
        *,
        system: str,
        messages: list[BrainMessage],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> BrainResponse:
        generation: dict[str, Any] = {"maxOutputTokens": self._config.max_tokens}
        if self._config.temperature is not None:
            generation["temperature"] = self._config.temperature
        if self._config.thinking_level is not None:
            generation["thinkingConfig"] = {
                "thinkingLevel": self._config.thinking_level.upper()
            }
        if json_mode and not tools:
            # Constrained decoding: the reply cannot come back with a missing
            # comma or an unclosed brace. Gemini refuses this combined with
            # function calling, so it is only asked for on tool-free calls —
            # which is how the Planner calls it.
            generation["responseMimeType"] = "application/json"

        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _contents(messages),
            "generationConfig": generation,
        }
        if tools:
            payload["tools"] = _to_gemini_tools(tools)

        _log.info(
            "calling model",
            extra={
                "model": self._config.model,
                "n_messages": len(messages),
                "n_tools": len(tools or []),
            },
        )
        data = await self._request(
            "POST", f"/models/{self._config.model}:generateContent", payload
        )
        result = self._parse(data)
        usage = data.get("usageMetadata")
        usage = usage if isinstance(usage, dict) else {}
        _log.info(
            "model responded",
            extra={
                "stop_reason": result.stop_reason,
                "n_tool_calls": len(result.tool_calls),
                "output_tokens": usage.get("candidatesTokenCount"),
                "thinking_tokens": usage.get("thoughtsTokenCount"),
            },
        )
        return result

    async def describe_model(self) -> dict[str, Any]:
        """Look up the configured model — proves the key works, costs no quota.

        Used by ``jarvis brain`` to check the key is accepted and the model
        name is real before anything depends on either.
        """
        return await self._request("GET", f"/models/{self._config.model}", None)

    async def aclose(self) -> None:
        """Close the HTTP connection pool (the runtime calls this on exit)."""
        await self._client.aclose()

    # -- response -----------------------------------------------------------
    def _parse(self, data: dict[str, Any]) -> BrainResponse:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            feedback = data.get("promptFeedback")
            if isinstance(feedback, dict) and feedback.get("blockReason"):
                # The prompt itself was blocked; there is no answer to parse.
                _log.warning(
                    "model refused the request",
                    extra={"block_reason": feedback.get("blockReason")},
                )
                return BrainResponse(stop_reason="refusal")
            raise BrainError("Gemini returned a reply with no candidates.")

        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict) or part.get("thought"):
                continue  # reasoning summaries are not the answer
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            call = part.get("functionCall")
            if isinstance(call, dict) and call.get("name"):
                args = call.get("args")
                tool_calls.append(
                    ToolCall(
                        tool=str(call["name"]),
                        arguments=args if isinstance(args, dict) else {},
                    )
                )

        finish = str(candidate.get("finishReason") or "STOP")
        stop_reason = (
            "tool_use" if tool_calls else _STOP_REASONS.get(finish, finish.lower())
        )

        text = "".join(text_parts)
        if not text and not tool_calls and stop_reason != "refusal":
            if finish == "MAX_TOKENS":
                raise BrainError(
                    f"{self._config.model!r} used its whole output budget "
                    "before answering (thinking counts against it). Raise "
                    "llm.max_tokens or lower llm.thinking_level."
                )
            raise BrainError(
                f"{self._config.model!r} returned an empty reply "
                f"(finish reason: {finish})."
            )
        if stop_reason == "refusal":
            _log.warning("model refused the request", extra={"finish": finish})
        return BrainResponse(text=text, tool_calls=tool_calls, stop_reason=stop_reason)

    # -- HTTP ---------------------------------------------------------------
    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Call Gemini and return the decoded body, or raise ``BrainError``.

        Rate limits and overload are retried with backoff. Every other
        failure is translated into plain, actionable language here, so
        callers only ever see a :class:`BrainError` they can show the user.
        """
        url = f"{self._base_url}{path}"
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method, url, json=payload, headers=self._headers
                )
                response.raise_for_status()
                data = response.json()
                break
            except self._httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                wait = self._backoff(exc.response, attempt)
                if status in _RETRYABLE and wait is not None:
                    attempt += 1
                    _log.warning(
                        "model call throttled, retrying",
                        extra={"status_code": status, "wait": wait, "attempt": attempt},
                    )
                    await self._sleep(wait)
                    continue
                message = self._status_message(exc.response)
                _log.error(
                    "model call failed",
                    extra={"status_code": status, "error": message},
                )
                raise BrainError(message) from exc
            except self._httpx.TimeoutException as exc:
                message = (
                    f"Gemini did not answer within {self._config.timeout:.0f}s. "
                    "Raise llm.timeout, or lower llm.thinking_level so it "
                    "spends less time reasoning."
                )
                _log.error("model call failed", extra={"error": str(exc)})
                raise BrainError(message) from exc
            except self._httpx.RequestError as exc:
                message = (
                    "Could not reach the Gemini API — check the internet "
                    "connection on this machine."
                )
                _log.error("model call failed", extra={"error": str(exc)})
                raise BrainError(message) from exc
            except json.JSONDecodeError as exc:
                message = "Gemini returned something that is not JSON."
                _log.error("model call failed", extra={"error": str(exc)})
                raise BrainError(message) from exc

        if not isinstance(data, dict):
            raise BrainError("Gemini returned an unexpected response shape.")
        return data

    def _backoff(self, response: httpx.Response, attempt: int) -> float | None:
        """Seconds to wait before retrying, or ``None`` to stop retrying."""
        if attempt >= self._config.max_retries:
            return None
        asked = _retry_delay(response)
        if asked is None:
            return float(2 ** (attempt + 1))  # 2s, 4s, 8s ...
        if asked > _MAX_RETRY_WAIT:
            return None  # a daily quota, not a per-minute blip
        return asked

    def _status_message(self, response: httpx.Response) -> str:
        """Turn an HTTP error from Gemini into plain, actionable language."""
        detail = _error_detail(response)
        status = response.status_code
        lowered = detail.lower()
        if "api key" in lowered or status in {401, 403}:
            return (
                "Gemini rejected the API key — check GEMINI_API_KEY in your "
                f".env file. A free key comes from {API_KEY_URL}"
            )
        if status == 404:
            return (
                f"Gemini has no model called {self._config.model!r}. Check "
                "llm.model in your config (run `jarvis brain` to test it)."
            )
        if status == 429:
            return (
                "Gemini's rate limit was hit (the free tier allows a limited "
                "number of requests per minute and per day). Wait a minute and "
                "try again, or lower agent.pool_size so fewer requests go out "
                f"at once. Details: {detail or 'none given'}"
            )
        if status >= 500:
            return f"Gemini's API is having problems right now — try again shortly. ({detail})"
        if "thinking" in lowered:
            return (
                f"{self._config.model!r} does not accept llm.thinking_level "
                f"{self._config.thinking_level!r} — set it to null in your "
                f"config. Details: {detail}"
            )
        return f"Gemini rejected the request ({status}): {detail or 'no detail given'}"
