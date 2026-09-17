"""Tests for the Desktop Controller and Vision service using fake backends."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from jarvis.core.errors import ToolError
from jarvis.desktop import DesktopController, build_desktop_tools
from jarvis.files import FileManager
from jarvis.vision import VisionService, build_vision_tools


class FakeBackend:
    """Records every desktop call for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.titles = ["Notepad — notes.txt", "Chrome"]

    def launch(self, command: str) -> None:
        self.calls.append(("launch", command))

    def window_titles(self) -> list[str]:
        return self.titles

    def focus_window(self, title: str) -> None:
        self.calls.append(("focus", title))

    def close_window(self, title: str) -> None:
        self.calls.append(("close", title))

    def move_window(self, title: str, x: int, y: int) -> None:
        self.calls.append(("move", title, x, y))

    def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))

    def type_text(self, text: str) -> None:
        self.calls.append(("type", text))

    def hotkey(self, keys: list[str]) -> None:
        self.calls.append(("hotkey", tuple(keys)))


class TestDesktopController:
    def test_actions_delegate_to_backend(self) -> None:
        backend = FakeBackend()
        controller = DesktopController(backend)
        controller.open_application("notepad")
        controller.focus_window("Notepad")
        controller.move_window("Notepad", 10, 20)
        controller.click_at(100, 200)
        controller.type_text("hello")
        controller.press_hotkey("ctrl+S")
        controller.close_window("Notepad")
        assert backend.calls == [
            ("launch", "notepad"),
            ("focus", "Notepad"),
            ("move", "Notepad", 10, 20),
            ("click", 100, 200),
            ("type", "hello"),
            ("hotkey", ("ctrl", "s")),
            ("close", "Notepad"),
        ]
        assert controller.list_windows() == backend.titles

    def test_validation_errors(self) -> None:
        controller = DesktopController(FakeBackend())
        with pytest.raises(ToolError):
            controller.open_application("   ")
        with pytest.raises(ToolError):
            controller.click_at(-1, 5)
        with pytest.raises(ToolError):
            controller.press_hotkey("  ")

    def test_tools_build_and_carry_docs(self) -> None:
        tools = build_desktop_tools(DesktopController(FakeBackend()))
        names = {t.name for t in tools}
        assert "open_application" in names
        assert "press_hotkey" in names
        assert all(t.description for t in tools)


def _png_with_text(text: str) -> bytes:
    """Render text onto a white image as PNG bytes.

    The font size matters. At PIL's default size Tesseract reads
    "HELLO WORLD" as the single token "HELLOWORLD" - the gap is too narrow
    to segment - which fails locate_text for a reason that has nothing to do
    with the code under test.
    """
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=28)  # Pillow >= 10.1
    except TypeError:  # pragma: no cover - older Pillow, unsized default
        font = ImageFont.load_default()
    image = Image.new("RGB", (400, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 40), text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class StaticGrabber:
    def __init__(self, png: bytes) -> None:
        self._png = png

    def grab_png(self) -> bytes:
        return self._png


class TestVision:
    def test_capture_screen_saves_into_sandbox(self, tmp_path: Path) -> None:
        files = FileManager(tmp_path / "root")
        service = VisionService(StaticGrabber(b"pngbytes"), files)
        path = service.capture_screen()
        assert path == "screenshots/screen_0001.png"
        assert (files.root / path).read_bytes() == b"pngbytes"
        assert service.capture_screen().endswith("0002.png")

    def test_vision_tools_build(self, tmp_path: Path) -> None:
        files = FileManager(tmp_path / "root")
        tools = build_vision_tools(VisionService(StaticGrabber(b"x"), files))
        assert {t.name for t in tools} == {
            "capture_screen",
            "read_screen_text",
            "locate_text_on_screen",
        }

    def test_ocr_reads_and_locates_text(self, tmp_path: Path) -> None:
        pytest.importorskip("pytesseract")
        from jarvis.vision.service import find_tesseract

        # Same lookup the service uses, so an installed-but-not-yet-on-PATH
        # Tesseract exercises this test instead of silently skipping it.
        if find_tesseract() is None:
            pytest.skip("tesseract binary not installed")
        files = FileManager(tmp_path / "root")
        service = VisionService(StaticGrabber(_png_with_text("HELLO WORLD")), files)
        assert "HELLO" in service.read_screen_text().upper()
        location = service.locate_text("HELLO")
        assert 0 < location["x"] < 400
        assert 0 < location["y"] < 120
