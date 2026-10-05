"""Trade the news on Alpaca paper: the bet follows the article's probability.

Gemini gives every (article, symbol) a probability, 1-100%, that its
predicted move happens within its horizon. When a fresh article clears
``min_probability`` (default 55%), the bot bets on it, and the bet grows in a
straight line with the probability:

    55% -> 2% of equity   ...   75% -> 26%   ...   95% or more -> 50%

The trade then ends at whichever comes first:

- the article's target (``target_price``, e.g. "BTC to $90k"), or the predicted
  move if it named none: take profit
- the stop, ``stop_pct`` (default 4%) against the entry
- the article's horizon running out
- the symbol's news turning the other way (its mood past the signal threshold)

Which articles count: news only (posts on X move the mood, never open a
trade), not just hype, published within ``entry_window_hours`` (news is
priced in fast), each article traded once, and none against the symbol's
overall mood. It opens only symbols nothing else holds on the account, and
closes only what it opened itself, so the package trader and anything bought
by hand are left alone.

Safety: Alpaca's paper endpoint only, unless ``research.trade.allow_real_money``
is true. No new trades after ``live.max_daily_loss`` (default 3%) in a day.
All news trades together stay within ``max_total`` of equity.

Every closed trade keeps its probability, so ``calibration`` shows whether the
70% calls really win about 70% of the time.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import BotConfig
from .store import NewsStore

STATE_FILE = "news_trader.json"
LIVE_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
BUCKETS = [(0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 1.01)]


@dataclass
class TradeConfig:
    min_probability: float = 0.55   # below this, no trade
    full_probability: float = 0.95  # at or above this, the biggest bet
    min_size: float = 0.02          # fraction of equity at min_probability
    max_size: float = 0.50          # fraction of equity at full_probability
    max_total: float = 1.0          # all news trades together
    max_open: int = 10
    min_strength: float = 0.2       # skip "what's going on with X stock" filler
    entry_window_hours: float = 2   # only articles this fresh
    stop_pct: float = 4.0
    cooldown_hours: float = 6       # after a trade on a symbol ends
    allow_real_money: bool = False
    state_file: str = STATE_FILE

    @classmethod
    def from_config(cls, config: BotConfig) -> TradeConfig:
        raw = (config.section("research").get("trade") or {})
        kwargs: dict[str, Any] = {}
        for name, spec in cls.__dataclass_fields__.items():
            if name in raw:
                kind = spec.type if isinstance(spec.type, type) else str(spec.type)
                value = raw[name]
                kwargs[name] = (bool(value) if kind in (bool, "bool") else
                                int(value) if kind in (int, "int") else
                                str(value) if kind in (str, "str") else float(value))
        return cls(**kwargs)

    def size_for(self, probability: float) -> float:
        """Fraction of equity to bet on an article with this probability (0 = none)."""
        if probability < self.min_probability:
            return 0.0
        span = max(self.full_probability - self.min_probability, 1e-9)
        t = min(1.0, (probability - self.min_probability) / span)
        return self.min_size + (self.max_size - self.min_size) * t


@dataclass
class NewsHolding:
    direction: int
    qty: float
    entry: float
    stop: float
    take: float | None
    until: str
    probability: float
    size: float
    item_id: str
    headline: str
    opened: str


@dataclass
class NewsTraderState:
    holdings: dict[str, NewsHolding] = field(default_factory=dict)
    traded: list[str] = field(default_factory=list)       # article ids already bet on
    cooldown: dict[str, str] = field(default_factory=dict)  # symbol -> until (ISO)
    closed: list[dict[str, Any]] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> NewsTraderState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        state = cls(**{k: v for k, v in raw.items()
                       if k in cls.__dataclass_fields__ and k != "holdings"})
        state.holdings = {s: NewsHolding(**h) for s, h in (raw.get("holdings") or {}).items()}
        return state

    def save(self, path: Path, extra: dict[str, Any]) -> None:
        data = {**asdict(self), **extra}
        data["traded"] = self.traded[-2000:]
        data["closed"] = self.closed[-500:]
        data["log"] = self.log[-300:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)


def calibration(closed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per probability band: how many news trades, and how many made money."""
    out = []
    for low, high in BUCKETS:
        trades = [t for t in closed if low <= float(t.get("probability", 0)) < high]
        if trades:
            wins = sum(1 for t in trades if float(t.get("pnl", 0)) > 0)
            out.append({"band": f"{low:.0%}-{min(high, 1):.0%}", "trades": len(trades),
                        "won": wins, "win_rate": round(wins / len(trades), 3),
                        "pnl": round(sum(float(t.get("pnl", 0)) for t in trades), 2)})
    return out


