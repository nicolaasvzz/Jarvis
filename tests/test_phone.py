"""Tests for phone connectivity: the push channel and the Telegram bridge.

Everything runs against a scripted fake HTTP transport, so these exercise
the real bridge and channel logic with no network and no Telegram account.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import SecretStr

from jarvis.agent import Orchestrator
from jarvis.config.schema import (
    AgentConfig,
    PushConfig,
    SecurityConfig,
    TelegramConfig,
)
from jarvis.core.events import EventBus
from jarvis.files import FileManager, build_file_tools
from jarvis.memory import MemoryStore
from jarvis.notifications import NotificationService, PushChannel
from jarvis.notifications.service import Notification
from jarvis.phone import TelegramBridge
from jarvis.phone.transport import HttpResponse
from jarvis.planner import Planner
from jarvis.security import PermissionPolicy
from jarvis.tools import ToolManager, ToolRegistry
from tests.helpers import ScriptedBrain


class FakeTransport:
    """Records outbound requests; replays queued responses for getUpdates."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        # Map of URL-suffix -> list of response bodies to return in order.
        self._update_batches: list[list[dict[str, Any]]] = []

    def queue_updates(self, updates: list[dict[str, Any]]) -> None:
        self._update_batches.append(updates)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.requests.append(
            {"method": method, "url": url, "json": json, "data": data, "headers": headers}
        )
        if url.endswith("/getUpdates"):
            batch = self._update_batches.pop(0) if self._update_batches else []
            return _ok({"result": batch})
        # sendMessage / answerCallbackQuery
        return _ok({"result": {}})

    def sent_messages(self) -> list[dict[str, Any]]:
        return [
            r["json"]
            for r in self.requests
            if r["url"].endswith("/sendMessage")
        ]

    def sent_texts(self) -> list[str]:
        return [m["text"] for m in self.sent_messages()]


def _ok(body: dict[str, Any]) -> HttpResponse:
    payload = {"ok": True, **body}
    return HttpResponse(status_code=200, text=json.dumps(payload))


# -- Push channel -------------------------------------------------------------


