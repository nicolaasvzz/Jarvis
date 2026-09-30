"""Tests for the Notification System."""

from __future__ import annotations

from jarvis.core.events import EventBus, EventType
from jarvis.notifications import InMemoryChannel, NotificationService


async def test_notable_events_become_notifications() -> None:
    bus = EventBus()
    channel = InMemoryChannel()
    service = NotificationService(bus, [channel])

    await bus.emit(EventType.TASK_STARTED, "Task started", task_id="task-1")
    await bus.emit(EventType.TASK_COMPLETED, "Task done", task_id="task-1")

    messages = [n.message for n in channel.received]
    assert messages == ["Task started", "Task done"]
    assert service.history()[-1].task_id == "task-1"


async def test_progress_events_are_not_pushed_but_ignored() -> None:
    bus = EventBus()
    channel = InMemoryChannel()
    NotificationService(bus, [channel])

    await bus.emit(EventType.STEP_STARTED, "running a step")
    assert channel.received == []  # step-level noise is not a notification


async def test_channel_failure_does_not_break_delivery() -> None:
    bus = EventBus()

    class Broken:
        async def send(self, notification: object) -> None:
            raise RuntimeError("channel down")

    good = InMemoryChannel()
    NotificationService(bus, [Broken(), good])
    await bus.emit(EventType.TASK_FAILED, "it failed")
    assert [n.message for n in good.received] == ["it failed"]


async def test_history_is_bounded() -> None:
    bus = EventBus()
    service = NotificationService(bus, [], history_limit=3)
    for i in range(5):
        await bus.emit(EventType.ERROR, f"error {i}")
    history = service.history()
    assert len(history) == 3
    assert [n.message for n in history] == ["error 2", "error 3", "error 4"]
