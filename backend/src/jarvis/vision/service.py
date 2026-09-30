"""Screen capture and OCR services.

The heavy dependencies (``mss``, ``pillow``, ``pytesseract`` plus the
Tesseract binary) are imported lazily and belong to the ``vision`` extra.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from jarvis.core.errors import ToolError
from jarvis.files.operations import FileManager
from jarvis.logging import get_logger

_log = get_logger(__name__)

_SCREENSHOT_DIR = "screenshots"


def find_tesseract() -> str | None:
    """Locate the Tesseract binary: PATH first, then the standard installs.

    The Windows installer adds its folder to the *user* PATH, and only
    processes started afterwards inherit that. A Jarvis launched from a shell
    that was already open therefore fails to find a binary that is plainly
    sitting on disk, so look where it actually lives before giving up.
    """
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    # Windows upper-cases every key in os.environ, so these are the real names.
    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    candidates = []
    for root in roots:
        if not root:
            continue
        candidates.append(Path(root) / "Tesseract-OCR" / "tesseract.exe")
        candidates.append(Path(root) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


@runtime_checkable
class ScreenGrabber(Protocol):
    """Anything that can produce a PNG screenshot of the screen."""

    def grab_png(self) -> bytes: ...


class MssGrabber:
    """Real screen capture via ``mss`` (primary monitor)."""

    def grab_png(self) -> bytes:
        try:
            import mss
            import mss.tools
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ToolError(
                "mss is not installed. "
                'Install with: pip install "jarvis-assistant[vision]"',
                recoverable=False,
            ) from exc
        with mss.mss() as screen:
            monitor = screen.monitors[1] if len(screen.monitors) > 1 else screen.monitors[0]
            shot = screen.grab(monitor)
            # to_png returns None only when handed an output path to write to.
            png = mss.tools.to_png(shot.rgb, shot.size)
            if png is None:  # pragma: no cover - unreachable without output=
                raise ToolError("Screen capture produced no image data.")
            return png


class VisionService:
    """Screenshots into the sandbox + OCR reading and locating."""

    def __init__(self, grabber: ScreenGrabber, files: FileManager) -> None:
        self._grabber = grabber
        self._files = files
        self._counter = 0

    def capture_screen(self) -> str:
        """Take a screenshot; returns its workspace path."""
        png = self._grabber.grab_png()
        self._counter += 1
        path = f"{_SCREENSHOT_DIR}/screen_{self._counter:04d}.png"
        stored = self._files.write_bytes(path, png)
        _log.info("screenshot captured", extra={"path": stored})
        return stored

    def read_screen_text(self) -> str:
        """OCR the whole screen and return the recognised text."""
        image = self._image_from_screen()
        pytesseract = self._ocr()
        text: str = pytesseract.image_to_string(image)
        return text.strip()

    def locate_text(self, text: str) -> dict[str, int]:
        """Find ``text`` on screen; returns the centre {"x": .., "y": ..}.

        Matching is case-insensitive on OCR word groups. Raises a
        recoverable ToolError when not found so the agent can retry after
        e.g. focusing a different window.
        """
        needle = text.strip().lower()
        if not needle:
            raise ToolError("locate_text needs a non-empty string.")
        image = self._image_from_screen()
        pytesseract = self._ocr()
        data = pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT
        )
        words: list[str] = [w.strip() for w in data["text"]]
        lowered = [w.lower() for w in words]
        needle_parts = needle.split()

        for start in range(len(lowered) - len(needle_parts) + 1):
            window = lowered[start : start + len(needle_parts)]
            if window == needle_parts:
                xs = data["left"][start : start + len(needle_parts)]
                ys = data["top"][start : start + len(needle_parts)]
                ws = data["width"][start : start + len(needle_parts)]
                hs = data["height"][start : start + len(needle_parts)]
                x_centre = (min(xs) + max(x + w for x, w in zip(xs, ws, strict=True))) // 2
                y_centre = (min(ys) + max(y + h for y, h in zip(ys, hs, strict=True))) // 2
                return {"x": int(x_centre), "y": int(y_centre)}
        raise ToolError(f"Could not find {text!r} on the screen.")

    def _image_from_screen(self) -> Any:
        try:
            import io

            from PIL import Image
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ToolError(
                "pillow is not installed. "
                'Install with: pip install "jarvis-assistant[vision]"',
                recoverable=False,
            ) from exc
        return Image.open(io.BytesIO(self._grabber.grab_png()))

    @staticmethod
    def _ocr() -> Any:
        try:
            import pytesseract
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ToolError(
                "pytesseract (and the Tesseract binary) are required. "
                'Install with: pip install "jarvis-assistant[vision]" '
                "and install Tesseract OCR.",
                recoverable=False,
            ) from exc

        # pytesseract shells out to `tesseract` on PATH by default; point it
        # at the real binary when PATH has not caught up yet.
        if shutil.which(pytesseract.pytesseract.tesseract_cmd) is None:
            found = find_tesseract()
            if found is None:
                raise ToolError(
                    "The Tesseract OCR binary was not found. Install it from "
                    "https://github.com/UB-Mannheim/tesseract/wiki, then open a "
                    "new terminal. capture_screen works without it; reading "
                    "text from the screen does not.",
                    recoverable=False,
                )
            pytesseract.pytesseract.tesseract_cmd = found
            _log.info("resolved tesseract binary", extra={"path": found})
        return pytesseract