class PushTransport:
    def __init__(self, status: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status = status

    async def request(
        self, method: str, url: str, *, params=None, json=None, data=None, headers=None
    ) -> HttpResponse:
        self.calls.append({"url": url, "data": data, "headers": headers})
        return HttpResponse(status_code=self._status, text="")


async def test_push_channel_posts_to_ntfy_topic() -> None:
    transport = PushTransport()
    channel = PushChannel(
        PushConfig(enabled=True, server="https://ntfy.sh", topic="my-jarvis"),
        transport,
    )
    await channel.send(
        Notification(type="task.completed", message="All done!", task_id="task-1")
    )
    (call,) = transport.calls
    assert call["url"] == "https://ntfy.sh/my-jarvis"
    assert call["data"] == b"All done!"
    assert call["headers"]["Priority"] == "default"
    assert call["headers"]["Tags"] == "white_check_mark"


async def test_push_channel_uses_high_priority_for_failures() -> None:
    transport = PushTransport()
    channel = PushChannel(
        PushConfig(enabled=True, topic="t"), transport, auth_token="secret"
    )
    await channel.send(Notification(type="task.failed", message="boom"))
    call = transport.calls[0]
    assert call["headers"]["Priority"] == "high"
    assert call["headers"]["Authorization"] == "Bearer secret"


async def test_push_channel_requires_topic() -> None:
    with pytest.raises(ValueError):
        PushChannel(PushConfig(enabled=True, topic=None), PushTransport())


# -- Telegram bridge ----------------------------------------------------------


def build_bridge(
    tmp_path,
    transport: FakeTransport,
    *,
    owner_chat_id: int | None = 42,
    brain: ScriptedBrain | None = None,
) -> tuple[TelegramBridge, Orchestrator, PermissionPolicy]:
    fm = FileManager(tmp_path / "sandbox")
    registry = ToolRegistry()
    registry.register_all(build_file_tools(fm))
    policy = PermissionPolicy(SecurityConfig(require_confirmation=["delete_files"]))
    bus = EventBus()
    tools = ToolManager(registry, policy, bus)
    planner = Planner(brain or ScriptedBrain([]), registry, policy)
    memory = MemoryStore(tmp_path / "memory.db")
    orchestrator = Orchestrator(
        brain=brain or ScriptedBrain([]),
        planner=planner,
        tools=tools,
        memory=memory,
        bus=bus,
        config=AgentConfig(),
    )
    bridge = TelegramBridge(
        token=SecretStr("bot-token"),
        config=TelegramConfig(enabled=True, owner_chat_id=owner_chat_id),
        orchestrator=orchestrator,
        policy=policy,
        registry=registry,
        transport=transport,
    )
    # Wire the bridge as a notification channel so events reach the phone.
    NotificationService(bus, [bridge])
    return bridge, orchestrator, policy


def _message(chat_id: int, text: str, update_id: int = 1) -> dict[str, Any]:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


async def test_unauthorized_chat_is_refused_but_told_its_id(tmp_path) -> None:
    transport = FakeTransport()
    bridge, _, _ = build_bridge(tmp_path, transport, owner_chat_id=42)
    await bridge.handle_update(_message(999, "delete everything"))
    texts = transport.sent_texts()
    assert len(texts) == 1
    assert "not authorised" in texts[0].lower()
    assert "999" in texts[0]


async def test_help_command(tmp_path) -> None:
    transport = FakeTransport()
    bridge, _, _ = build_bridge(tmp_path, transport)
    await bridge.handle_update(_message(42, "/help"))
    assert "Commands:" in transport.sent_texts()[0]


async def test_plain_message_submits_a_task(tmp_path) -> None:
    transport = FakeTransport()
    plan = json.dumps(
        {
            "goal": "Write a file",
            "steps": [
                {
                    "description": "write",
                    "tool": "write_file",
                    "arguments": {"path": "hi.txt", "content": "hi"},
                }
            ],
        }
    )
    bridge, orchestrator, _ = build_bridge(
        tmp_path, transport, brain=ScriptedBrain([plan, "Wrote hi.txt."])
    )
    await bridge.handle_update(_message(42, "make a file called hi.txt"))
    # Immediate acknowledgement to the phone.
    assert any("planning" in t.lower() for t in transport.sent_texts())
    # The task actually ran.
    task = orchestrator.all_tasks()[0]
    await orchestrator.wait(task.id)
    # A completion notification was pushed to the owner.
    assert any("Wrote hi.txt" in t for t in transport.sent_texts())


async def test_approval_pushed_with_buttons_and_resolved_by_callback(tmp_path) -> None:
    transport = FakeTransport()
    plan = json.dumps(
        {
            "goal": "delete",
            "steps": [
                {
                    "description": "write",
                    "tool": "write_file",
                    "arguments": {"path": "v.txt", "content": "x"},
                },
                {
                    "description": "delete",
                    "tool": "delete_path",
                    "arguments": {"path": "v.txt"},
                },
            ],
        }
    )
    bridge, orchestrator, policy = build_bridge(
        tmp_path, transport, brain=ScriptedBrain([plan, "Deleted v.txt."])
    )
    await bridge.handle_update(_message(42, "delete v.txt"))

    # Wait for the approval-required message (with buttons) to be pushed.
    import asyncio

    approval_id = None
    for _ in range(300):
        await asyncio.sleep(0.01)
        for msg in transport.sent_messages():
            markup = msg.get("reply_markup")
            if markup:
                data = markup["inline_keyboard"][0][0]["callback_data"]
                approval_id = data.split(":", 1)[1]
                break
        if approval_id:
            break
    assert approval_id, "no approval buttons were pushed"

    # Simulate the user tapping "Allow".
    await bridge.handle_update(
        {
            "update_id": 2,
            "callback_query": {
                "id": "cb1",
                "data": f"approve:{approval_id}",
                "message": {"chat": {"id": 42}},
            },
        }
    )
    task = orchestrator.all_tasks()[0]
    await orchestrator.wait(task.id)
    from jarvis.core.models import TaskStatus

    assert task.status is TaskStatus.COMPLETED


async def test_callback_from_stranger_is_rejected(tmp_path) -> None:
    transport = FakeTransport()
    bridge, _, policy = build_bridge(tmp_path, transport, owner_chat_id=42)
    await bridge.handle_update(
        {
            "update_id": 5,
            "callback_query": {
                "id": "cb",
                "data": "approve:appr-x",
                "message": {"chat": {"id": 7}},
            },
        }
    )
    # Answered the callback (to dismiss the spinner) but resolved nothing.
    assert any(r["url"].endswith("/answerCallbackQuery") for r in transport.requests)


async def test_status_and_cancel_and_tools_commands(tmp_path) -> None:
    transport = FakeTransport()
    bridge, orchestrator, _ = build_bridge(tmp_path, transport)
    await bridge.handle_update(_message(42, "/status"))
    assert "No tasks yet." in transport.sent_texts()[-1]
    await bridge.handle_update(_message(42, "/tools"))
    assert "write_file" in transport.sent_texts()[-1]
    await bridge.handle_update(_message(42, "/cancel task-nope"))
    assert "not running" in transport.sent_texts()[-1]


async def test_run_loop_processes_a_queued_update_then_stops(tmp_path) -> None:
    import asyncio

    transport = FakeTransport()
    transport.queue_updates([_message(42, "/help", update_id=7)])
    bridge, _, _ = build_bridge(tmp_path, transport)

    stop = asyncio.Event()

    async def run() -> None:
        await bridge.run(stop_event=stop)

    task = asyncio.create_task(run())
    for _ in range(300):
        await asyncio.sleep(0.01)
        if any("Commands:" in t for t in transport.sent_texts()):
            break
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    # Offset advanced past the processed update, so it isn't re-handled.
    assert bridge._offset == 8  # update_id 7 + 1


async def test_bridge_pushes_generic_notifications(tmp_path) -> None:
    transport = FakeTransport()
    bridge, _, _ = build_bridge(tmp_path, transport)
    await bridge.send(
        Notification(type="task.started", message="Starting big job", task_id="t")
    )
    assert any("Starting big job" in t for t in transport.sent_texts())


async def test_bridge_does_not_push_when_no_owner(tmp_path) -> None:
    transport = FakeTransport()
    bridge, _, _ = build_bridge(tmp_path, transport, owner_chat_id=None)
    await bridge.send(Notification(type="task.started", message="x"))
    assert transport.sent_messages() == []
