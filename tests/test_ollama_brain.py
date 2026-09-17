"""Tests for the Ollama Brain: the wire format in both directions.

The Brain protocol is what every other module depends on, so what matters
here is that a local model is fed exactly what the Planner and Orchestrator
already produce (system prompt, history, tool schemas) and that whatever
comes back is turned into the same :class:`BrainResponse` and
:class:`ToolCall` objects an Anthropic reply would have produced.

Every test drives the real :class:`OllamaBrain` over a mock HTTP transport,
so the request that would go on the wire is asserted byte for byte without
needing a running Ollama.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jarvis.brain.base import BrainMessage
from jarvis.brain.ollama_brain import OllamaBrain
from jarvis.config.schema import LLMConfig
from jarvis.core.errors import BrainError
from jarvis.core.models import ToolCall

# A tool definition exactly as jarvis.tools.base.Tool.schema() emits it.
WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": "Write text to a file in the workspace.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
}


def _reply(
    content: str = "",
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    done_reason: str = "stop",
) -> dict[str, Any]:
    """A response body shaped the way Ollama's /api/chat answers."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "model": "gpt-oss:20b",
        "message": message,
        "done": True,
        "done_reason": done_reason,
        "eval_count": 42,
    }


class Recorder:
    """Captures the requests a brain makes and replays canned responses."""

    def __init__(self, response: Any = None, status_code: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response if response is not None else _reply("ok")
        self._status_code = status_code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self._response, str):
            return httpx.Response(self._status_code, text=self._response)
        return httpx.Response(self._status_code, json=self._response)

    @property
    def payload(self) -> dict[str, Any]:
        """The JSON body of the last request made."""
        return json.loads(self.requests[-1].content)


