"""The Telegram remote-control bridge.

Long-polls Telegram for the owner's messages and turns them into actions on
the running :class:`~jarvis.agent.orchestrator.Orchestrator`; pushes task
notifications back, rendering approval requests as tap-able Allow / Deny
buttons. Only ``owner_chat_id`` may issue commands. All traffic is outbound
HTTPS to ``api.telegram.org`` — no inbound connection, no LAN, no open port.

The bridge is also a :class:`~jarvis.notifications.channels.NotificationChannel`
(``send``), so wiring it into the notification service is all it takes to get
push. Everything runs over the injected
:class:`~jarvis.phone.transport.HttpTransport`, so it is fully testable with a
fake transport.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from jarvis.config.schema import TelegramConfig
from jarvis.core.models import ApprovalDecision
from jarvis.logging import get_logger, log_context
from jarvis.phone.transport import HttpTransport, TransportError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.agent.orchestrator import Orchestrator
    from jarvis.notifications.service import Notification
    from jarvis.security.permissions import PermissionPolicy
    from jarvis.tools.registry import ToolRegistry

_log = get_logger(__name__)

_HELP = (
    "🤖 *Jarvis*\n"
    "Send me anything and I'll plan and do it.\n\n"
    "Commands:\n"
    "• /status — recent tasks\n"
    "• /task <id> — task detail\n"
    "• /cancel <id> — cancel a task\n"
    "• /approve [id] · /deny [id] — decide a pending action\n"
    "• /approvals — list pending approvals\n"
    "• /tools — what I can do here\n"
    "• /whoami — show your chat id\n"
    "• /help — this message"
)

_NOTIFY_ICON = {
    "task.started": "🚀",
    "task.completed": "✅",
    "task.failed": "❌",
    "task.cancelled": "🚫",
    "approval.required": "⚠️",
    "error": "🚨",
}


class TelegramBridge:
    """A Telegram bot that gives full remote control of Jarvis from a phone."""

    def __init__(
        self,
        *,
        token: SecretStr,
        config: TelegramConfig,
        orchestrator: Orchestrator,
        policy: PermissionPolicy,
        registry: ToolRegistry,
        transport: HttpTransport,
    ) -> None:
        secret = token.get_secret_value()
        if not secret:
            raise ValueError("Telegram bot token is empty.")
        self._base = f"{config.api_base.rstrip('/')}/bot{secret}"
        self._config = config
        self._orchestrator = orchestrator
        self._policy = policy
        self._registry = registry
        self._transport = transport
        self._offset = 0

    # -- outbound (Jarvis → phone) ---------------------------------------
    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._transport.request(
            "POST", f"{self._base}/{method}", json=payload
        )
        body = response.json() or {}
        if not response.ok or not body.get("ok", False):
            raise TransportError(
                f"Telegram {method} failed: HTTP {response.status_code} {body}"
            )
        result: dict[str, Any] = body.get("result", {})
        return result

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in buttons]
                ]
            }
        try:
            await self._call("sendMessage", payload)
        except TransportError as exc:
            _log.warning("telegram send failed", extra={"error": str(exc)})

    async def _answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            await self._call(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": text},
            )
        except TransportError as exc:
            _log.warning("telegram answer failed", extra={"error": str(exc)})

    async def send(self, notification: Notification) -> None:
        """NotificationChannel: push a notification to the owner chat."""
        owner = self._config.owner_chat_id
        if owner is None:
            return
        icon = _NOTIFY_ICON.get(notification.type, "•")
        text = f"{icon} {notification.message}"
        buttons: list[tuple[str, str]] | None = None
        if notification.type == "approval.required":
            approval_id = notification.data.get("approval_id")
            if approval_id:
                buttons = [
                    ("✅ Allow", f"approve:{approval_id}"),
                    ("⛔ Deny", f"deny:{approval_id}"),
                ]
        await self.send_message(owner, text, buttons=buttons)

    # -- inbound (phone → Jarvis) ----------------------------------------
    def _authorized(self, chat_id: int) -> bool:
        return (
            self._config.owner_chat_id is not None
            and chat_id == self._config.owner_chat_id
        )

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text:
            return
        if not self._authorized(chat_id):
            await self.send_message(
                chat_id,
                "You're not authorised to control this Jarvis.\n"
                f"Your chat id is `{chat_id}` — set it as "
                "`telegram.owner_chat_id` to enable control.",
            )
            _log.warning("unauthorized telegram message", extra={"chat_id": chat_id})
            return
        await self._handle_command(chat_id, text)

    async def _handle_command(self, chat_id: int, text: str) -> None:
        command, _, argument = text.partition(" ")
        argument = argument.strip()
        command = command.lower()

        if command in {"/start", "/help"}:
            await self.send_message(chat_id, _HELP)
        elif command == "/whoami":
            await self.send_message(chat_id, f"Your chat id is `{chat_id}`.")
        elif command == "/status":
            await self.send_message(chat_id, self._status_text())
        elif command == "/task":
            await self.send_message(chat_id, self._task_text(argument))
        elif command == "/cancel":
            await self._cancel(chat_id, argument)
        elif command == "/approvals":
            await self._list_approvals(chat_id)
        elif command in {"/approve", "/deny"}:
            decision = (
                ApprovalDecision.ALLOW
                if command == "/approve"
                else ApprovalDecision.DENY
            )
            await self._decide(chat_id, argument, decision)
        elif command == "/tools":
            await self.send_message(
                chat_id, "I can use:\n" + ", ".join(self._registry.names())
            )
        else:
            await self._submit(chat_id, text)

    async def _submit(self, chat_id: int, request: str) -> None:
        task = await self._orchestrator.submit(request)
        await self.send_message(
            chat_id, f"🧠 On it — planning now. (`{task.id}`)"
        )

    def _status_text(self) -> str:
        tasks = self._orchestrator.all_tasks()[:10]
        if not tasks:
            return "No tasks yet."
        lines = [
            f"`{t.id}` [{t.status.value}] {t.request[:48]}" for t in tasks
        ]
        return "Recent tasks:\n" + "\n".join(lines)

    def _task_text(self, task_id: str) -> str:
        if not task_id:
            return "Usage: /task <id>"
        task = self._orchestrator.get(task_id)
        if task is None:
            return f"No task `{task_id}`."
        lines = [f"`{task.id}` — *{task.status.value}*", task.request]
        if task.plan:
            for step in task.plan.steps:
                lines.append(f"  • [{step.status.value}] {step.description}")
        if task.result:
            lines.append(f"\nResult: {task.result}")
        if task.error:
            lines.append(f"\nError: {task.error}")
        return "\n".join(lines)

    async def _cancel(self, chat_id: int, task_id: str) -> None:
        if not task_id:
            await self.send_message(chat_id, "Usage: /cancel <id>")
            return
        cancelled = await self._orchestrator.cancel(task_id)
        await self.send_message(
            chat_id,
            f"🚫 Cancelled `{task_id}`."
            if cancelled
            else f"`{task_id}` is not running.",
        )

    async def _list_approvals(self, chat_id: int) -> None:
        pending = self._policy.pending()
        if not pending:
            await self.send_message(chat_id, "No pending approvals.")
            return
        for request in pending:
            await self.send_message(
                chat_id,
                f"⚠️ `{request.tool}` {request.arguments}\n{request.reason}",
                buttons=[
                    ("✅ Allow", f"approve:{request.id}"),
                    ("⛔ Deny", f"deny:{request.id}"),
                ],
            )

    async def _decide(
        self, chat_id: int, approval_id: str, decision: ApprovalDecision
    ) -> None:
        if not approval_id:
            pending = self._policy.pending()
            if not pending:
                await self.send_message(chat_id, "No pending approvals.")
                return
            approval_id = pending[0].id
        message = self._resolve(approval_id, decision)
        await self.send_message(chat_id, message)

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id", "")
        chat_id = (
            callback.get("message", {}).get("chat", {}).get("id")
            if callback.get("message")
            else None
        )
        if chat_id is None or not self._authorized(chat_id):
            await self._answer_callback(callback_id, "Not authorised.")
            return
        action, _, approval_id = (callback.get("data") or "").partition(":")
        decision = (
            ApprovalDecision.ALLOW if action == "approve" else ApprovalDecision.DENY
        )
        message = self._resolve(approval_id, decision)
        await self._answer_callback(callback_id, decision.value)
        await self.send_message(chat_id, message)

    def _resolve(self, approval_id: str, decision: ApprovalDecision) -> str:
        try:
            self._policy.resolve(approval_id, decision)
        except KeyError:
            return f"No pending approval `{approval_id}`."
        except ValueError:
            return f"Approval `{approval_id}` was already decided."
        verb = "Approved ✅" if decision is ApprovalDecision.ALLOW else "Denied ⛔"
        return f"{verb} `{approval_id}`."

    # -- the loop ---------------------------------------------------------
    async def _get_updates(self) -> list[dict[str, Any]]:
        response = await self._transport.request(
            "POST",
            f"{self._base}/getUpdates",
            json={"offset": self._offset, "timeout": self._config.poll_timeout},
        )
        body = response.json() or {}
        if not response.ok or not body.get("ok", False):
            raise TransportError(f"getUpdates failed: HTTP {response.status_code}")
        updates: list[dict[str, Any]] = body.get("result", [])
        return updates

    async def announce_online(self) -> None:
        if self._config.owner_chat_id is not None:
            await self.send_message(
                self._config.owner_chat_id, "🤖 Jarvis is online and ready."
            )

    async def _log_identity(self) -> None:
        """Say which bot the token actually authenticated as.

        Polling starts whether or not the token is any good, so on its own
        "polling started" cannot be told apart from a bridge that will never
        receive anything. One getMe at startup settles it in the log.
        """
        try:
            me = await self._call("getMe", {})
        except TransportError as exc:
            _log.error(
                "Telegram would not accept the bot token - check "
                "TELEGRAM_BOT_TOKEN in .env",
                extra={"error": str(exc)},
            )
            return
        username = me.get("username")
        _log.info(
            "telegram bridge connected",
            extra={"bot": f"@{username}" if username else "unknown"},
        )

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Long-poll and dispatch until ``stop_event`` is set or cancelled."""
        stop_event = stop_event or asyncio.Event()
        await self._log_identity()
        try:
            await self.announce_online()
        except TransportError as exc:
            # A greeting that will not send is not a reason to refuse to run:
            # the poll loop below reports and retries its own failures.
            _log.warning(
                "could not send the startup greeting",
                extra={"error": str(exc)},
            )
        _log.info("telegram bridge polling started")
        backoff = 1.0
        while not stop_event.is_set():
            # Yield to the event loop every iteration. Real getUpdates blocks
            # server-side for poll_timeout seconds (naturally cooperative), but
            # this guards against a busy-spin if the transport ever returns
            # instantly (e.g. poll_timeout=0 or a fake transport in tests).
            await asyncio.sleep(0)
            try:
                updates = await self._get_updates()
                backoff = 1.0
            except TransportError as exc:
                _log.warning(
                    "telegram poll error; backing off",
                    extra={"error": str(exc), "backoff_s": backoff},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            for update in updates:
                self._offset = int(update.get("update_id", self._offset)) + 1
                with log_context(update_id=update.get("update_id")):
                    try:
                        await self.handle_update(update)
                    except Exception:  # noqa: BLE001 - one bad update must not stop us
                        _log.exception("failed handling telegram update")
        _log.info("telegram bridge polling stopped")
