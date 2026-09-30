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
# ~1.5k tokens. The old 20k chars was ~5k tokens on its own - every page read
# lands in the next prompt, so a full page is paid for again on each step
# that follows, and on a rate-limited free tier that is the budget that runs
# out first. An excerpt the model can actually use beats a full page.
_MAX_PAGE_TEXT = 6_000


def _slug(text: str, limit: int = 40) -> str:
    """A short, filesystem-safe stem for an archived page."""
    keep = [c if c.isalnum() else "-" for c in text.lower()]
    return "".join(keep).strip("-")[:limit] or "page"


def _direct_url(href: str) -> str:
    """Unwrap a DuckDuckGo redirect into the destination it points at.

    Results come back as ``duckduckgo.com/l/?uddg=<encoded>&rut=<hash>``.
    Handed one of those, a model tends to discard it and "helpfully" invent
    a tidy-looking direct link instead - which then 404s. Give it the real
    URL and it has nothing to improve on.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(href)
    if not parsed.netloc.endswith("duckduckgo.com") or parsed.path != "/l/":
        return href
    target = parse_qs(parsed.query).get("uddg")
    return unquote(target[0]) if target else href


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
        status = response.status if response else None
        title = await page.title()
        if status is not None and status >= 400:
            # Reporting this as success is worse than it sounds: an error
            # page's body is ordinary text to read_page, so the model happily
            # summarises a 404 and then cites the URL it never really read.
            # Failing here makes it pick another link instead.
            raise ToolError(
                f"{url} returned HTTP {status}, so there is no page to read. "
                "Open a URL from browser_search results rather than guessing."
            )
        return f"Opened {url} (HTTP {status}) — title: {title!r}"

    async def read_page(self, limit: int | None = _MAX_PAGE_TEXT) -> str:
        """Visible page text, trimmed to ``limit`` chars (None = all of it).

        The cap protects the model's context window, not the disk. Callers
        storing the text rather than showing it pass None and keep it all.
        """
        page = await self._ensure_page()
        if page.url == "about:blank":
            raise ToolError("No page is open yet; use browser_open first.")
        text: str = await page.inner_text("body")
        cleaned = "\n".join(
            line.strip() for line in text.splitlines() if line.strip()
        )
        return cleaned if limit is None else cleaned[:limit]

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

    async def research(
        self, query: str, pages: int = 2, save_to: str = "research"
    ) -> list[dict[str, str]]:
        """Search, then open and read the first pages that actually load.

        A plan cannot say "open the first search result": every step carries
        literal arguments chosen before anything ran, so the URL has to be
        guessed at planning time and a guessed URL is usually a 404. Doing
        the whole loop inside one tool removes the guess - the URLs come from
        the search, and only pages that really opened come back.
        """
        results = await self.search(query)
        if not results:
            raise ToolError(f"The search for {query!r} returned nothing.")

        collected: list[dict[str, str]] = []
        errors: list[str] = []
        for result in results:
            if len(collected) >= max(1, pages):
                break
            url = result["url"]
            try:
                await self.open(url)
                text = await self.read_page(limit=None)
            except ToolError as exc:  # dead link, paywall, timeout - try the next
                errors.append(f"{url}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the search
                errors.append(f"{url}: {type(exc).__name__}")
                continue
            title = result.get("title", "")
            entry = {"url": url, "title": title, "chars": str(len(text))}
            if save_to:
                # Disk is cheap and the context window is not, so archive
                # the whole page and hand back only a pointer plus a taste
                # of it. The rest is read later with read_file_slice, in
                # bites that fit.
                name = _slug(title or url)
                entry["path"] = self._files.write_text(
                    f"{save_to}/{len(collected):02d}_{name}.txt",
                    "SOURCE: " + url + "\nTITLE: " + title + "\n\n" + text,
                )
                # Deliberately short. This list is what a later step reads
                # the paths out of, and a fat excerpt per page pushes the
                # last paths past the prompt budget - at which point the
                # model invents a filename instead.
                entry["excerpt"] = text[:300]
            else:
                entry["text"] = text[:_MAX_PAGE_TEXT]
            collected.append(entry)

        if not collected:
            raise ToolError(
                f"None of the {len(results)} results for {query!r} could be read. "
                + "; ".join(errors[:3])
            )
        _log.info(
            "research complete",
            extra={"query": query, "read": len(collected), "skipped": len(errors)},
        )
        return collected

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
        return [
            {"title": r["title"], "url": _direct_url(r["url"])} for r in raw
        ][:10]
