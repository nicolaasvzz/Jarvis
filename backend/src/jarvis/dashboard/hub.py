"""The dashboard's view of a running Jarvis.

The hub subscribes to the event bus once and turns each raw
:class:`~jarvis.core.events.Event` into a *frame*: the same event, enriched
with everything the browser needs to draw it — which office room the work
happens in, which files it touched, whether Jarvis should say it aloud.

Doing that here rather than in JavaScript keeps a single definition of what
an event means. The three pages are then genuinely just renderers of one
stream, and a new page needs no new backend.

Two things it deliberately does *not* do: block, and grow. Publishing to a
browser that has stopped reading must never slow down real work, so each
subscriber has a bounded queue and a stalled one loses its oldest frames
rather than consuming memory without limit.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterator
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from jarvis.config.schema import DashboardConfig
from jarvis.core.events import Event, EventType
from jarvis.dashboard.mapping import (
    LOBBY,
    SITUATION_ROOM,
    file_action,
    paths_touched,
    room_for_tool,
    rooms_as_dicts,
)
from jarvis.logging import get_logger

if TYPE_CHECKING:
    from jarvis.agent.orchestrator import Orchestrator
    from jarvis.files.operations import FileManager
    from jarvis.security.permissions import PermissionPolicy
    from jarvis.voice.service import VoiceService

_log = get_logger(__name__)

#: Frames buffered per browser tab before the slowest ones are dropped. A
#: tab that falls this far behind is not rendering anyway; it will re-sync
#: from the snapshot when it reconnects.
_SUBSCRIBER_BACKLOG = 256

#: Events that mean "an agent moved to a new room".
_ROOM_EVENTS = frozenset(
    {EventType.STEP_STARTED, EventType.AGENT_ASSIGNED, EventType.APPROVAL_REQUIRED}
)

#: File activity is attributed to the moment work *starts*, and only then.
#: Both STEP_STARTED and STEP_COMPLETED carry the same arguments, so
#: classifying either would light every file — and list it — twice.
_FILE_EVENTS = frozenset({EventType.STEP_STARTED})


class DashboardHub:
    """Fans enriched events out to every open dashboard page."""

    def __init__(
        self,
        *,
        config: DashboardConfig,
        orchestrator: Orchestrator,
        policy: PermissionPolicy,
        files: FileManager,
        voice: VoiceService | None = None,
    ) -> None:
        self._config = config
        self._orchestrator = orchestrator
        self._policy = policy
        self._files = files
        self._voice = voice
        self._history: deque[dict[str, Any]] = deque(maxlen=config.history_limit)
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self._unsubscribe: Any | None = None
        self._sequence = 0
        # Where each agent currently is, so a page opened mid-task shows the
        # office already populated instead of empty.
        self._rooms: dict[str, str] = {}
        # Recently touched workspace paths, newest last, for the file view.
        self._touched: deque[dict[str, Any]] = deque(maxlen=120)

    # -- lifecycle --------------------------------------------------------
    def attach(self, bus: Any) -> None:
        """Start listening to the event bus."""
        if self._unsubscribe is None:
            self._unsubscribe = bus.subscribe(self._on_event)

    def close(self) -> None:
        """Stop listening and release every subscriber."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._queues.clear()

    # -- ingestion --------------------------------------------------------
    async def _on_event(self, event: Event) -> None:
        frame = self._to_frame(event)
        self._remember(frame)
        self._history.append(frame)
        for queue in list(self._queues):
            if queue.full():
                # Drop the oldest frame to make room; a stalled tab must not
                # be able to apply back-pressure to the agent loop.
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(frame)

    def _to_frame(self, event: Event) -> dict[str, Any]:
        self._sequence += 1
        data = dict(event.data)
        tool = _as_str(data.get("tool"))
        agent_id = _as_str(data.get("agent_id")) or _agent_id_from(data.get("agent"))
        arguments = data.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}

        frame: dict[str, Any] = {
            "seq": self._sequence,
            "type": event.type.value,
            "message": event.message,
            "task_id": event.task_id,
            "created_at": event.created_at.isoformat(),
            "data": data,
            "tool": tool,
            "agent_id": agent_id,
            "room": self._room_for(event, tool, agent_id),
            "speak": self._should_speak(event),
        }

        if event.type in _FILE_EVENTS:
            action = file_action(tool)
            if action is not None:
                paths = paths_touched(arguments)
                if paths:
                    frame["files"] = {"action": action, "paths": paths}
        return frame

    def _room_for(
        self, event: Event, tool: str | None, agent_id: str | None
    ) -> str | None:
        """Track and return which room this event puts an agent in."""
        if event.type is EventType.TASK_PLANNING:
            return SITUATION_ROOM
        if agent_id is None:
            return None
        if event.type in _ROOM_EVENTS:
            room = room_for_tool(tool) if tool else SITUATION_ROOM
            self._rooms[agent_id] = room
            return room
        if event.type in (EventType.AGENT_IDLE, EventType.AGENT_RELEASED):
            self._rooms[agent_id] = LOBBY
            return LOBBY
        return self._rooms.get(agent_id)

    def _should_speak(self, event: Event) -> bool:
        return self._voice is not None and self._voice.should_speak(event.type.value)

    def _remember(self, frame: dict[str, Any]) -> None:
        """Record file touches so a freshly opened page can show them."""
        touched = frame.get("files")
        if not isinstance(touched, dict):
            return
        for path in touched.get("paths", []):
            self._touched.append(
                {
                    "path": path,
                    "action": touched["action"],
                    "at": frame["created_at"],
                    "agent_id": frame.get("agent_id"),
                }
            )

    # -- output -----------------------------------------------------------
    def history(self, limit: int | None = None) -> list[dict[str, Any]]:
        frames = list(self._history)
        return frames[-limit:] if limit else frames

    def snapshot(self) -> dict[str, Any]:
        """Everything a page needs to render itself from cold."""
        from jarvis.api.schemas import ApprovalOut, TaskOut

        return {
            "workspace": str(self._files.root),
            "rooms": rooms_as_dicts(),
            "agents": self._agents(),
            "tasks": [TaskOut.from_task(t).model_dump(mode="json") for t in
                      self._orchestrator.all_tasks()[:20]],
            "approvals": [
                ApprovalOut.from_request(r).model_dump(mode="json")
                for r in self._policy.pending()
            ],
            "events": self.history(self._config.history_limit),
            "touched": list(self._touched),
            "voice": self._voice.describe() if self._voice else {"enabled": False},
            "settings": {
                "particles": self._config.particles,
                "accent": self._config.accent,
                "stats_interval": self._config.stats_interval,
            },
        }

    def _agents(self) -> list[dict[str, Any]]:
        """Pool agents, each tagged with the room it is currently in."""
        agents = self._orchestrator.pool.snapshot()
        for agent in agents:
            agent_id = _as_str(agent.get("id"))
            if agent_id is None:
                continue
            if agent.get("status") == "working":
                agent["room"] = room_for_tool(_as_str(agent.get("tool")))
            else:
                agent["room"] = self._rooms.get(agent_id, LOBBY)
        return agents

    def stream(self) -> _Subscription:
        """An async iterator of frames published from now on."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_SUBSCRIBER_BACKLOG
        )
        self._queues.add(queue)
        _log.info("dashboard client attached", extra={"clients": len(self._queues)})
        return _Subscription(queue, lambda: self._queues.discard(queue))


class _Subscription:
    """Async iterator over one client's queue, cleaning up on exit."""

    def __init__(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        close: Any,
    ) -> None:
        self._queue = queue
        self._close = close

    def __aiter__(self) -> _Subscription:
        return self

    async def __anext__(self) -> dict[str, Any]:
        return await self._queue.get()

    def __enter__(self) -> _Subscription:
        return self

    def __exit__(self, *_: object) -> None:
        self._close()

    def __iter__(self) -> Iterator[None]:  # pragma: no cover - guard
        raise TypeError("Use 'async for' to read a dashboard subscription.")


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _agent_id_from(agent: object) -> str | None:
    """Agent events carry the whole profile; pull the id back out of it."""
    if isinstance(agent, dict):
        return _as_str(agent.get("id"))
    return None
