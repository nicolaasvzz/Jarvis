"""The Ollama implementation of the Brain protocol.

Talks to a locally running Ollama server over its native ``/api/chat``
endpoint, so no conversation leaves the machine and no API key exists to
leak. Tools are sent as real function schemas — Ollama has first-class tool
calling — and the tool calls that come back are translated into the same
:class:`~jarvis.core.models.ToolCall` objects every other module already
speaks. Nothing downstream can tell which provider answered.

Two facts of life about local models are absorbed here rather than allowed
to leak into the Planner:

* reasoning models (gpt-oss and qwen3 among them) may wrap their reasoning in
  ``<think>`` tags inside the reply; those are stripped, so the Planner
  sees only the answer it asked for;
* Ollama's context window defaults to a few thousand tokens and silently
  truncates anything longer, which the planner's tool catalogue can exceed
  — so ``llm.context_window`` is sent explicitly with every request.

``httpx`` is imported lazily, the same way the Anthropic brain imports its
SDK, so importing this module never fails on a machine that lacks it.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from jarvis.brain.base import BrainMessage, BrainResponse
from jarvis.config.schema import LLMConfig
from jarvis.core.errors import BrainError
from jarvis.core.models import ToolCall
from jarvis.logging import get_logger

if TYPE_CHECKING:
    import httpx

_log = get_logger(__name__)

_CHAT_PATH = "/api/chat"
_TAGS_PATH = "/api/tags"

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)

# Ollama's ``done_reason`` values, mapped onto the vocabulary the rest of
# Jarvis already uses. Note "refusal" is deliberately unreachable: a local
# model has no safety classifier in front of it, so BrainResponse.refused
# stays False and the orchestrator treats replies as ordinary answers.
_STOP_REASONS = {"stop": "end_turn", "length": "max_tokens"}


def _strip_thinking(text: str) -> str:
    """Remove ``<think>`` reasoning blocks from a model reply."""
    cleaned = _THINK_BLOCK.sub("", text)
    # A reply cut off mid-thought leaves an unterminated tag behind.
    cleaned = _UNCLOSED_THINK.sub("", cleaned)
    return cleaned.strip()


def _to_ollama_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Jarvis tool schemas into Ollama's function-calling format.

    Jarvis carries tool definitions in Anthropic's shape (``name``,
    ``description``, ``input_schema``); Ollama uses the OpenAI shape. This
    is purely a rename of the wrapper — the JSON Schema describing the
    arguments is passed through untouched, so every tool is still defined
    in exactly one place (:meth:`jarvis.tools.base.Tool.schema`).
    """
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            converted.append(tool)  # already in Ollama's shape
            continue
        parameters = tool.get("input_schema") or tool.get("parameters")
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": parameters or {"type": "object", "properties": {}},
                },
            }
        )
    return converted


def _arguments(raw: Any) -> dict[str, Any]:
    """Normalise one tool call's arguments to a dict.

    Ollama returns a JSON object, but some models emit the arguments as a
    JSON *string* instead; both are accepted so a stray quoting choice by
    the model never sinks a whole task.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    """Turn Ollama's ``message.tool_calls`` into Jarvis :class:`ToolCall`s."""
    calls: list[ToolCall] = []
    for entry in message.get("tool_calls") or []:
        if not isinstance(entry, dict):
            continue
        function = entry.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not name:
            continue
        calls.append(
            ToolCall(tool=str(name), arguments=_arguments(function.get("arguments")))
        )
    return calls


def _stop_reason(done_reason: Any, tool_calls: list[ToolCall]) -> str:
    if tool_calls:
        return "tool_use"
    if isinstance(done_reason, str) and done_reason:
        return _STOP_REASONS.get(done_reason, done_reason)
    return "end_turn"


