"""Desktop Controller: drives Windows itself.

Opens and closes applications, focuses and moves windows, clicks, types,
and sends keyboard shortcuts. The OS-specific calls live behind the
:class:`~jarvis.desktop.controller.DesktopBackend` protocol —
:class:`~jarvis.desktop.controller.WindowsBackend` implements it with
``pyautogui``/``pygetwindow`` (imported lazily, ``desktop`` extra), while
tests inject a fake. Coordinates for clicking come from the Vision module,
never from hardcoded positions.
"""

from jarvis.desktop.controller import DesktopBackend, DesktopController, WindowsBackend
from jarvis.desktop.tools import build_desktop_tools

__all__ = [
    "DesktopBackend",
    "DesktopController",
    "WindowsBackend",
    "build_desktop_tools",
]
