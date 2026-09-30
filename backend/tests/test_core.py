"""Tests for core domain types and the event bus."""

from __future__ import annotations

from jarvis.core import (
    Event,
    EventBus,
    EventType,
    Plan,
    PlanStep,
    RiskLevel,
    StepStatus,
    Task,
    TaskStatus,
    ToolResult,
    new_id,
)


def test_ids_are_prefixed_unique_and_sortable() -> None:
    a = new_id("task")
    b = new_id("task")
    assert a.startswith("task-")
    assert a != b


def test_task_touch_updates_status_and_timestamp() -> None:
    task = Task(request="do a thing")
    before = task.updated_at
    task.touch(TaskStatus.RUNNING)
    assert task.status is TaskStatus.RUNNING
    assert task.updated_at >= before
    assert not task.is_terminal
    task.touch(TaskStatus.COMPLETED)
    assert task.is_terminal


def test_plan_next_pending_walks_in_order() -> None:
    plan = Plan(
        task_id="task-1",
        goal="g",
        steps=[
            PlanStep(description="one", status=StepStatus.COMPLETED),
            PlanStep(description="two"),
            PlanStep(description="three"),
        ],
    )
    assert plan.next_pending() is not None
    assert plan.next_pending().description == "two"


def test_tool_result_helpers() -> None:
    from datetime import UTC, datetime

    started = datetime.now(tz=UTC)
    ok = ToolResult.success("t", output=42, started_at=started)
    assert ok.ok and ok.output == 42
    bad = ToolResult.failure("t", error="nope", started_at=started)
    assert not bad.ok and bad.error == "nope"


def test_default_step_risk_is_safe() -> None:
    assert PlanStep(description="x").risk is RiskLevel.SAFE


async def test_event_bus_delivers_to_all_subscribers() -> None:
    bus = EventBus()
    received: list[str] = []

    async def one(event: Event) -> None:
        received.append(f"one:{event.message}")

    async def two(event: Event) -> None:
        received.append(f"two:{event.message}")

    bus.subscribe(one)
    unsubscribe = bus.subscribe(two)
    await bus.emit(EventType.TASK_STARTED, "hello")
    assert set(received) == {"one:hello", "two:hello"}

    unsubscribe()
    received.clear()
    await bus.emit(EventType.TASK_STARTED, "again")
    assert received == ["one:again"]


async def test_event_bus_isolates_subscriber_failures() -> None:
    bus = EventBus()
    delivered: list[str] = []

    async def bad(event: Event) -> None:
        raise RuntimeError("boom")

    async def good(event: Event) -> None:
        delivered.append(event.message)

    bus.subscribe(bad)
    bus.subscribe(good)
    await bus.emit(EventType.ERROR, "still delivered")  # must not raise
    assert delivered == ["still delivered"]
