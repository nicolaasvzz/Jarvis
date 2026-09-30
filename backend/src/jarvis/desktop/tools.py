"""Desktop capabilities exposed as Tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jarvis.desktop.controller import DesktopController
from jarvis.tools.base import Tool


def build_desktop_tools(controller: DesktopController) -> list[Tool]:
    """Create the desktop tool set bound to ``controller``."""

    def open_application(command: str) -> str:
        """Open an application by name or path (e.g. notepad, explorer)."""
        return controller.open_application(command)

    def list_windows() -> list[str]:
        """List the titles of all open windows."""
        return controller.list_windows()

    def focus_window(title: str) -> str:
        """Bring the window whose title contains the text to the front."""
        return controller.focus_window(title)

    def close_window(title: str) -> str:
        """Close the window whose title contains the text."""
        return controller.close_window(title)

    def move_window(title: str, x: int, y: int) -> str:
        """Move a window to screen coordinates (x, y)."""
        return controller.move_window(title, x, y)

    def click_at(x: int, y: int) -> str:
        """Click the mouse at screen coordinates (x, y). Get coordinates
        from locate_text_on_screen rather than guessing."""
        return controller.click_at(x, y)

    def type_text(text: str) -> str:
        """Type text with the keyboard into the focused window."""
        return controller.type_text(text)

    def press_hotkey(keys: str) -> str:
        """Press a keyboard shortcut, '+'-separated (e.g. ctrl+s, alt+f4)."""
        return controller.press_hotkey(keys)

    functions: list[Callable[..., Any]] = [
        open_application,
        list_windows,
        focus_window,
        close_window,
        move_window,
        click_at,
        type_text,
        press_hotkey,
    ]
    return [
        Tool(name=f.__name__, description=f.__doc__ or "", func=f) for f in functions
    ]
