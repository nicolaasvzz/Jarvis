"""Notification System.

Turns lifecycle events into user-facing notifications. A
:class:`~jarvis.notifications.service.NotificationService` subscribes to the
core :class:`~jarvis.core.events.EventBus`, keeps a rolling in-memory history
(so the phone can fetch what it missed), and fans each notification out to
any number of pluggable *channels*.

Channels implement a single ``send`` coroutine, so new delivery mechanisms
(phone push, email, webhook) can be added without touching producers. Two
channels ship today: :class:`LogChannel` (always on) and
:class:`InMemoryChannel` (used by tests and the API's live feed).
"""

from jarvis.notifications.channels import (
    InMemoryChannel,
    LogChannel,
    NotificationChannel,
)
from jarvis.notifications.service import Notification, NotificationService

__all__ = [
    "InMemoryChannel",
    "LogChannel",
    "Notification",
    "NotificationChannel",
    "NotificationService",
]
