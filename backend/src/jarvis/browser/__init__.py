"""Browser Controller: Playwright-based web automation.

Opens sites, reads pages, clicks, fills forms, downloads files into the
sandbox, uploads files from it, extracts links, and performs web searches.
Playwright is imported lazily, so the rest of Jarvis works without the
``browser`` extra installed; the browser itself starts on first use and is
shut down cleanly on close.

All downloads are written through the sandboxed
:class:`~jarvis.files.operations.FileManager`, so the browser can never
scatter files outside the allowed root.
"""

from jarvis.browser.controller import BrowserController
from jarvis.browser.tools import build_browser_tools

__all__ = ["BrowserController", "build_browser_tools"]
