"""The local web dashboard — Jarvis's heads-up display.

Three views onto one event stream:

* **Core** — the particle sphere, machine vitals, live activity and voice.
* **Files** — the workspace as a constellation, lighting up as it is touched.
* **Office** — the agent pool as pixel characters moving between rooms.

The :class:`~jarvis.dashboard.hub.DashboardHub` subscribes to the event bus
and enriches each event with what the pages need to draw it, so all three
are renderers of the same data rather than separate features.

Served by the API server, in the same process as the Orchestrator — live
agent state exists only in memory, so nothing outside that process could
show it.
"""

from jarvis.dashboard.frontend import find_frontend
from jarvis.dashboard.hub import DashboardHub
from jarvis.dashboard.mapping import ROOMS, file_action, room_for_tool, rooms_as_dicts
from jarvis.dashboard.routes import build_router

__all__ = [
    "ROOMS",
    "DashboardHub",
    "build_router",
    "file_action",
    "find_frontend",
    "room_for_tool",
    "rooms_as_dicts",
]
