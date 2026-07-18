"""Tests for the Tool base, registry, and the Tool Manager gateway."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.config.schema import SecurityConfig
from jarvis.core.errors import ToolError
from jarvis.core.events import EventBus, EventType
from jarvis.core.models import ApprovalDecision
from jarvis.security import PermissionPolicy
from jarvis.tools import Tool, ToolManager, ToolRegistry, tool


def test_tool_decorator_builds_schema_from_hints() -> None:
    @tool(risk_category="delete_files")
    def sample(path: str, force: bool = False) -> str:
        """Do a sample thing."""
        return path

    assert sample.name == "sample"
    assert sample.description == "Do a sample thing."
    assert sample.risk_category == "delete_files"
    schema = sample.schema()["input_schema"]
    assert schema["properties"]["path"] == {"type": "string"}
    assert schema["properties"]["force"] == {"type": "boolean"}
    assert schema["required"] == ["path"]  # force has a default


def test_registry_rejects_duplicates_and_looks_up() -> None:
    registry = ToolRegistry()

    @tool()
    def a() -> str:
        return "a"

    registry.register(a)
    assert registry.has("a")
    assert registry.get("a") is a
    with pytest.raises(ValueError):
        registry.register(Tool(name="a", description="dup", func=lambda: "x"))


def _manager(require: list[str] | None = None) -> tuple[ToolManager, EventBus, PermissionPolicy]:
    registry = ToolRegistry()
    policy = PermissionPolicy(SecurityConfig(require_confirmation=require or []))
    bus = EventBus()
    return ToolManager(registry, policy, bus), bus, policy


async def test_execute_success_returns_result() -> None:
    manager, _, _ = _manager()

    @tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    manager.registry.register(add)
    result = await manager.execute("add", {"a": 2, "b": 3})
    assert result.ok
    assert result.output == 5


async def test_unknown_tool_becomes_structured_failure() -> None:
    manager, _, _ = _manager()
    result = await manager.execute("missing", {})
    assert not result.ok
    assert "missing" in (result.error or "")


async def test_tool_exception_is_captured_not_raised() -> None:
    manager, _, _ = _manager()

    @tool()
    def boom() -> str:
        """Always fails."""
        raise ToolError("kaboom")

    manager.registry.register(boom)
    result = await manager.execute("boom", {})
    assert not result.ok
    assert "kaboom" in (result.error or "")


async def test_unexpected_exception_is_captured() -> None:
    manager, _, _ = _manager()

    @tool()
    def crash() -> str:
        """Raises a non-Jarvis error."""
        raise ValueError("unexpected")

    manager.registry.register(crash)
    result = await manager.execute("crash", {})
    assert not result.ok
    assert "ValueError" in (result.error or "")


async def test_dangerous_tool_waits_for_approval_and_runs_on_allow() -> None:
    manager, bus, policy = _manager(require=["delete_files"])
    events: list[str] = []

    async def record(event: object) -> None:
        events.append(event.type.value)  # type: ignore[attr-defined]

    bus.subscribe(record)

    @tool(risk_category="delete_files")
    def wipe() -> str:
        """Deletes something."""
        return "gone"

    manager.registry.register(wipe)

    async def run() -> object:
        return await manager.execute("wipe", {}, task_id="task-1")

    task = asyncio.create_task(run())
    # Wait until the approval request appears, then allow it.
    for _ in range(100):
        await asyncio.sleep(0)
        pending = policy.pending()
        if pending:
            policy.resolve(pending[0].id, ApprovalDecision.ALLOW)
            break
    result = await task
    assert result.ok  # type: ignore[union-attr]
    assert result.output == "gone"  # type: ignore[union-attr]
    assert EventType.APPROVAL_REQUIRED.value in events


async def test_dangerous_tool_denied_becomes_failure() -> None:
    manager, _, policy = _manager(require=["delete_files"])

    @tool(risk_category="delete_files")
    def wipe() -> str:
        """Deletes something."""
        return "gone"

    manager.registry.register(wipe)

    async def run() -> object:
        return await manager.execute("wipe", {}, task_id="task-1")

    task = asyncio.create_task(run())
    for _ in range(100):
        await asyncio.sleep(0)
        pending = policy.pending()
        if pending:
            policy.resolve(pending[0].id, ApprovalDecision.DENY)
            break
    result = await task
    assert not result.ok  # type: ignore[union-attr]


async def test_require_approval_false_bypasses_gate() -> None:
    manager, _, _ = _manager(require=["delete_files"])

    @tool(risk_category="delete_files")
    def wipe() -> str:
        """Deletes something."""
        return "gone"

    manager.registry.register(wipe)
    result = await manager.execute("wipe", {}, require_approval=False)
    assert result.ok
