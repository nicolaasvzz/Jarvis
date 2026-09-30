"""The Notification System service.

Subscribes to the event bus, maps events to user-facing notifications,
keeps a bounded history, and delivers each notification to every registered
channel. The event→notification mapping lives here so producers only ever
publish neutral events and never worry about presentation.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.events import Event, EventBus, EventType
from jarvis.logging import get_logger
from jarvis.notifications.channels import NotificationChannel

_log = get_logger(__name__)

# Events that are worth interrupting the user for. Fine-grained progress
# events are still recorded in history but are not pushed as notifications.
_NOTIFY_EVENTS = frozenset(
    {
        EventType.TASK_STARTED,
        EventType.TASK_COMPLETED,
        EventType.TASK_FAILED,
        EventType.TASK_CANCELLED,
        EventType.APPROVAL_REQUIRED,
        EventType.ERROR,
    }
)


class Notification(BaseModel):
    """A user-facing message derived from a lifecycle event."""

    type: str
    message: str
    task_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class NotificationService:
    """Bridges the event bus to notification channels, with history."""

    def __init__(
        self,
        bus: EventBus,
        channels: list[NotificationChannel] | None = None,
        *,
        history_limit: int = 500,
    ) -> None:
        self._bus = bus
        self._channels: list[NotificationChannel] = list(channels or [])
        self._history: deque[Notification] = deque(maxlen=history_limit)
        self._unsubscribe = bus.subscribe(self._on_event)

    def add_channel(self, channel: NotificationChannel) -> None:
        self._channels.append(channel)

    def history(self, limit: int | None = None) -> list[Notification]:
        """Return recent notifications, newest last."""
        items = list(self._history)
        return items[-limit:] if limit is not None else items

    async def notify(self, notification: Notification) -> None:
        """Record a notification and deliver it to every channel."""
        self._history.append(notification)
        if not self._channels:
            return
        results = await asyncio.gather(
            *(channel.send(notification) for channel in self._channels),
            return_exceptions=True,
        )
        for outcome in results:
            if isinstance(outcome, Exception):
                _log.warning(
                    "notification channel failed", extra={"error": str(outcome)}
                )

    async def _on_event(self, event: Event) -> None:
        if event.type not in _NOTIFY_EVENTS:
            return
        await self.notify(
            Notification(
                type=event.type.value,
                message=event.message,
                task_id=event.task_id,
                data=event.data,
            )
        )

    def close(self) -> None:
        self._unsubscribe()
