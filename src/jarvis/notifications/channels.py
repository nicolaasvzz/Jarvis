"""Notification delivery channels.

A channel is anything that can ``send`` a notification somewhere. Keeping the
interface to one coroutine means adding a real phone-push or email channel
later is a self-contained change — the service and every producer stay
untouched.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from jarvis.logging import get_logger

if TYPE_CHECKING:
    from jarvis.notifications.service import Notification

_log = get_logger(__name__)


@runtime_checkable
class NotificationChannel(Protocol):
    """A destination a notification can be delivered to."""

    async def send(self, notification: Notification) -> None: ...


class LogChannel:
    """Writes every notification to the structured log. Always safe to use."""

    async def send(self, notification: Notification) -> None:
        _log.info(
            notification.message,
            extra={
                "notification": notification.type,
                "task_id": notification.task_id,
            },
        )


class InMemoryChannel:
    """Collects notifications in a list and offers a live async stream.

    Used by tests and by the API's server-sent-events feed. Each subscriber
    gets its own :class:`asyncio.Queue`, so a slow consumer never blocks
    delivery to others.
    """

    def __init__(self) -> None:
        self.received: list[Notification] = []
        self._queues: set[asyncio.Queue[Notification]] = set()

    async def send(self, notification: Notification) -> None:
        self.received.append(notification)
        for queue in list(self._queues):
            queue.put_nowait(notification)

    def stream(self) -> _Subscription:
        """Return an async iterator of notifications sent from now on."""
        queue: asyncio.Queue[Notification] = asyncio.Queue()
        self._queues.add(queue)
        return _Subscription(queue, lambda: self._queues.discard(queue))


class _Subscription:
    """Async iterator over a queue that cleans itself up on exit."""

    def __init__(self, queue: asyncio.Queue[Notification], close: object) -> None:
        self._queue = queue
        self._close = close

    def __aiter__(self) -> _Subscription:
        return self

    async def __anext__(self) -> Notification:
        return await self._queue.get()

    def __enter__(self) -> _Subscription:
        return self

    def __exit__(self, *_: object) -> None:
        self._close()  # type: ignore[operator]
