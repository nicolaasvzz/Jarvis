"""Vision: understand the screen instead of trusting fixed coordinates.

Captures screenshots (``mss``), reads on-screen text with OCR
(``pytesseract``), and — most importantly — *locates* text on screen,
returning the centre coordinates of the match. The Desktop Controller
clicks what Vision finds, so Jarvis recovers when windows move.

Screen capture is behind the :class:`~jarvis.vision.service.ScreenGrabber`
protocol so everything above it is testable with synthetic images.
"""

from jarvis.vision.service import MssGrabber, ScreenGrabber, VisionService
from jarvis.vision.tools import build_vision_tools

__all__ = ["MssGrabber", "ScreenGrabber", "VisionService", "build_vision_tools"]
