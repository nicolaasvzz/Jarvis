"""The research loop: collect, score, summarise, write research.json, repeat.

Each step catches its own failure and records it, so a busy Gemini or an X
error never stops Alpaca news from being collected (and the other way round).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import BotConfig
from .scorer import GeminiScorer, ScorerError
from .signals import SignalConfig, since, symbol_moods
from .sources import AlpacaNews, SourceError, XSearch
from .store import NewsStore

STATE_FILE = "research.json"


@dataclass
class ResearchConfig:
    db_file: str = "news.db"
    state_file: str = STATE_FILE
    poll_minutes: float = 5
    first_hours: float = 24          # how far back the very first run reads
    model: str = "gemini-3.5-flash"
    batch_size: int = 25
    max_scored_per_cycle: int = 200  # keeps a backlog from eating the day's quota at once
    x_monthly_cap: float = 5.0       # dollars; 0 turns X off
    x_price_per_post: float = 0.005
    x_posts_per_search: int = 10
    x_minutes_between: float = 120   # at most one X search this often
    x_symbol_hours: float = 12       # don't search the same symbol again sooner
    signals: SignalConfig = field(default_factory=SignalConfig)

    @classmethod
    def from_config(cls, config: BotConfig) -> ResearchConfig:
        raw = config.section("research")
        x = raw.get("x") or {}
        return cls(
            db_file=str(raw.get("db_file", cls.db_file)),
            state_file=str(raw.get("state_file", cls.state_file)),
            poll_minutes=float(raw.get("poll_minutes", cls.poll_minutes)),
            first_hours=float(raw.get("first_hours", cls.first_hours)),
            model=str(raw.get("model", cls.model)),
            batch_size=int(raw.get("batch_size", cls.batch_size)),
            max_scored_per_cycle=int(raw.get("max_scored_per_cycle", cls.max_scored_per_cycle)),
            x_monthly_cap=float(x.get("monthly_cap", cls.x_monthly_cap)),
            x_price_per_post=float(x.get("price_per_post", cls.x_price_per_post)),
            x_posts_per_search=int(x.get("posts_per_search", cls.x_posts_per_search)),
            x_minutes_between=float(x.get("minutes_between", cls.x_minutes_between)),
            x_symbol_hours=float(x.get("symbol_hours", cls.x_symbol_hours)),
            signals=SignalConfig.from_dict(raw.get("signals") or {}),
        )


def tracked_symbols(config: BotConfig, universe_file: str | Path = "universe.json") -> list[str]:
    """The lab's universe if it has scanned one, plus the config's own symbols."""
    symbols = list(config.universe)
    try:
        data = json.loads(Path(universe_file).read_text(encoding="utf-8"))
        symbols += [u["symbol"] for u in data.get("symbols") or [] if u.get("symbol")]
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        pass
    return list(dict.fromkeys(symbols))


class Researcher:
    def __init__(self, config: BotConfig, http: Any = None,
                 say: Callable[[str], None] = lambda _l: None,
                 symbols: list[str] | None = None):
        if http is None:
            import requests

            http = requests.Session()
        self.config = config
        self.cfg = ResearchConfig.from_config(config)
        self.say = say
        self.symbols = symbols or tracked_symbols(config)
        self.store = NewsStore(self.cfg.db_file)
        self.news = AlpacaNews(http, say=say)
        self.x = XSearch(http, self.cfg.x_monthly_cap, self.cfg.x_price_per_post,
                         self.cfg.x_posts_per_search, say=say)
        self.scorer = GeminiScorer(http, self.cfg.model, self.cfg.batch_size, say=say)
        self.moods: list[dict[str, Any]] = []
        self.http = http
        self.trader: Any = None

    def trade_with(self, broker: Any, dry_run: bool = False) -> None:
        """Also trade the news on `broker` each cycle (see ``trader.py``)."""
        from .trader import NewsTrader

        self.trader = NewsTrader(self.config, broker, self.store, self.http, self.symbols,
                                 say=self.say, dry_run=dry_run)

    # ------------------------------------------------------------ steps

    def collect_news(self, now: datetime) -> str:
        items = self.news.fetch(self.store, self.symbols, now, first_hours=self.cfg.first_hours)
        added = self.store.add(items)
        return f"{added} new stories"

    def pick_x_symbol(self, now: datetime) -> str | None:
        """The most-talked-about symbol (by news) not searched on X lately."""
        last = self.store.cursor("x_last_search")
        if last and now - datetime.fromisoformat(last) < timedelta(
                minutes=self.cfg.x_minutes_between):
            return None
        for mood in sorted(self.moods, key=lambda m: (-m["stories"], -abs(m["mood"]))):
            at = self.store.cursor(f"x_at:{mood['symbol']}")
            if not at or now - datetime.fromisoformat(at) >= timedelta(
                    hours=self.cfg.x_symbol_hours):
                return str(mood["symbol"])
        return None

    def collect_x(self, now: datetime) -> str:
        if not self.x.ready:
            return "off (no X_BEARER_TOKEN)" if not self.x.token else "off (cap is 0)"
        symbol = self.pick_x_symbol(now)
        if symbol is None:
            return "waiting"
        items = self.x.search(self.store, symbol, now)
        if items is None:
            return f"waiting for budget (${self.x.allowance(self.store, now):.2f} free now)"
        self.store.set_cursor("x_last_search", now.isoformat())
        self.store.set_cursor(f"x_at:{symbol}", now.isoformat())
        added = self.store.add(items)
        return f"{added} new posts about {symbol}"

    def score(self) -> str:
        if not self.scorer.ready:
            raise ScorerError("GEMINI_API_KEY isn't set in the bot's .env.")
        pending = self.store.unscored(self.cfg.max_scored_per_cycle)
        rated = 0
        for start in range(0, len(pending), self.cfg.batch_size):
            batch = pending[start:start + self.cfg.batch_size]
            scores = self.scorer.rate(batch)
            self.store.save_scores([i.id for i in batch], scores)  # kept even if a later batch fails
            rated += len(batch)
        return f"rated {rated}" + (" (more waiting)" if len(pending) == self.cfg.max_scored_per_cycle
                                   else "")

    # ------------------------------------------------------------ cycle

    def cycle(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        steps: dict[str, str] = {}
        for name, step in (("Alpaca news", lambda: self.collect_news(now)),
                           ("X", lambda: self.collect_x(now)),
                           ("Scoring", self.score)):
            try:
                steps[name] = step()
            except (SourceError, ScorerError, OSError, ValueError, KeyError) as exc:
                steps[name] = f"error: {exc}"
            self.say(f"{name}: {steps[name]}")
        rows = self.store.scored_since(since(now, self.cfg.signals))
        self.moods = symbol_moods(rows, now, self.cfg.signals)
        if self.trader is not None:
            try:
                done = self.trader.cycle(now, {m["symbol"]: m["mood"] for m in self.moods})
                steps["Trading"] = (f"{done['actions']} order(s), {done['open']} open"
                                    + (" [paused: daily loss limit]" if done["paused"] else ""))
            except Exception as exc:  # noqa: BLE001 - a broker hiccup must not stop research
                steps["Trading"] = f"error: {' '.join(str(exc).split())[:200]}"
            self.say(f"Trading: {steps['Trading']}")
        state = self.state(now, steps, rows)
        path = Path(self.cfg.state_file)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
        return state

    def state(self, now: datetime, steps: dict[str, str],
              rows: list[dict[str, Any]]) -> dict[str, Any]:
        news_rows = [r for r in rows if r["source"] != "x"]
        return {
            "updated": now.isoformat(timespec="seconds"),
            "steps": steps,
            "model": self.cfg.model,
            "symbols_tracked": len(self.symbols),
            "window_hours": self.cfg.signals.window_hours,
            "counts": self.store.count_since(since(now, self.cfg.signals)),
            "x_spent": round(self.store.spent("x", now), 3),
            "x_cap": self.cfg.x_monthly_cap,
            "x_on": self.x.ready,
            "trading": self.trader is not None,
            "moods": self.moods[:40],
            "signals": [m for m in self.moods if m["signal"]],
            "recent": [
                {k: r[k] for k in ("published", "symbol", "direction", "strength",
                                   "probability", "move_pct", "target", "horizon_hours",
                                   "event", "headline", "url")}
                for r in news_rows[:40]
            ],
        }

    def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            self.cycle()
            try:
                from ..jarvis_status import write_status

                write_status(self.config)
            except Exception:  # noqa: BLE001 - the status page must never stop research
                pass
            wait = self.cfg.poll_minutes * 60 - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)