class OllamaBrain:
    """Brain backed by a local Ollama server — no API key, no egress."""

    def __init__(
        self, config: LLMConfig, *, client: httpx.AsyncClient | None = None
    ) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "The 'httpx' package is needed to talk to Ollama. "
                "Install it with: pip install httpx"
            ) from exc
        self._config = config
        self._httpx = httpx
        self._base_url = config.base_url.rstrip("/")
        # trust_env=False: Ollama runs on this machine, so an HTTP_PROXY or
        # ALL_PROXY inherited from the environment (common on corporate
        # networks and VPNs) would send local traffic somewhere it cannot
        # come back from.
        self._client = client or httpx.AsyncClient(
            timeout=config.timeout, trust_env=False
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[BrainMessage],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> BrainResponse:
        options: dict[str, Any] = {
            "num_predict": self._config.max_tokens,
            "num_ctx": self._config.context_window,
        }
        if self._config.temperature is not None:
            options["temperature"] = self._config.temperature

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
            "stream": False,
            "options": options,
        }
        if self._config.think is not None:
            payload["think"] = self._config.think
        if tools:
            payload["tools"] = _to_ollama_tools(tools)
        if json_mode:
            # Ollama constrains decoding to a JSON grammar, so the reply
            # cannot come back with a missing comma or an unclosed brace.
            payload["format"] = "json"
            # Do NOT also disable thinking here. Measured on gpt-oss:20b with
            # this planning prompt: format=json alone returns 3968 chars of
            # content, while format=json plus think=false returns an entirely
            # empty reply (eval_count 112, done_reason "stop"). A harmony
            # model needs its analysis channel to reach a final answer at all.

        _log.info(
            "calling model",
            extra={
                "model": self._config.model,
                "n_messages": len(messages),
                "n_tools": len(payload.get("tools", [])),
            },
        )
        data = await self._request("POST", _CHAT_PATH, payload)

        message = data.get("message")
        if not isinstance(message, dict):
            raise BrainError(
                "Ollama returned a reply with no message content — "
                f"check that {self._config.model!r} is a chat model."
            )

        tool_calls = _parse_tool_calls(message)
        content = message.get("content")
        if not content and not tool_calls:
            # An empty reply is a failure however it happened - budget spent
            # entirely on `thinking`, or a model that answers nothing under
            # the settings in play. Name it here, because the caller only
            # sees the downstream symptom ("no JSON object found").
            thought = len(message.get("thinking") or "")
            raise BrainError(
                f"{self._config.model!r} returned an empty reply "
                f"({thought} chars of internal reasoning, no answer). "
                "Raise llm.max_tokens if this repeats."
            )
        result = BrainResponse(
            text=_strip_thinking(content if isinstance(content, str) else ""),
            tool_calls=tool_calls,
            stop_reason=_stop_reason(data.get("done_reason"), tool_calls),
        )
        _log.info(
            "model responded",
            extra={
                "stop_reason": result.stop_reason,
                "n_tool_calls": len(tool_calls),
                "output_tokens": data.get("eval_count"),
            },
        )
        return result

    async def list_models(self) -> list[str]:
        """Model names this Ollama server has downloaded.

        Used by ``jarvis brain`` to prove the server is reachable and that
        the configured model is actually present before anything depends
        on it.
        """
        data = await self._request("GET", _TAGS_PATH, None)
        models = data.get("models")
        if not isinstance(models, list):
            return []
        return sorted(
            str(entry["name"])
            for entry in models
            if isinstance(entry, dict) and entry.get("name")
        )

    async def aclose(self) -> None:
        """Close the HTTP connection pool (the runtime calls this on exit)."""
        await self._client.aclose()

    # -- HTTP ---------------------------------------------------------------
    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Call Ollama and return the decoded body, or raise ``BrainError``.

        Every failure mode is translated into plain, actionable language
        here, so callers only ever see a :class:`BrainError` they can show
        the user — exactly as the Anthropic brain does for its own SDK.
        """
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.request(method, url, json=payload)
            response.raise_for_status()
            data = response.json()
        except self._httpx.HTTPStatusError as exc:
            message = self._status_message(exc)
            _log.error(
                "model call failed",
                extra={"status_code": exc.response.status_code, "error": message},
            )
            raise BrainError(message) from exc
        except self._httpx.TimeoutException as exc:
            message = (
                f"Ollama did not answer within {self._config.timeout:.0f}s. "
                f"A large model on modest hardware can need longer — raise "
                f"llm.timeout, or use a smaller model than {self._config.model!r}."
            )
            _log.error("model call failed", extra={"error": str(exc)})
            raise BrainError(message) from exc
        except self._httpx.RequestError as exc:
            message = (
                f"Could not reach Ollama at {self._base_url} — start it with "
                "'ollama serve' and check llm.base_url."
            )
            _log.error("model call failed", extra={"error": str(exc)})
            raise BrainError(message) from exc
        except json.JSONDecodeError as exc:
            message = (
                f"Ollama at {self._base_url} returned something that is not "
                "JSON — check that llm.base_url points at an Ollama server."
            )
            _log.error("model call failed", extra={"error": str(exc)})
            raise BrainError(message) from exc

        if not isinstance(data, dict):
            raise BrainError("Ollama returned an unexpected response shape.")
        error = data.get("error")
        if isinstance(error, str) and error:
            raise BrainError(f"Ollama error: {error}")
        return data

    def _status_message(self, exc: httpx.HTTPStatusError) -> str:
        """Turn an HTTP error from Ollama into plain, actionable language."""
        detail = _error_detail(exc.response)
        status = exc.response.status_code
        lowered = detail.lower()
        if status == 404:
            return (
                f"Ollama does not have the model {self._config.model!r}. "
                f"Download it with: ollama pull {self._config.model}"
            )
        if "does not support tools" in lowered:
            return (
                f"The model {self._config.model!r} does not support tool "
                "calling, which Jarvis needs. Use a tool-capable model such "
                "as gpt-oss:20b."
            )
        if status >= 500:
            return f"Ollama hit an internal error: {detail or 'no detail given'}"
        return f"Ollama rejected the request ({status}): {detail or 'no detail given'}"


def _error_detail(response: httpx.Response) -> str:
    """Pull Ollama's own ``error`` string out of an error response body."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, str):
            return error
    return response.text.strip()