def _brain(handler: Any, **config_overrides: Any) -> OllamaBrain:
    config = LLMConfig(**config_overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaBrain(config, client=client)


async def _complete(brain: OllamaBrain, **kwargs: Any) -> Any:
    kwargs.setdefault("system", "You are Jarvis.")
    kwargs.setdefault("messages", [BrainMessage(role="user", content="hello")])
    try:
        return await brain.complete(**kwargs)
    finally:
        await brain.aclose()


class TestConfiguration:
    def test_defaults_point_at_a_local_model(self) -> None:
        config = LLMConfig()
        assert config.provider == "ollama"
        assert config.model == "gpt-oss:20b"
        assert config.base_url == "http://localhost:11434"

    def test_model_default_follows_the_provider(self) -> None:
        assert LLMConfig(provider="anthropic").model == "claude-opus-4-8"
        assert LLMConfig(provider="ollama").model == "gpt-oss:20b"

    def test_explicit_model_is_kept(self) -> None:
        assert LLMConfig(provider="ollama", model="llama3.1:8b").model == "llama3.1:8b"

    async def test_an_environment_proxy_is_never_used_for_a_local_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A proxy from the environment would black-hole local traffic."""
        monkeypatch.setenv("HTTP_PROXY", "http://corporate-proxy.invalid:3128")
        monkeypatch.setenv("ALL_PROXY", "http://corporate-proxy.invalid:3128")
        brain = OllamaBrain(LLMConfig())
        try:
            assert brain._client.trust_env is False
        finally:
            await brain.aclose()


class TestRequest:
    async def test_posts_to_the_local_ollama_chat_endpoint(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder))
        request = recorder.requests[-1]
        assert request.method == "POST"
        assert str(request.url) == "http://localhost:11434/api/chat"

    async def test_base_url_is_configurable(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder, base_url="http://192.168.1.5:11434/"))
        assert str(recorder.requests[-1].url) == "http://192.168.1.5:11434/api/chat"

    async def test_configured_model_is_the_one_asked_for(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder))
        assert recorder.payload["model"] == "gpt-oss:20b"

    async def test_system_prompt_and_history_are_passed_through(self) -> None:
        recorder = Recorder()
        history = [
            BrainMessage(role="user", content="organise my downloads"),
            BrainMessage(role="assistant", content="Done — 12 files moved."),
            BrainMessage(role="user", content="now empty the trash"),
        ]
        await _complete(
            _brain(recorder), system="JARVIS SYSTEM PROMPT", messages=history
        )
        messages = recorder.payload["messages"]
        # The system prompt leads, then the conversation in order.
        assert messages[0] == {"role": "system", "content": "JARVIS SYSTEM PROMPT"}
        assert [m["role"] for m in messages[1:]] == ["user", "assistant", "user"]
        assert messages[3]["content"] == "now empty the trash"

    async def test_streaming_is_off_so_one_response_arrives(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder))
        assert recorder.payload["stream"] is False

    async def test_token_limits_are_sent_as_options(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder, max_tokens=1234, context_window=4096))
        assert recorder.payload["options"]["num_predict"] == 1234
        assert recorder.payload["options"]["num_ctx"] == 4096

    async def test_optional_knobs_are_omitted_unless_set(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder))
        assert "temperature" not in recorder.payload["options"]
        assert "think" not in recorder.payload
        assert "format" not in recorder.payload

    async def test_json_mode_constrains_decoding_to_json(self) -> None:
        """Planning asks for JSON, so Ollama must enforce it while decoding.

        Prompting alone is not enough: a long plan is precisely where the
        model drops a comma, and the reply is unparseable a retry later.
        """
        recorder = Recorder()
        await _complete(_brain(recorder), json_mode=True)
        assert recorder.payload["format"] == "json"

    async def test_json_mode_leaves_thinking_alone(self) -> None:
        """JSON mode must not switch reasoning off as well.

        Measured on gpt-oss:20b: format=json alone returns a full reply,
        while format=json plus think=false returns an empty one - a harmony
        model needs its analysis channel to reach a final answer.
        """
        recorder = Recorder()
        await _complete(_brain(recorder), json_mode=True)
        assert "think" not in recorder.payload

    async def test_optional_knobs_are_sent_when_set(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder, temperature=0.2, think=False))
        assert recorder.payload["options"]["temperature"] == 0.2
        assert recorder.payload["think"] is False


class TestToolSchemas:
    async def test_tool_schemas_are_converted_to_ollama_functions(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder), tools=[WRITE_FILE_SCHEMA])
        tools = recorder.payload["tools"]
        assert tools == [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write text to a file in the workspace.",
                    # The JSON Schema is passed through untouched — there is
                    # only ever one definition of a tool's arguments.
                    "parameters": WRITE_FILE_SCHEMA["input_schema"],
                },
            }
        ]

    async def test_every_tool_is_forwarded(self) -> None:
        recorder = Recorder()
        schemas = [
            {"name": f"tool_{n}", "description": "d", "input_schema": {"type": "object"}}
            for n in range(5)
        ]
        await _complete(_brain(recorder), tools=schemas)
        sent = [t["function"]["name"] for t in recorder.payload["tools"]]
        assert sent == [f"tool_{n}" for n in range(5)]

    async def test_tools_already_in_ollama_shape_pass_through(self) -> None:
        recorder = Recorder()
        native = {
            "type": "function",
            "function": {"name": "n", "description": "d", "parameters": {}},
        }
        await _complete(_brain(recorder), tools=[native])
        assert recorder.payload["tools"] == [native]

    async def test_no_tools_key_when_there_are_no_tools(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder))
        assert "tools" not in recorder.payload

    async def test_schema_without_properties_still_produces_an_object(self) -> None:
        recorder = Recorder()
        await _complete(_brain(recorder), tools=[{"name": "ping", "description": "d"}])
        parameters = recorder.payload["tools"][0]["function"]["parameters"]
        assert parameters == {"type": "object", "properties": {}}


class TestResponseParsing:
    async def test_plain_text_reply(self) -> None:
        response = await _complete(_brain(Recorder(_reply("All done."))))
        assert response.text == "All done."
        assert response.tool_calls == []
        assert response.stop_reason == "end_turn"
        assert response.refused is False

    async def test_tool_calls_become_tool_call_models(self) -> None:
        body = _reply(
            "",
            tool_calls=[
                {
                    "function": {
                        "name": "write_file",
                        "arguments": {"path": "notes.txt", "content": "hi"},
                    }
                }
            ],
        )
        response = await _complete(_brain(Recorder(body)))
        assert response.tool_calls == [
            ToolCall(tool="write_file", arguments={"path": "notes.txt", "content": "hi"})
        ]
        assert isinstance(response.tool_calls[0], ToolCall)
        assert response.stop_reason == "tool_use"

    async def test_several_tool_calls_keep_their_order(self) -> None:
        body = _reply(
            "",
            tool_calls=[
                {"function": {"name": "read_file", "arguments": {"path": "a"}}},
                {"function": {"name": "write_file", "arguments": {"path": "b"}}},
            ],
        )
        response = await _complete(_brain(Recorder(body)))
        assert [c.tool for c in response.tool_calls] == ["read_file", "write_file"]

    async def test_string_encoded_arguments_are_parsed(self) -> None:
        """Some models emit the arguments as a JSON string, not an object."""
        body = _reply(
            "",
            tool_calls=[
                {"function": {"name": "write_file", "arguments": '{"path": "x.txt"}'}}
            ],
        )
        response = await _complete(_brain(Recorder(body)))
        assert response.tool_calls[0].arguments == {"path": "x.txt"}

    async def test_unparseable_arguments_degrade_to_empty(self) -> None:
        body = _reply(
            "", tool_calls=[{"function": {"name": "ping", "arguments": "not json"}}]
        )
        response = await _complete(_brain(Recorder(body)))
        assert response.tool_calls == [ToolCall(tool="ping", arguments={})]

    async def test_malformed_tool_call_entries_are_skipped(self) -> None:
        body = _reply(
            "",
            tool_calls=[
                "junk",
                {"function": {"arguments": {}}},  # no name
                {"function": {"name": "ping", "arguments": {}}},
            ],
        )
        response = await _complete(_brain(Recorder(body)))
        assert [c.tool for c in response.tool_calls] == ["ping"]

    async def test_thinking_blocks_are_stripped_from_the_answer(self) -> None:
        """The Planner parses this text as JSON — reasoning must not leak in."""
        body = _reply('<think>The user wants {a plan}</think>\n{"goal": "x"}')
        response = await _complete(_brain(Recorder(body)))
        assert response.text == '{"goal": "x"}'
        assert json.loads(response.text) == {"goal": "x"}

    async def test_unterminated_thinking_block_is_stripped(self) -> None:
        body = _reply("Answer first.\n<think>cut off mid-thought")
        response = await _complete(_brain(Recorder(body)))
        assert response.text == "Answer first."

    async def test_length_stop_reason_is_translated(self) -> None:
        response = await _complete(_brain(Recorder(_reply("...", done_reason="length"))))
        assert response.stop_reason == "max_tokens"

    async def test_a_local_model_never_reports_a_refusal(self) -> None:
        response = await _complete(_brain(Recorder(_reply("no", done_reason="stop"))))
        assert response.refused is False

    async def test_missing_message_is_a_brain_error(self) -> None:
        recorder = Recorder({"model": "gpt-oss:20b", "done": True})
        with pytest.raises(BrainError, match="no message content"):
            await _complete(_brain(recorder))


class TestErrorHandling:
    """Every failure must arrive as a BrainError the user can act on."""

    async def test_connection_refused_names_the_endpoint_and_the_fix(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with pytest.raises(BrainError) as exc_info:
            await _complete(_brain(refuse))
        message = str(exc_info.value)
        assert "http://localhost:11434" in message
        assert "ollama serve" in message

    async def test_timeout_suggests_raising_the_limit(self) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        with pytest.raises(BrainError, match="did not answer within"):
            await _complete(_brain(timeout))

    async def test_missing_model_tells_you_to_pull_it(self) -> None:
        recorder = Recorder(
            {"error": "model 'gpt-oss:20b' not found, try pulling it first"}, 404
        )
        with pytest.raises(BrainError, match="ollama pull gpt-oss:20b"):
            await _complete(_brain(recorder))

    async def test_model_without_tool_support_is_explained(self) -> None:
        recorder = Recorder({"error": "model does not support tools"}, 400)
        with pytest.raises(BrainError, match="does not support tool calling"):
            await _complete(_brain(recorder), tools=[WRITE_FILE_SCHEMA])

    async def test_server_error_is_reported_with_its_detail(self) -> None:
        recorder = Recorder({"error": "out of memory"}, 500)
        with pytest.raises(BrainError, match="out of memory"):
            await _complete(_brain(recorder))

    async def test_non_json_body_points_at_the_base_url(self) -> None:
        recorder = Recorder("<html>not ollama</html>")
        with pytest.raises(BrainError, match="llm.base_url"):
            await _complete(_brain(recorder))

    async def test_error_field_in_a_200_response_is_raised(self) -> None:
        recorder = Recorder({"error": "something went sideways"})
        with pytest.raises(BrainError, match="something went sideways"):
            await _complete(_brain(recorder))


class TestListModels:
    async def test_lists_downloaded_models(self) -> None:
        recorder = Recorder(
            {"models": [{"name": "gpt-oss:20b"}, {"name": "llama3.1:8b"}]}
        )
        brain = _brain(recorder)
        try:
            assert await brain.list_models() == ["gpt-oss:20b", "llama3.1:8b"]
        finally:
            await brain.aclose()
        assert str(recorder.requests[-1].url) == "http://localhost:11434/api/tags"

    async def test_unreachable_server_is_a_brain_error(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        brain = _brain(refuse)
        try:
            with pytest.raises(BrainError, match="Could not reach Ollama"):
                await brain.list_models()
        finally:
            await brain.aclose()


class TestAgainstARealOllama:
    """Runs only where an Ollama server is actually listening.

    Skipped in CI and on machines without Ollama; on the developer's own
    box this is the test that proves the configured model really answers.
    """

    async def test_local_server_answers_with_the_configured_model(self) -> None:
        config = LLMConfig()
        brain = OllamaBrain(config)
        try:
            try:
                models = await brain.list_models()
            except BrainError as exc:
                pytest.skip(f"no local Ollama server: {exc}")
            assert config.model in models, (
                f"{config.model} is not downloaded — run: ollama pull {config.model}"
            )
            response = await brain.complete(
                system="You are a test fixture. Answer with one word.",
                messages=[BrainMessage(role="user", content="Say the word: ready")],
            )
            assert response.text
            assert "<think>" not in response.text
        finally:
            await brain.aclose()
