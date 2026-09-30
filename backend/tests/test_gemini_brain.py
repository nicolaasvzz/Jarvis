"""Tests for the Gemini brain, against a fake HTTP transport.

No test here talks to Google: every request is answered by a handler that
records what was sent, so the suite needs neither a network nor a key.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from jarvis.brain.base import BrainMessage
from jarvis.brain.gemini_brain import GeminiBrain
from jarvis.config.schema import LLMConfig
from jarvis.core.errors import BrainError

KEY = "test-gemini-key"
TOOL = {
    "name": "write_file",
    "description": "Write text to a file.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
}


def reply(*parts: dict[str, Any], finish: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [
            {"content": {"role": "model", "parts": list(parts)}, "finishReason": finish}
        ],
        "usageMetadata": {"candidatesTokenCount": 7, "thoughtsTokenCount": 3},
    }


class FakeGemini:
    """Answers each request with the next scripted (status, body) pair."""

    def __init__(self, *answers: tuple[int, Any]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body)

    @property
    def last_body(self) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self.requests[-1].content)
        return body


def brain_for(fake: FakeGemini, **config: Any) -> tuple[GeminiBrain, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    brain = GeminiBrain(
        LLMConfig(provider="gemini", **config),
        SecretStr(KEY),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
        sleep=sleep,
    )
    return brain, slept


def rate_limited(delay: str | None) -> tuple[int, dict[str, Any]]:
    details = [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}
    ] if delay else []
    return 429, {
        "error": {
            "code": 429,
            "message": "Resource has been exhausted (e.g. check quota).",
            "status": "RESOURCE_EXHAUSTED",
            "details": details,
        }
    }


class TestRequest:
    async def test_sends_system_messages_and_generation_settings(self) -> None:
        fake = FakeGemini((200, reply({"text": "hello"})))
        brain, _ = brain_for(fake, max_tokens=900, temperature=0.4)
        await brain.complete(
            system="be brief",
            messages=[
                BrainMessage(role="user", content="hi"),
                BrainMessage(role="assistant", content="hello"),
                BrainMessage(role="user", content="again"),
            ],
        )
        request = fake.requests[0]
        assert request.url.path == "/v1beta/models/gemini-3.8-flash:generateContent"
        body = fake.last_body
        assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
        assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
        assert body["contents"][2]["parts"] == [{"text": "again"}]
        generation = body["generationConfig"]
        assert generation["maxOutputTokens"] == 900
        assert generation["temperature"] == 0.4
        assert generation["thinkingConfig"] == {"thinkingLevel": "LOW"}

    async def test_key_goes_in_a_header_never_the_url(self) -> None:
        fake = FakeGemini((200, reply({"text": "ok"})))
        brain, _ = brain_for(fake)
        await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        request = fake.requests[0]
        assert request.headers["x-goog-api-key"] == KEY
        assert KEY not in str(request.url)

    async def test_unset_settings_are_left_to_the_model(self) -> None:
        fake = FakeGemini((200, reply({"text": "ok"})))
        brain, _ = brain_for(fake, thinking_level=None)
        await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        generation = fake.last_body["generationConfig"]
        assert "thinkingConfig" not in generation
        assert "temperature" not in generation

    async def test_json_mode_constrains_the_reply(self) -> None:
        fake = FakeGemini((200, reply({"text": "{}"})))
        brain, _ = brain_for(fake)
        await brain.complete(
            system="s", messages=[BrainMessage(role="user", content="x")], json_mode=True
        )
        assert fake.last_body["generationConfig"]["responseMimeType"] == "application/json"

    async def test_json_mode_is_not_combined_with_tools(self) -> None:
        # Gemini rejects a JSON mime type alongside function calling.
        fake = FakeGemini((200, reply({"text": "{}"})))
        brain, _ = brain_for(fake)
        await brain.complete(
            system="s",
            messages=[BrainMessage(role="user", content="x")],
            tools=[TOOL],
            json_mode=True,
        )
        assert "responseMimeType" not in fake.last_body["generationConfig"]

    async def test_tools_become_function_declarations(self) -> None:
        fake = FakeGemini((200, reply({"text": "ok"})))
        brain, _ = brain_for(fake)
        await brain.complete(
            system="s", messages=[BrainMessage(role="user", content="x")], tools=[TOOL]
        )
        (declaration,) = fake.last_body["tools"][0]["functionDeclarations"]
        assert declaration["name"] == "write_file"
        assert declaration["description"] == "Write text to a file."
        assert declaration["parametersJsonSchema"] == TOOL["input_schema"]


class TestResponse:
    async def test_text_answer(self) -> None:
        fake = FakeGemini((200, reply({"text": "Hello, "}, {"text": "sir."})))
        brain, _ = brain_for(fake)
        result = await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert result.text == "Hello, sir."
        assert result.stop_reason == "end_turn"
        assert not result.refused

    async def test_thought_summaries_are_not_part_of_the_answer(self) -> None:
        fake = FakeGemini(
            (200, reply({"text": "let me think...", "thought": True}, {"text": "42"}))
        )
        brain, _ = brain_for(fake)
        result = await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert result.text == "42"

    async def test_function_calls_become_tool_calls(self) -> None:
        call = {
            "functionCall": {"name": "write_file", "args": {"path": "a.txt", "content": "hi"}},
            "thoughtSignature": "c2lnbmF0dXJl",
        }
        fake = FakeGemini((200, reply(call)))
        brain, _ = brain_for(fake)
        result = await brain.complete(
            system="s", messages=[BrainMessage(role="user", content="x")], tools=[TOOL]
        )
        assert result.stop_reason == "tool_use"
        (tool_call,) = result.tool_calls
        assert tool_call.tool == "write_file"
        assert tool_call.arguments == {"path": "a.txt", "content": "hi"}

    async def test_safety_stop_is_a_refusal(self) -> None:
        fake = FakeGemini((200, reply(finish="SAFETY")))
        brain, _ = brain_for(fake)
        result = await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert result.refused

    async def test_blocked_prompt_is_a_refusal(self) -> None:
        fake = FakeGemini((200, {"promptFeedback": {"blockReason": "SAFETY"}}))
        brain, _ = brain_for(fake)
        result = await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert result.refused

    async def test_budget_spent_thinking_is_named(self) -> None:
        fake = FakeGemini((200, reply(finish="MAX_TOKENS")))
        brain, _ = brain_for(fake)
        with pytest.raises(BrainError, match="llm.max_tokens"):
            await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])


class TestRetries:
    async def test_rate_limit_waits_as_asked_then_succeeds(self) -> None:
        fake = FakeGemini(rate_limited("7s"), (200, reply({"text": "ok"})))
        brain, slept = brain_for(fake)
        result = await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert result.text == "ok"
        assert slept == [7.0]

    async def test_overload_backs_off_exponentially(self) -> None:
        fake = FakeGemini(
            (503, {"error": {"message": "overloaded"}}),
            (503, {"error": {"message": "overloaded"}}),
            (200, reply({"text": "ok"})),
        )
        brain, slept = brain_for(fake)
        await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert slept == [2.0, 4.0]

    async def test_gives_up_after_max_retries(self) -> None:
        fake = FakeGemini(rate_limited(None), rate_limited(None), rate_limited(None))
        brain, slept = brain_for(fake, max_retries=2)
        with pytest.raises(BrainError, match="rate limit"):
            await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert len(slept) == 2

    async def test_a_daily_quota_is_not_waited_out(self) -> None:
        # "Come back in 20 hours" is a failure to report, not a sleep.
        fake = FakeGemini(rate_limited("72000s"))
        brain, slept = brain_for(fake)
        with pytest.raises(BrainError, match="rate limit"):
            await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert slept == []


class TestErrors:
    async def test_bad_key_names_the_setting_and_where_to_get_one(self) -> None:
        message = "API key not valid. Please pass a valid API key."
        body = {"error": {"code": 400, "message": message}}
        fake = FakeGemini((400, body))
        brain, _ = brain_for(fake)
        with pytest.raises(BrainError, match="GEMINI_API_KEY") as caught:
            await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])
        assert "aistudio.google.com" in str(caught.value)

    async def test_unknown_model_names_the_setting(self) -> None:
        fake = FakeGemini((404, {"error": {"message": "models/nope is not found"}}))
        brain, _ = brain_for(fake, model="nope")
        with pytest.raises(BrainError, match="llm.model"):
            await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])

    async def test_unreachable_api_is_a_brain_error(self) -> None:
        def offline(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        brain = GeminiBrain(
            LLMConfig(),
            SecretStr(KEY),
            client=httpx.AsyncClient(transport=httpx.MockTransport(offline)),
        )
        with pytest.raises(BrainError, match="internet"):
            await brain.complete(system="s", messages=[BrainMessage(role="user", content="x")])


class TestDescribeModel:
    async def test_looks_the_model_up_without_generating(self) -> None:
        info = {"name": "models/gemini-3.8-flash", "inputTokenLimit": 1048576}
        fake = FakeGemini((200, info))
        brain, _ = brain_for(fake)
        assert await brain.describe_model() == info
        request = fake.requests[0]
        assert request.method == "GET"
        assert request.url.path == "/v1beta/models/gemini-3.8-flash"
