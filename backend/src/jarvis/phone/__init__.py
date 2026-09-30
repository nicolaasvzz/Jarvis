"""Phone connectivity: push notifications and full remote control.

Two independent ways to reach Jarvis from your phone, both using only
*outbound* HTTPS — so neither needs wifi, a LAN, an open port, or an exposed
server. They work over any internet the laptop has, including a USB/phone
tether.

* :class:`~jarvis.phone.telegram.TelegramBridge` — a Telegram bot that
  long-polls for your commands (send a task, approve/deny dangerous actions
  with buttons, check status, cancel) and pushes results and notifications
  back. This is the "control Jarvis totally from my phone" path.
* :class:`~jarvis.notifications.push.PushChannel` — push-only notifications
  to an ntfy-compatible server (no bot, no account).

Both sit behind :class:`~jarvis.phone.transport.HttpTransport`, so the whole
bridge is unit-tested here with a fake transport, no real Telegram account
required.
"""

from jarvis.phone.telegram import TelegramBridge
from jarvis.phone.transport import HttpResponse, HttpTransport, HttpxTransport

__all__ = ["HttpResponse", "HttpTransport", "HttpxTransport", "TelegramBridge"]
