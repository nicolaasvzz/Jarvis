"""Push-only notifications via an ntfy-compatible server.

A :class:`NotificationChannel` that POSTs each notification to
``<server>/<topic>``. Subscribe your phone to that topic in the ntfy app
(ntfy.sh works with no account) and you get native push notifications with
no bot, no LAN, and only outbound HTTPS.

Delivery failures are swallowed and logged — a flaky push server must never
interfere with task execution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.config.schema import PushConfig
from jarvis.logging import get_logger
from jarvis.phone.transport import HttpTransport, TransportError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.notifications.service import Notification

_log = get_logger(__name__)

# ntfy priority + a friendly tag per notification type.
_STYLES: dict[str, tuple[str, str]] = {
    "task.started": ("default", "rocket"),
    "task.completed": ("default", "white_check_mark"),
    "task.failed": ("high", "x"),
    "task.cancelled": ("low", "no_entry"),
    "approval.required": ("high", "warning"),
    "error": ("high", "rotating_light"),
}


class PushChannel:
    """Delivers notifications to an ntfy topic over HTTPS."""

    def __init__(
        self,
        config: PushConfig,
        transport: HttpTransport,
        *,
        auth_token: str | None = None,
    ) -> None:
        if not config.topic:
            raise ValueError("push.topic must be set when push is enabled.")
        self._url = f"{config.server.rstrip('/')}/{config.topic}"
        self._transport = transport
        self._auth_token = auth_token

    async def send(self, notification: Notification) -> None:
        priority, tag = _STYLES.get(notification.type, ("default", "bell"))
        headers = {
            # HTTP headers are ASCII only, so no typographic punctuation here:
            # a stray "·" fails the whole send before it leaves the machine.
            "Title": f"Jarvis: {notification.type}",
            "Priority": priority,
            "Tags": tag,
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        try:
            response = await self._transport.request(
                "POST",
                self._url,
                data=notification.message.encode("utf-8"),
                headers=headers,
            )
            if not response.ok:
                _log.warning(
                    "push server rejected notification",
                    extra={"status": response.status_code},
                )
        except TransportError as exc:
            _log.warning("push delivery failed", extra={"error": str(exc)})
