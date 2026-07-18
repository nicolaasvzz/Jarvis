"""The Playwright-backed browser controller.

One controller = one browser context, started lazily on the first call and
kept alive between tool invocations so logins and cookies persist across
the steps of a task (and between tasks, until shutdown).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jarvis.config.schema import BrowserConfig
from jarvis.core.errors import ToolError
from jarvis.files.operations import FileManager
from jarvis.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

_log = get_logger(__name__)

_SEARCH_URL = "https://duckduckgo.com/html/?q={query}"
_MAX_PAGE_TEXT = 20_000


class BrowserController:
    """Drives a single persistent browser session via Playwright."""

    def __init__(self, config: BrowserConfig, files: FileManager) -> None:
        self._config = config
        self._files = files
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def _ensure_page(self) -> Page:
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ToolError(
                "Playwright is not installed. Install with: "
                'pip install "jarvis-assistant[browser]" && playwright install',
                recoverable=False,
            ) from exc

        self._playwright = await async_playwright().start()
        launcher = getattr(self._playwright, self._config.engine)
        self._browser = await launcher.launch(headless=self._config.headless)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        _log.info(
            "browser started",
            extra={"engine": self._config.engine, "headless": self._config.headless},
        )
        return self._page

    async def close(self) -> None:
        """Shut the browser down; safe to call when never started."""
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._playwright = self._browser = self._context = self._page = None

    # -- capabilities ------------------------------------------------------
    async def open(self, url: str) -> str:
        page = await self._ensure_page()
        response = await page.goto(url, wait_until="domcontentloaded")
        status = response.status if response else "?"
        title = await page.title()
        return f"Opened {url} (HTTP {status}) — title: {title!r}"

    async def read_page(self) -> str:
        page = await self._ensure_page()
        if page.url == "about:blank":
            raise ToolError("No page is open yet; use browser_open first.")
        text: str = await page.inner_text("body")
        cleaned = "\n".join(
            line.strip() for line in text.splitlines() if line.strip()
        )
        return cleaned[:_MAX_PAGE_TEXT]

    async def click(self, target: str) -> str:
        """Click a CSS selector, or visible text if the selector matches nothing."""
        page = await self._ensure_page()
        locator = page.locator(target)
        if await locator.count() == 0:
            locator = page.get_by_text(target, exact=False)
        if await locator.count() == 0:
            raise ToolError(f"Nothing matches {target!r} on the current page.")
        await locator.first.click()
        return f"Clicked {target!r}; now at {page.url}"

    async def fill(self, selector: str, value: str) -> str:
        page = await self._ensure_page()
        locator = page.locator(selector)
        if await locator.count() == 0:
            raise ToolError(f"No form field matches {selector!r}.")
        await locator.first.fill(value)
        return f"Filled {selector!r}."

    async def upload(self, selector: str, path: str) -> str:
        """Attach a sandbox file to a file input on the page."""
        page = await self._ensure_page()
        absolute = self._files.root / path
        if not absolute.is_file():
            raise ToolError(f"No such file in the workspace: {path}")
        await page.locator(selector).first.set_input_files(str(absolute))
        return f"Attached {path} to {selector!r}."

    async def download(self, url: str, save_as: str) -> str:
        """Fetch a URL (with the session's cookies) into the sandbox."""
        page = await self._ensure_page()
        response = await page.context.request.get(url)
        if not response.ok:
            raise ToolError(f"Download failed: HTTP {response.status} for {url}")
        body = await response.body()
        stored = self._files.write_bytes(save_as, body)
        return f"Saved {len(body)} bytes to {stored}"

    async def extract_links(self) -> list[dict[str, str]]:
        page = await self._ensure_page()
        raw: list[dict[str, Any]] = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))",
        )
        return [
            {"text": item["text"][:200], "href": item["href"]}
            for item in raw
            if item.get("href")
        ][:100]

    async def search(self, query: str) -> list[dict[str, str]]:
        """Run a web search and return the top results (title + URL)."""
        from urllib.parse import quote_plus

        page = await self._ensure_page()
        await page.goto(
            _SEARCH_URL.format(query=quote_plus(query)),
            wait_until="domcontentloaded",
        )
        raw: list[dict[str, Any]] = await page.eval_on_selector_all(
            "a.result__a",
            "els => els.map(e => ({title: e.innerText.trim(), url: e.href}))",
        )
        return [{"title": r["title"], "url": r["url"]} for r in raw][:10]
