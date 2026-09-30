"""Desktop control behind a swappable backend.

``DesktopController`` holds the behaviour (validation, logging, friendly
errors); the ``DesktopBackend`` protocol holds the platform calls. This
keeps every bit of logic testable off-Windows and makes the real backend a
thin, obviously-correct wrapper.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Protocol, runtime_checkable

from jarvis.core.errors import ToolError
from jarvis.logging import get_logger

_log = get_logger(__name__)


@runtime_checkable
class DesktopBackend(Protocol):
    """The platform calls the Desktop Controller needs."""

    def launch(self, command: str) -> None: ...
    def window_titles(self) -> list[str]: ...
    def focus_window(self, title: str) -> None: ...
    def close_window(self, title: str) -> None: ...
    def move_window(self, title: str, x: int, y: int) -> None: ...
    def click(self, x: int, y: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def hotkey(self, keys: list[str]) -> None: ...


class WindowsBackend:
    """Real implementation using pyautogui + pygetwindow (Windows).

    Imports are deferred to first use so the package imports fine on any
    platform and without the ``desktop`` extra.
    """

    def _gui(self) -> Any:
        try:
            import pyautogui

            pyautogui.FAILSAFE = True  # slam cursor to a corner to abort
            return pyautogui
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ToolError(
                "pyautogui is not installed. "
                'Install with: pip install "jarvis-assistant[desktop]"',
                recoverable=False,
            ) from exc

    def _windows(self) -> Any:
        try:
            import pygetwindow

            return pygetwindow
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ToolError(
                "pygetwindow is not installed. "
                'Install with: pip install "jarvis-assistant[desktop]"',
                recoverable=False,
            ) from exc

    def _find(self, title: str) -> Any:
        matches = self._windows().getWindowsWithTitle(title)
        if not matches:
            raise ToolError(f"No window with title containing {title!r}.")
        return matches[0]

    def launch(self, command: str) -> None:
        if sys.platform == "win32":  # pragma: no cover - Windows only
            subprocess.Popen(  # noqa: S603 - launching apps is the feature
                f'start "" {command}', shell=True
            )
        else:
            subprocess.Popen([command])  # noqa: S603

    def window_titles(self) -> list[str]:
        return [t for t in self._windows().getAllTitles() if t.strip()]

    def focus_window(self, title: str) -> None:
        self._find(title).activate()

    def close_window(self, title: str) -> None:
        self._find(title).close()

    def move_window(self, title: str, x: int, y: int) -> None:
        self._find(title).moveTo(x, y)

    def click(self, x: int, y: int) -> None:
        self._gui().click(x, y)

    def type_text(self, text: str) -> None:
        self._gui().write(text, interval=0.02)

    def hotkey(self, keys: list[str]) -> None:
        self._gui().hotkey(*keys)


class DesktopController:
    """Validated, logged desktop actions over any backend."""

    def __init__(self, backend: DesktopBackend) -> None:
        self._backend = backend

    def open_application(self, command: str) -> str:
        if not command.strip():
            raise ToolError("Application command must not be empty.")
        self._backend.launch(command.strip())
        _log.info("launched application", extra={"command": command})
        return f"Launched {command!r}."

    def list_windows(self) -> list[str]:
        return self._backend.window_titles()

    def focus_window(self, title: str) -> str:
        self._backend.focus_window(title)
        return f"Focused window {title!r}."

    def close_window(self, title: str) -> str:
        self._backend.close_window(title)
        _log.info("closed window", extra={"title": title})
        return f"Closed window {title!r}."

    def move_window(self, title: str, x: int, y: int) -> str:
        self._backend.move_window(title, x, y)
        return f"Moved window {title!r} to ({x}, {y})."

    def click_at(self, x: int, y: int) -> str:
        if x < 0 or y < 0:
            raise ToolError("Click coordinates must be non-negative.")
        self._backend.click(x, y)
        return f"Clicked at ({x}, {y})."

    def type_text(self, text: str) -> str:
        self._backend.type_text(text)
        return f"Typed {len(text)} characters."

    def press_hotkey(self, keys: str) -> str:
        """``keys`` is a '+'-separated combo, e.g. ``ctrl+shift+esc``."""
        parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
        if not parts:
            raise ToolError("No keys given for the hotkey.")
        self._backend.hotkey(parts)
        return f"Pressed {'+'.join(parts)}."
