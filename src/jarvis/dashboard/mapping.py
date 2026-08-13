"""Translating tool calls into things you can watch.

The dashboard shows two views of the same event stream: a pixel office where
agents walk between rooms, and a constellation of files that lights up as
they are touched. Both need the same question answered — *what kind of work
is this?* — and neither the Tool Manager nor the agent pool should have to
care, because "which room does browser_click happen in" is a presentation
decision, not an execution one.

So the mapping lives here, on the presentation side of the line, and is
derived from the tool name alone. New tools slot in automatically: a name
that matches nothing known lands in the general work area rather than
vanishing from the display.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Where an agent is when it is not running a tool.
LOBBY = "lobby"
#: Where an agent is while the Planner is still thinking.
SITUATION_ROOM = "situation"


@dataclass(frozen=True)
class Room:
    """One area of the office, and what it is for."""

    id: str
    name: str
    #: Short line shown under the room name in the office view.
    subtitle: str
    #: Hue for the room's floor tint, 0-360.
    hue: int
    #: How many desks the renderer should draw. Agents beyond this stand.
    desks: int


ROOMS: tuple[Room, ...] = (
    Room(LOBBY, "Lobby", "waiting for orders", 210, 2),
    Room(SITUATION_ROOM, "Situation Room", "planning", 280, 4),
    Room("archives", "Archives", "files", 190, 4),
    Room("web", "Web Wing", "browser", 150, 4),
    Room("workshop", "Workshop", "apps & windows", 30, 4),
    Room("observatory", "Observatory", "screen & vision", 320, 2),
    Room("library", "Library", "memory", 90, 2),
)

_ROOM_IDS = frozenset(room.id for room in ROOMS)

#: Exact tool name → room. Covers every tool Jarvis ships with.
_TOOL_ROOMS: dict[str, str] = {
    # files
    "read_file": "archives",
    "write_file": "archives",
    "list_directory": "archives",
    "make_directory": "archives",
    "move_path": "archives",
    "copy_path": "archives",
    "delete_path": "archives",
    "search_files": "archives",
    "compress_files": "archives",
    "extract_archive": "archives",
    # memory
    "remember_fact": "library",
    "recall_facts": "library",
    "set_preference": "library",
    "get_preference": "library",
    # desktop
    "open_application": "workshop",
    "list_windows": "workshop",
    "focus_window": "workshop",
    "close_window": "workshop",
    "move_window": "workshop",
    "click_at": "workshop",
    "type_text": "workshop",
    "press_hotkey": "workshop",
    # vision
    "capture_screen": "observatory",
    "read_screen_text": "observatory",
    "locate_text_on_screen": "observatory",
}

#: Prefix → room, for tools added later that follow the naming convention.
_PREFIX_ROOMS: tuple[tuple[str, str], ...] = (
    ("browser_", "web"),
    ("web_", "web"),
    ("file_", "archives"),
    ("memory_", "library"),
    ("screen_", "observatory"),
    ("window_", "workshop"),
    ("app_", "workshop"),
)


def room_for_tool(tool: str | None) -> str:
    """Which room a tool call happens in."""
    if not tool:
        return LOBBY
    known = _TOOL_ROOMS.get(tool)
    if known is not None:
        return known
    for prefix, room in _PREFIX_ROOMS:
        if tool.startswith(prefix):
            return room
    # An unrecognised tool is still real work; put it on the main floor
    # rather than hiding it.
    return "workshop"


def is_room(room_id: str) -> bool:
    return room_id in _ROOM_IDS


def rooms_as_dicts() -> list[dict[str, object]]:
    """The office layout, for the browser to render."""
    return [
        {
            "id": room.id,
            "name": room.name,
            "subtitle": room.subtitle,
            "hue": room.hue,
            "desks": room.desks,
        }
        for room in ROOMS
    ]


# -- file activity ---------------------------------------------------------

#: Tools that only look at a file, versus those that change or remove one.
#: Drives the colour of the flare on the constellation.
_READ_TOOLS = frozenset(
    {
        "read_file",
        "list_directory",
        "search_files",
        "get_preference",
        "recall_facts",
    }
)
_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "make_directory",
        "copy_path",
        "move_path",
        "compress_files",
        "extract_archive",
    }
)
_DELETE_TOOLS = frozenset({"delete_path"})

#: Argument names that carry a workspace path, in the order they should be
#: preferred when a tool takes more than one. Deliberately excludes
#: ``pattern``: a glob like ``*.md`` is a query, not a file, and listing it
#: as one clutters the feed with paths that match no node.
_PATH_KEYS = ("path", "destination", "archive", "source", "sources")


def file_action(tool: str | None) -> str | None:
    """Classify a tool as ``read``/``write``/``delete``, or ``None``.

    ``None`` means the tool does not touch the workspace, so the file view
    should ignore it entirely.
    """
    if not tool:
        return None
    if tool in _DELETE_TOOLS:
        return "delete"
    if tool in _WRITE_TOOLS:
        return "write"
    if tool in _READ_TOOLS:
        return "read"
    return None


def paths_touched(arguments: dict[str, object] | None) -> list[str]:
    """Pull workspace paths out of (already redacted) tool arguments.

    Returns them normalised to forward slashes so they compare equal to the
    paths the File Manager reports, regardless of what the model wrote.
    """
    if not arguments:
        return []
    found: list[str] = []
    for key in _PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            _add_path(found, value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    _add_path(found, item)
    return found


def _add_path(found: list[str], raw: str) -> None:
    cleaned = raw.strip().replace("\\", "/").lstrip("./")
    # Redaction leaves size summaries like "<412 chars>"; they are not paths.
    if not cleaned or cleaned.startswith("<") or cleaned in found:
        return
    found.append(cleaned)
