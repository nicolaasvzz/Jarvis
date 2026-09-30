"""Tests for the browser controller's handling of dead links.

Playwright only raises for transport-level failures, so a 404 arrives as a
perfectly ordinary page whose body happens to say "not found". These cover
the guard that turns such a page into a tool failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.browser.controller import BrowserController, _direct_url
from jarvis.config.schema import BrowserConfig
from jarvis.core.errors import ToolError
from jarvis.files import FileManager


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakePage:
    """The two calls open() makes, and nothing else."""

    def __init__(self, status: int, title: str = "Example") -> None:
        self._status = status
        self._title = title
        self.visited: list[str] = []

    async def goto(self, url: str, **_: Any) -> FakeResponse:
        self.visited.append(url)
        return FakeResponse(self._status)

    async def title(self) -> str:
        return self._title


def _controller(tmp_path: Path, page: FakePage) -> BrowserController:
    controller = BrowserController(BrowserConfig(), FileManager(tmp_path / "root"))
    controller._page = page  # type: ignore[assignment]
    return controller


async def test_open_returns_title_and_status_for_a_live_page(tmp_path: Path) -> None:
    page = FakePage(200, title="Real Article")
    result = await _controller(tmp_path, page).open("https://example.com/a")
    assert "HTTP 200" in result
    assert "Real Article" in result


def test_search_results_unwrap_duckduckgo_redirects() -> None:
    """Results must carry the destination, not the redirector.

    Given a duckduckgo.com/l/ wrapper the model discards it and invents a
    tidy direct URL, which 404s and then gets cited as a source.
    """
    wrapped = (
        "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.investopedia.com"
        "%2Farticles%2Factive-trading%2F022715%2F20-rules-followed-professional"
        "-traders.asp&rut=b6834c14991f77224ec3c44580f4a142"
    )
    assert _direct_url(wrapped) == (
        "https://www.investopedia.com/articles/active-trading/022715/"
        "20-rules-followed-professional-traders.asp"
    )


def test_direct_url_leaves_ordinary_links_alone() -> None:
    for href in (
        "https://example.com/article",
        "https://duckduckgo.com/about",  # same host, not a redirect path
        "https://duckduckgo.com/l/",  # redirect path, no target
    ):
        assert _direct_url(href) == href


@pytest.mark.parametrize("status", [404, 402, 410, 500, 503])
async def test_open_fails_on_an_error_page(tmp_path: Path, status: int) -> None:
    """A dead link must fail loudly.

    Reported as success, the model reads the error page's text, summarises
    it, and cites a URL it never actually read - which is exactly how a
    research task ends up with fabricated sources.
    """
    page = FakePage(status)
    with pytest.raises(ToolError) as caught:
        await _controller(tmp_path, page).open("https://example.com/missing")
    assert str(status) in str(caught.value)
    # Recoverable, so the agent tries a different link rather than giving up.
    assert caught.value.recoverable is True


class ScriptedPage:
    """A page whose goto() status depends on the URL it is given."""

    def __init__(self, statuses: dict[str, int], body: str = "page body text") -> None:
        self._statuses = statuses
        self._body = body
        self.url = "https://example.com/"
        self.visited: list[str] = []

    async def goto(self, url: str, **_: Any) -> FakeResponse:
        self.visited.append(url)
        self.url = url
        return FakeResponse(self._statuses.get(url, 200))

    async def title(self) -> str:
        return "T"

    async def inner_text(self, _selector: str) -> str:
        return self._body


async def test_research_skips_dead_links_and_reads_live_ones(tmp_path: Path) -> None:
    """The whole point: no URL is ever guessed, and 404s do not stop the run."""
    page = ScriptedPage({"https://dead.example/a": 404})
    controller = _controller(tmp_path, page)

    async def fake_search(_query: str) -> list[dict[str, str]]:
        return [
            {"title": "Dead", "url": "https://dead.example/a"},
            {"title": "Live one", "url": "https://live.example/b"},
            {"title": "Live two", "url": "https://live.example/c"},
        ]

    controller.search = fake_search  # type: ignore[method-assign]
    out = await controller.research("traders", pages=2)

    assert [r["url"] for r in out] == [
        "https://live.example/b",
        "https://live.example/c",
    ]
    assert all(r["excerpt"] for r in out)
    assert "https://dead.example/a" in page.visited  # tried, then skipped


async def test_research_fails_when_nothing_can_be_read(tmp_path: Path) -> None:
    page = ScriptedPage({"https://dead.example/a": 404})
    controller = _controller(tmp_path, page)

    async def fake_search(_query: str) -> list[dict[str, str]]:
        return [{"title": "Dead", "url": "https://dead.example/a"}]

    controller.search = fake_search  # type: ignore[method-assign]
    with pytest.raises(ToolError, match="None of the 1 results"):
        await controller.research("traders")


async def test_research_archives_each_page_in_full(tmp_path: Path) -> None:
    """Disk is cheap; the context window is not.

    The whole page is kept on disk and only a pointer plus an excerpt comes
    back, so a long source can be worked through later in slices instead of
    being truncated to fit the prompt.
    """
    body = "x" * 50_000
    page = ScriptedPage({}, body=body)
    controller = _controller(tmp_path, page)

    async def fake_search(_query: str) -> list[dict[str, str]]:
        return [{"title": "Big Page", "url": "https://live.example/big"}]

    controller.search = fake_search  # type: ignore[method-assign]
    out = await controller.research("q", pages=1)

    entry = out[0]
    assert entry["chars"] == str(len(body))
    assert len(entry["excerpt"]) == 300           # what the model sees
    # Short on purpose: a later step reads the archived paths out of this
    # list, and fat excerpts push the last paths out of the prompt.
    saved = (tmp_path / "root" / entry["path"]).read_text(encoding="utf-8")
    assert len(saved) > 50_000                    # what is kept
    assert "SOURCE: https://live.example/big" in saved


async def test_research_can_return_text_inline_when_not_archiving(
    tmp_path: Path,
) -> None:
    page = ScriptedPage({}, body="short body")
    controller = _controller(tmp_path, page)

    async def fake_search(_query: str) -> list[dict[str, str]]:
        return [{"title": "P", "url": "https://live.example/p"}]

    controller.search = fake_search  # type: ignore[method-assign]
    out = await controller.research("q", pages=1, save_to="")
    assert out[0]["text"] == "short body"
    assert "path" not in out[0]
