"""The Brain protocol and its request/response types."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from jarvis.core.models import ToolCall


class BrainMessage(BaseModel):
    """One turn of conversation handed to the model."""

    role: str  # "user" or "assistant"
    content: str


class BrainResponse(BaseModel):
    """What the model decided: prose, tool calls, and why it stopped."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = "end_turn"
    raw: dict[str, Any] | None = None

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


@runtime_checkable
class Brain(Protocol):
    """Anything that can complete a conversation.

    ``tools`` is a list of Jarvis tool schemas, as produced by
    :meth:`jarvis.tools.base.Tool.schema`; when provided the model may
    answer with tool calls instead of (or in addition to) text. Each
    implementation is responsible for translating them into whatever shape
    its provider expects, so callers never need to know which model is
    behind the protocol.
    """

    async def complete(
        self,
        *,
        system: str,
        messages: list[BrainMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> BrainResponse: ...
