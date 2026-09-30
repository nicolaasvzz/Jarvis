"""Shared test doubles."""

from __future__ import annotations

from typing import Any

from jarvis.brain.base import BrainMessage, BrainResponse


class ScriptedBrain:
    """A Brain that replays canned responses in order.

    Each entry may be a plain string (returned as text) or a ready
    :class:`BrainResponse`. The calls it received are recorded for
    assertions.
    """

    def __init__(self, responses: list[str | BrainResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[BrainMessage],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> BrainResponse:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "json_mode": json_mode,
            }
        )
        if not self._responses:
            return BrainResponse(text="(no scripted response left)")
        item = self._responses.pop(0)
        if isinstance(item, BrainResponse):
            return item
        return BrainResponse(text=item)
