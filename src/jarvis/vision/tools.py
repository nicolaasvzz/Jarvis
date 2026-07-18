"""Vision capabilities exposed as Tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jarvis.tools.base import Tool
from jarvis.vision.service import VisionService


def build_vision_tools(service: VisionService) -> list[Tool]:
    """Create the vision tool set bound to ``service``."""

    def capture_screen() -> str:
        """Take a screenshot; returns the saved image's workspace path."""
        return service.capture_screen()

    def read_screen_text() -> str:
        """Read all text currently visible on the screen (OCR)."""
        return service.read_screen_text()

    def locate_text_on_screen(text: str) -> dict[str, int]:
        """Find text on the screen and return its centre {x, y} for
        clicking with click_at."""
        return service.locate_text(text)

    functions: list[Callable[..., Any]] = [capture_screen, read_screen_text, locate_text_on_screen]
    return [
        Tool(name=f.__name__, description=f.__doc__ or "", func=f) for f in functions
    ]
