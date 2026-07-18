"""A small async event bus for lifecycle notifications.

Modules publish events (task started, step finished, approval required, ...)
without knowing who listens; the Notification System, the API's live-progress
stream, and the logger all subscribe. This keeps producers decoupled from
consumers — exactly the "explain what it is doing" requirement, wired as
publish/subscribe.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from jarvis.logging import get_logger

_log = get_logger(__name__)


class EventType(StrEnum):
    TASK_CREATED = "task.created"
    TASK_PLANNING = "task.planning"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    STEP_RETRYING = "step.retrying"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_RESOLVED = "approval.resolved"
    ERROR = "error"


class Event(BaseModel):
    """Something that happened, with a type, a human message, and details."""

    type: EventType
    message: str
    task_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


Subscriber = Callable[[Event], Awaitable[None]]


class EventBus:
    """In-process async publish/subscribe hub.

    Subscribers are coroutine callbacks. Publishing awaits every subscriber
    but isolates failures: one subscriber raising never stops the others or
    the publisher, it is only logged. This matters because a flaky
    notification channel must not crash task execution.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Register ``subscriber``; returns a function that unsubscribes it."""
        self._subscribers.append(subscriber)

        def _unsubscribe() -> None:
            with _suppress_value_error():
                self._subscribers.remove(subscriber)

        return _unsubscribe

    async def publish(self, event: Event) -> None:
        """Deliver ``event`` to all subscribers, isolating their failures."""
        if not self._subscribers:
            return
        results = await asyncio.gather(
            *(sub(event) for sub in list(self._subscribers)),
            return_exceptions=True,
        )
        for outcome in results:
            if isinstance(outcome, Exception):
                _log.warning(
                    "event subscriber failed",
                    extra={"event": event.type.value, "error": str(outcome)},
                )

    async def emit(
        self,
        type: EventType,
        message: str,
        *,
        task_id: str | None = None,
        **data: Any,
    ) -> None:
        """Convenience wrapper: build an :class:`Event` and publish it."""
        await self.publish(
            Event(type=type, message=message, task_id=task_id, data=data)
        )


class _suppress_value_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, *_: object) -> bool:
        return exc_type is ValueError
