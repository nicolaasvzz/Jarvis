"""Browser capabilities exposed as Tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jarvis.browser.controller import BrowserController
from jarvis.tools.base import Tool


def build_browser_tools(controller: BrowserController) -> list[Tool]:
    """Create the browser tool set bound to ``controller``."""

    async def browser_open(url: str) -> str:
        """Open a URL in the browser and report its title."""
        return await controller.open(url)

    async def browser_read_page() -> str:
        """Read the visible text of the currently open page."""
        return await controller.read_page()

    async def browser_click(target: str) -> str:
        """Click an element by CSS selector, or by its visible text."""
        return await controller.click(target)

    async def browser_fill(selector: str, value: str) -> str:
        """Fill a form field (CSS selector) with a value."""
        return await controller.fill(selector, value)

    async def browser_upload(selector: str, path: str) -> str:
        """Attach a workspace file to a file input on the page."""
        return await controller.upload(selector, path)

    async def browser_download(url: str, save_as: str) -> str:
        """Download a URL into the workspace, using the session's cookies."""
        return await controller.download(url, save_as)

    async def browser_extract_links() -> list[dict[str, str]]:
        """List the links (text + URL) on the currently open page."""
        return await controller.extract_links()

    async def browser_search(query: str) -> list[dict[str, str]]:
        """Search the web and return the top results (title + URL)."""
        return await controller.search(query)

    async def browser_research(
        query: str, pages: int = 2, save_to: str = "research"
    ) -> list[dict[str, str]]:
        """Search the web, then open and archive the top pages.

        Every page is saved in full under `save_to` - use as many pages as
        the job deserves, the archive is disk, not context. Returns one entry
        per page actually read: its real url, title, chars, the saved path,
        and a short excerpt. Read the full text later with read_file_slice,
        then delete_path the folder once the final answer is written.

        Prefer this over browser_search followed by browser_open: the URLs
        come from the search itself, so none has to be known in advance.
        """
        return await controller.research(query, pages, save_to)

    functions: list[Callable[..., Any]] = [
        browser_open,
        browser_read_page,
        browser_click,
        browser_fill,
        browser_upload,
        browser_download,
        browser_extract_links,
        browser_search,
        browser_research,
    ]
    return [
        Tool(name=f.__name__, description=f.__doc__ or "", func=f) for f in functions
    ]
