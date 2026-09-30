"""The tool registry: a name→tool lookup with duplicate protection."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from jarvis.core.errors import ToolNotFound
from jarvis.tools.base import Tool


class ToolRegistry:
    """Holds every registered tool and answers lookups by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"A tool named {tool.name!r} is already registered.")
        self._tools[tool.name] = tool

    def register_all(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(f"No tool named {name!r}.") from None

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """All tool definitions, for handing to the LLM."""
        return [self._tools[name].schema() for name in self.names()]

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)