class NewsTrader:
    def __init__(self, config: BotConfig, broker: Any, store: NewsStore, http: Any,
                 symbols: list[str], universe_file: str | Path = "universe.json",
                 say: Callable[[str], None] = print, dry_run: bool = False):
        self.cfg = TradeConfig.from_config(config)
        self.broker, self.store, self.http, self.say = broker, store, http, say
        self.dry_run = dry_run
        self.symbols = symbols
        live = config.section("live")
        self.max_daily_loss = float(live.get("max_daily_loss", 0.03))
        self.signal_threshold = float(
            (config.section("research").get("signals") or {}).get("threshold", 0.5))
        base_url = getattr(broker, "base_url", "")
        if base_url.startswith(LIVE_URL) and not self.cfg.allow_real_money:
            raise SystemExit(
                "ALPACA_BASE_URL points at the live (real money) endpoint, but the config "
                "doesn't say research.trade.allow_real_money: true. Refusing to trade news.")
        self.info = self._universe(universe_file)
        self.path = Path(self.cfg.state_file)
        self.state = NewsTraderState.load(self.path)

    def _universe(self, path: str | Path) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return {u["symbol"]: u for u in data.get("symbols") or [] if u.get("symbol")}
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            return {}

    # ------------------------------------------------------------ prices

    def price(self, symbol: str) -> float:
        """The latest trade price from Alpaca's data API."""
        headers = {"APCA-API-KEY-ID": getattr(self.broker, "api_key", ""),
                   "APCA-API-SECRET-KEY": getattr(self.broker, "secret_key", "")}
        if "/" in symbol:
            resp = self.http.get(f"{DATA_URL}/v1beta3/crypto/us/latest/trades",
                                 params={"symbols": symbol}, headers=headers, timeout=30)
            trade = ((resp.json() or {}).get("trades") or {}).get(symbol) or {}
        else:
            resp = self.http.get(f"{DATA_URL}/v2/stocks/{symbol}/trades/latest",
                                 params={"feed": "iex"}, headers=headers, timeout=30)
            trade = (resp.json() or {}).get("trade") or {}
        price = float(trade.get("p") or 0)
        if price <= 0:
            raise ValueError(f"no recent price for {symbol}")
        return price

    # ------------------------------------------------------------ choosing

    def candidates(self, now: datetime, moods: dict[str, float]) -> list[dict[str, Any]]:
        """The best fresh article per symbol that clears the bar, likeliest first."""
        traded = set(self.state.traded)
        best: dict[str, dict[str, Any]] = {}
        for row in self.store.scored_since(now - timedelta(hours=self.cfg.entry_window_hours)):
            if (row["source"] == "x" or row["event"] == "hype" or row["direction"] == 0
                    or row["strength"] < self.cfg.min_strength
                    or row["probability"] < self.cfg.min_probability
                    or row["id"] in traded):
                continue
            direction = 1 if row["direction"] > 0 else -1
            if moods.get(row["symbol"], 0.0) * direction < 0:
                continue  # the rest of the news says otherwise
            held = best.get(row["symbol"])
            if held is None or row["probability"] > held["probability"]:
                best[row["symbol"]] = {**row, "dir": direction}
        return sorted(best.values(), key=lambda r: -r["probability"])

    # ------------------------------------------------------------ the cycle

    def cycle(self, now: datetime, moods: dict[str, float]) -> dict[str, Any]:
        account = self.broker.account()
        equity = float(account.get("equity") or account.get("portfolio_value") or 0)
        last = float(account.get("last_equity") or equity)
        stop_new = last > 0 and (last - equity) / last >= self.max_daily_loss
        names = {s.replace("/", ""): s for s in self.symbols}
        positions = {names.get(p["symbol"], p["symbol"]): p for p in self.broker.positions()}
        market_open = bool(self.broker.clock().get("is_open"))
        actions = 0

        for symbol, h in list(self.state.holdings.items()):
            crypto = "/" in symbol
            pos = positions.get(symbol)
            if pos is None and not self.dry_run:
                self._note(now, symbol, "no longer on the account (closed elsewhere); forgetting it")
                self.state.holdings.pop(symbol)
                continue
            if not crypto and not market_open:
                continue
            try:
                price = float((pos or {}).get("current_price") or 0) or self.price(symbol)
            except (ValueError, KeyError, OSError) as exc:
                self._note(now, symbol, f"no price ({exc})")
                continue
            why = self._exit_reason(h, price, now, moods.get(symbol, 0.0))
            if why and self._close(now, symbol, h, pos, price, why):
                actions += 1

        exposure = sum(h.qty * h.entry for h in self.state.holdings.values())
        for row in [] if stop_new else self.candidates(now, moods):
            symbol, direction = row["symbol"], row["dir"]
            crypto = "/" in symbol
            info = self.info.get(symbol, {})
            if (symbol in self.state.holdings or symbol in positions
                    or len(self.state.holdings) >= self.cfg.max_open
                    or self._cooling(symbol, now)
                    or (not crypto and not market_open)
                    or (direction < 0 and (crypto or not info.get("shortable")))):
                continue
            size = self.cfg.size_for(row["probability"])
            notional = min(size * equity, self.cfg.max_total * equity - exposure)
            if notional < self.cfg.min_size * equity:
                continue
            try:
                price = self.price(symbol)
            except (ValueError, KeyError, OSError) as exc:
                self._note(now, symbol, f"no price ({exc})")
                continue
            if self._open(now, row, notional, price, equity, info):
                exposure += notional
                actions += 1

        self.state.save(self.path, {
            "updated": now.isoformat(timespec="seconds"),
            "equity": round(equity, 2),
            "paused_today": stop_new,
            "dry_run": self.dry_run,
            "calibration": calibration(self.state.closed),
            "sizing": {f"{p:.0%}": round(self.cfg.size_for(p), 3)
                       for p in (0.55, 0.65, 0.75, 0.85, 0.95)},
        })
        return {"actions": actions, "open": len(self.state.holdings), "paused": stop_new}

    def _cooling(self, symbol: str, now: datetime) -> bool:
        until = self.state.cooldown.get(symbol)
        return bool(until) and now < datetime.fromisoformat(until)

    def _exit_reason(self, h: NewsHolding, price: float, now: datetime, mood: float) -> str:
        if (price - h.stop) * h.direction <= 0:
            return "stop"
        if h.take is not None and (price - h.take) * h.direction >= 0:
            return "target reached"
        if now >= datetime.fromisoformat(h.until):
            return "time's up"
        if mood * h.direction <= -self.signal_threshold:
            return "news turned"
        return ""

    def _open(self, now: datetime, row: dict[str, Any], notional: float, price: float,
              equity: float, info: dict[str, Any]) -> bool:
        symbol, direction = row["symbol"], row["dir"]
        crypto = "/" in symbol
        qty = notional / price
        if not crypto and (direction < 0 or not info.get("fractionable")):
            qty = math.floor(qty)  # shorts and non-fractional stocks: whole shares
        else:
            qty = math.floor(qty * 1e6) / 1e6
        if qty <= 0:
            return False
        target = row.get("target")
        move = float(row.get("move_pct") or 0)
        # A target must lie the right way and within reach: far-off levels are
        # usually analysts' 12-month targets (or misread numbers), not this trade's.
        reach = max(3 * move, 5.0)
        if (target and (float(target) - price) * direction > 0
                and abs(float(target) / price - 1) * 100 <= reach):
            take: float | None = float(target)
        elif row.get("move_pct"):
            take = price * (1 + direction * float(row["move_pct"]) / 100)
        else:
            take = None
        published = datetime.fromisoformat(row["published"])
        until = max(published + timedelta(hours=float(row["horizon_hours"])),
                    now + timedelta(hours=1))
        p = float(row["probability"])
        what = (f"{'buy' if direction > 0 else 'short'} {qty:g} at ~{price:g} "
                f"({p:.0%} likely, {qty * price / equity:.0%} of equity): {row['headline'][:90]}")
        if not self._order(now, symbol, "buy" if direction > 0 else "sell", qty, crypto, what):
            return False
        self.state.traded.append(row["id"])
        self.state.holdings[symbol] = NewsHolding(
            direction=direction, qty=qty, entry=price,
            stop=price * (1 - direction * self.cfg.stop_pct / 100), take=take,
            until=until.isoformat(timespec="seconds"), probability=p,
            size=round(qty * price / equity, 4), item_id=row["id"],
            headline=row["headline"][:200], opened=now.isoformat(timespec="seconds"))
        return True

    def _close(self, now: datetime, symbol: str, h: NewsHolding, pos: dict[str, Any] | None,
               price: float, why: str) -> bool:
        qty = h.qty if pos is None else min(h.qty, abs(float(pos.get("qty") or 0)))
        side = "sell" if h.direction > 0 else "buy"
        if qty > 0 and not self._order(now, symbol, side, qty, "/" in symbol, f"exit ({why})"):
            return False
        pnl = h.direction * (price - h.entry) * qty
        self.state.closed.append({
            "closed": now.isoformat(timespec="seconds"), "symbol": symbol,
            "side": "long" if h.direction > 0 else "short", "probability": h.probability,
            "size": h.size, "entry": h.entry, "exit": price, "pnl": round(pnl, 2),
            "pnl_pct": round(h.direction * (price / h.entry - 1) * 100, 2), "why": why,
            "headline": h.headline, "opened": h.opened})
        self.state.holdings.pop(symbol)
        self.state.cooldown[symbol] = (now + timedelta(hours=self.cfg.cooldown_hours)).isoformat()
        return True

    def _order(self, now: datetime, symbol: str, side: str, qty: float, crypto: bool,
               what: str) -> bool:
        if self.dry_run:
            self._note(now, symbol, "[dry run] " + what)
            return True
        try:
            self.broker.place(symbol, side, qty, crypto)
        except Exception as exc:  # noqa: BLE001 - a rejected order must not end the cycle
            self._note(now, symbol, f"order failed ({what[:60]}): {' '.join(str(exc).split())[:160]}")
            return False
        self._note(now, symbol, what)
        return True

    def _note(self, now: datetime, symbol: str, text: str) -> None:
        self.say(f"  {symbol}: {text}".encode("ascii", "replace").decode())
        self.state.log.append({"at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
                               "symbol": symbol, "what": text})
