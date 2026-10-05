"""Where research items come from.

- ``AlpacaNews``: Benzinga's feed through Alpaca's data API. Free with the
  Alpaca key (200 calls a minute), already tagged with tickers, history back
  to 2015.
- ``XSearch``: recent posts on X about one symbol at a time. X charges per
  post read (``price_per_post``), so every search is checked against a
  monthly cap first, and the cap is spread evenly over the month: by day 10 of
  30 it may have spent a third. Off unless ``X_BEARER_TOKEN`` is set.
"""
from __future__ import annotations

import calendar
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Item, NewsStore

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
X_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


class SourceError(RuntimeError):
    pass


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(text: str) -> str:
    """A source's timestamp as ISO UTC, so stored times sort as text."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def symbol_map(symbols: list[str]) -> dict[str, str]:
    """How sources write each tracked symbol -> how the bot does.
    Alpaca's news writes crypto as BTCUSD; the bot trades BTC/USD."""
    return {s.replace("/", ""): s for s in symbols}


class AlpacaNews:
    name = "alpaca"

    def __init__(self, http: Any, say: Callable[[str], None] = lambda _l: None):
        self.http = http
        self.say = say
        self.key = os.environ.get("ALPACA_API_KEY", "")
        self.secret = os.environ.get("ALPACA_SECRET_KEY", "")

    @property
    def ready(self) -> bool:
        return bool(self.key and self.secret)

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}
        for attempt in range(5):
            resp = self.http.get(NEWS_URL, params=params, headers=headers, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(min(2 ** attempt, 20))
                continue
            if resp.status_code >= 400:
                raise SourceError(f"Alpaca news error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            return data if isinstance(data, dict) else {}
        raise SourceError("Alpaca news kept refusing (rate limit or outage); trying next cycle.")

    def fetch(self, store: NewsStore, tracked: list[str], now: datetime,
              first_hours: float = 24, max_pages: int = 40) -> list[Item]:
        """Every story since the last one seen (or the last `first_hours`) that
        mentions a tracked symbol."""
        if not self.ready:
            raise SourceError("ALPACA_API_KEY and ALPACA_SECRET_KEY aren't set in .env.")
        names = symbol_map(tracked)
        cursor = store.cursor("alpaca_news")
        start = (datetime.fromisoformat(cursor) + timedelta(seconds=1) if cursor
                 else now - timedelta(hours=first_hours))
        params: dict[str, Any] = {"start": _iso(start), "end": _iso(now), "limit": 50,
                                  "sort": "asc", "exclude_contentless": "false"}
        items: list[Item] = []
        latest = cursor
        for _ in range(max_pages):
            data = self._get(params)
            for story in data.get("news") or []:
                published = _utc(str(story.get("created_at") or story.get("updated_at")))
                latest = max(latest or published, published)
                mine = [names[s] for s in story.get("symbols") or [] if s in names]
                if not mine:
                    continue
                items.append(Item(
                    id=f"alpaca:{story['id']}", source="alpaca", published=published,
                    headline=str(story.get("headline") or "").strip(),
                    summary=str(story.get("summary") or "").strip()[:600],
                    url=str(story.get("url") or ""), author=str(story.get("source") or ""),
                    symbols=mine,
                ))
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        if latest:
            store.set_cursor("alpaca_news", latest)
        return items


class XSearch:
    name = "x"

    def __init__(self, http: Any, monthly_cap: float, price_per_post: float = 0.005,
                 posts_per_search: int = 10, say: Callable[[str], None] = lambda _l: None):
        self.http = http
        self.token = os.environ.get("X_BEARER_TOKEN", "")
        self.cap = float(monthly_cap)
        self.price = float(price_per_post)
        self.per_search = max(10, min(100, int(posts_per_search)))  # X's allowed range
        self.say = say

    @property
    def ready(self) -> bool:
        return bool(self.token) and self.cap > 0

    def allowance(self, store: NewsStore, now: datetime) -> float:
        """Dollars it may still spend right now: the cap spread evenly over the
        month, so it can't spend it all on day one."""
        days = calendar.monthrange(now.year, now.month)[1]
        paced = self.cap * min(1.0, now.day / days)
        return max(0.0, paced - store.spent(self.name, now))

    def search(self, store: NewsStore, symbol: str, now: datetime) -> list[Item] | None:
        """Recent posts about `symbol`; None when the budget (or X) says not now."""
        if not self.ready:
            return None
        worst = self.per_search * self.price  # it bills per post returned, at most this many
        if worst > self.allowance(store, now):
            return None
        ticker = symbol.split("/")[0]
        since = store.cursor(f"x:{symbol}")
        params: dict[str, Any] = {
            "query": f"${ticker} lang:en -is:retweet",
            "max_results": self.per_search,
            "tweet.fields": "created_at,public_metrics",
        }
        if since:
            params["since_id"] = since
        resp = self.http.get(X_SEARCH_URL, params=params, timeout=30,
                             headers={"Authorization": f"Bearer {self.token}"})
        if resp.status_code == 429:
            return None  # X's own rate limit; nothing was billed
        if resp.status_code >= 400:
            raise SourceError(f"X search error {resp.status_code}: {resp.text[:200]}")
        data = resp.json() if resp.content else {}
        posts = data.get("data") or []
        store.add_spend(self.name, len(posts) * self.price, len(posts), now)
        newest = (data.get("meta") or {}).get("newest_id")
        if newest:
            store.set_cursor(f"x:{symbol}", str(newest))
        items = []
        for post in posts:
            metrics = post.get("public_metrics") or {}
            items.append(Item(
                id=f"x:{post['id']}", source="x",
                published=_utc(str(post.get("created_at") or _iso(now))),
                headline=str(post.get("text") or "").strip()[:600],
                url=f"https://x.com/i/status/{post['id']}",
                author=str(post.get("author_id") or ""), symbols=[symbol],
                engagement=int(metrics.get("like_count", 0)) + int(metrics.get("retweet_count", 0)),
            ))
        return items
