"""Trade the champ-set builder's setups on Alpaca: decide on the 1h chart, watch every 10 minutes.

    investment-bot trade-setup                 # Alpaca paper account, loops forever
    investment-bot trade-setup --once          # one look (for a scheduler)
    investment-bot trade-setup --dry-run       # decide and log, place no orders
    investment-bot trade-setup --only crypto   # one class (stock or crypto)

Every 10 minutes, just after a 10-minute candle closes:

1. **Open trades** are checked against the 10-minute candles since the last
   look: the stop, the target, the time limit, and for stocks the day's close
   (they're closed at 15:50 New York). Exits are market orders.
2. **News may stretch a trade.** When a trade reaches its normal end and that
   symbol's news agrees with it (its mood past ``research.signals.threshold``
   the same way) while its 1h chart still leans its way, it stays open
   longer: up to ``champ.news_cap_hours`` x news strength x signal strength,
   never past the story's own horizon. Its target moves out toward the news's
   predicted move (at most 3x the original), and its stop moves to break-even
   once it's half an ATR in profit. News turning against it closes it. A
   stretched stock trade may stay open overnight; nothing else does. Each
   stretched trade records what the normal exit would have made, and after
   ``champ.news_judge_after`` of them, if stretching did worse than the
   normal exits, the trader stops stretching by itself. News comes from
   ``news.db``, so run news research alongside (``--research watch``).
3. **New trades** when a 1-hour candle has closed (crypto on the hour, stocks
   at :30 New York): the champion setup for the symbol's class reads its
   three charts, and the trades it says yes to are taken, surest first.

Small accounts: a US stock account under ``champ.pdt_equity`` ($25,000) may
make only 3 day trades in 5 business days (the Pattern Day Trader rule). Any
stock trade here could become one (a stop, a target, the day's close), so each
keeps one in reserve: when Alpaca's count leaves none, no new stock trades.
Crypto isn't affected. Below $2,000 there are no stock shorts (Alpaca needs
that much for margin).

Safety: Alpaca's live endpoint only with ``live.allow_real_money: true``, and
then only **proven** setups; no new trades after ``live.max_daily_loss`` in a
day; a halt at ``risk.max_drawdown`` from the peak; exposure within
``risk.max_gross_exposure``. Positions it didn't open are left alone.
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import BotConfig
from ..reports import Report, tone_of
from ..style import Style, money_text
from .champ import ChampConfig, class_of, load_champions
from .charts import NY, decision_features
from .prepare import LabConfig
from .setups import Setup

STATE_FILE = "setup_trader.json"
HISTORY_DAYS = {"stock": 200, "crypto": 60}   # 10-minute candles, for the 4h 200-candle averages
LIVE_URL = "https://api.alpaca.markets"
STEP = pd.Timedelta(minutes=10)
FLAT_BY = pd.Timedelta(hours=15, minutes=50)   # stock trades close by then (New York)
MIN_ORDER = 1.0                                # dollars: Alpaca's smallest fractional order
SHORTS_FROM = 2000.0                           # equity Alpaca needs for margin (stock shorts)


@dataclass
class Trade:
    cls: str
    direction: int
    qty: float
    entry: float
    stop: float
    take: float
    atr: float
    chance: float
    opened: str                # ISO, UTC
    until: str                 # the normal time limit (ISO, UTC)
    last_seen: str = ""        # start of the last 10-minute candle checked
    day: str = ""              # New York date it opened (stock day trades)
    stretched: bool = False
    stretch_until: str = ""
    plain_exit: float = 0.0    # price at the normal end, for stretched trades
    plain_why: str = ""
    news: str = ""


@dataclass
class TraderState:
    trades: dict[str, Trade] = field(default_factory=dict)
    last_decision: dict[str, str] = field(default_factory=dict)  # symbol -> 1h candle closed
    lean: dict[str, float] = field(default_factory=dict)         # symbol -> latest 1h score
    day: str = ""
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    halted: bool = False
    stretched: list[dict[str, Any]] = field(default_factory=list)  # closed stretched trades
    stretch_off: bool = False
    log: list[dict[str, Any]] = field(default_factory=list)
    equity: list[list[Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> TraderState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        names = {f.name for f in fields(cls)}
        state = cls(**{k: v for k, v in raw.items() if k in names and k != "trades"})
        state.trades = {s: Trade(**t) for s, t in (raw.get("trades") or {}).items()}
        return state

    def save(self, path: Path) -> None:
        data = {**self.__dict__, "trades": {s: asdict(t) for s, t in self.trades.items()}}
        data["log"] = self.log[-300:]
        data["equity"] = self.equity[-3000:]
        data["stretched"] = self.stretched[-500:]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1, default=float), encoding="utf-8")
        tmp.replace(path)


def news_view(config: BotConfig, now: datetime) -> dict[str, dict[str, Any]]:
    """Each symbol's news mood from ``news.db``, with its strongest agreeing story's
    horizon and predicted move. Empty when there's no news database."""
    from ..research.runner import ResearchConfig
    from ..research.signals import since, symbol_moods
    from ..research.store import NewsStore

    rcfg = ResearchConfig.from_config(config)
    if not Path(rcfg.db_file).exists():
        return {}
    store = NewsStore(rcfg.db_file)
    try:
        rows = store.scored_since(since(now, rcfg.signals))
    finally:
        store.close()
    out = {}
    for m in symbol_moods(rows, now, rcfg.signals):
        sign = 1 if m["mood"] > 0 else -1
        agreeing = [r for r in rows if r["symbol"] == m["symbol"] and r["source"] != "x"
                     and float(r["direction"]) * sign > 0]
        top = max(agreeing, key=lambda r: abs(r["direction"] * r["strength"] * r["probability"]),
                  default=None)
        out[m["symbol"]] = {
            "mood": float(m["mood"]), "threshold": rcfg.signals.threshold,
            "horizon": float(top["horizon_hours"]) if top and top.get("horizon_hours") else None,
            "move_pct": float(top["move_pct"]) if top and top.get("move_pct") else None,
            "headline": m.get("headline", "")}
    return out


def latest_close(now: pd.Timestamp, stock: bool) -> pd.Timestamp | None:
    """When the latest 1-hour candle closed (None for stocks before the day's first)."""
    if not stock:
        return now.floor("1h")
    wall = now.tz_convert(NY)
    opens = wall.normalize() + pd.Timedelta(hours=9, minutes=30)
    hours = math.floor((wall - opens) / pd.Timedelta(hours=1))
    if hours < 1:
        return None
    close = min(opens + pd.Timedelta(hours=hours), wall.normalize() + pd.Timedelta(hours=16))
    return close.tz_convert("UTC")


class SetupTrader:
    def __init__(
        self,
        config: BotConfig,
        broker: Any,
        data: Any,
        say: Callable[[str], None] = print,
        dry_run: bool = False,
        state_file: str | Path = STATE_FILE,
        style: Style | None = None,
        money: float = 0.0,
        only: str | None = None,
        now: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC"),
        news: Callable[[datetime], dict[str, dict[str, Any]]] | None = None,
    ):
        self.config, self.broker, self.data, self.say = config, broker, data, say
        self.lab = LabConfig.from_config(config)
        self.cfg = ChampConfig.from_config(config)
        self.dry_run = dry_run
        self.style = style or Style()
        self.money = max(float(money), 0.0)
        self.classes = [c for c in self.cfg.trade if not only or c == only.rstrip("s")]
        self.state_path = Path(state_file)
        self.state = TraderState.load(self.state_path)
        self.now = now
        self.news = news or (lambda when: news_view(config, when))
        live = config.section("live")
        risk = config.section("risk")
        self.max_daily_loss = float(live.get("max_daily_loss", 0.03))
        self.max_drawdown = float(risk.get("max_drawdown", 0.25))
        self.max_gross = float(risk.get("max_gross_exposure", 1.0))
        self.real_money = getattr(broker, "base_url", "").startswith(LIVE_URL)
        if self.real_money and not live.get("allow_real_money", False):
            raise SystemExit(
                "ALPACA_BASE_URL points at the live (real money) endpoint, but the config "
                "doesn't say live.allow_real_money: true. Refusing to trade.")
        self.setups: dict[str, tuple[Setup, bool]] = {}
        self._stamp = 0.0
        self.started: pd.Timestamp | None = None   # set on the first look
        self.cycles = 0
        self._log_start = len(self.state.log)
        self._equity_start = len(self.state.equity)

    # ------------------------------------------------------------ inputs

    def load_setups(self) -> dict[str, tuple[Setup, bool]]:
        path = Path(self.cfg.results_file)
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return self.setups
        if stamp != self._stamp:
            fresh: dict[str, tuple[Setup, bool]] = {}
            for cls, champ in load_champions(path).items():
                if cls not in self.classes:
                    continue
                setup = Setup.from_dict(champ["setup"])
                if self.style.pinned:
                    pins = self.style.package_pins()
                    changes = {"confidence": pins.get("threshold"), "size": pins.get("size"),
                               "shorts": pins.get("shorts")}
                    setup = setup.with_(**{k: v for k, v in changes.items() if v is not None})
                proven = bool(champ.get("proven"))
                old = self.setups.get(cls)
                if old is None or old[0].to_dict() != setup.to_dict():
                    self.say(f"Trading {cls} with the champion setup"
                             + ("" if proven else " (NOT proven: small bets, paper only)")
                             + f": {setup.describe()}.")
                fresh[cls] = (setup, proven)
            self.setups = fresh
            self._stamp = stamp
        return self.setups

    def universe(self) -> list[dict[str, Any]]:
        try:
            items = json.loads(Path(self.lab.universe_file).read_text(encoding="utf-8"))["symbols"]
        except (OSError, ValueError, KeyError):
            return []
        return [u for u in items if class_of(u) in self.setups]

    def candles(self, symbol: str, days: int) -> pd.DataFrame:
        """Closed 10-minute candles only (the one still forming is dropped)."""
        raw = self.data.bars(symbol, "10Min", days)
        return raw[raw.index + STEP <= self.now()]

    # ------------------------------------------------------------ the cycle

    def cycle(self) -> None:
        setups = self.load_setups()
        if not setups:
            self.say("No champion setup yet: run the champ-set builder first "
                     "(investment-bot champ, or Champ-set builder in Jarvis).")
            return
        now = self.now()
        self.started = self.started or now
        account = self.broker.account()
        equity = float(account.get("equity") or account.get("portfolio_value") or 0)
        self._day_book(equity)
        universe = self.universe()
        meta = {u["symbol"]: u for u in universe}
        names = {u["symbol"].replace("/", ""): u["symbol"] for u in universe}
        positions = {names.get(p["symbol"], p["symbol"]): p for p in self.broker.positions()}
        self._forget_closed(positions)
        market_open = bool(self.broker.clock().get("is_open"))
        stretching = self.cfg.news_extension and not self.state.stretch_off
        news: dict[str, dict[str, Any]] = {}
        if stretching or self.state.trades:
            try:
                news = self.news(now.to_pydatetime())
            except Exception as exc:  # noqa: BLE001 - news must never stop trading
                self.say(f"  news unavailable ({' '.join(str(exc).split())[:100]})")
        actions = 0

        # 1. the decisions due: the 1h candles that have closed since the last look
        leans: dict[str, tuple[int, float, dict[str, Any]]] = {}
        for item in universe:
            cls = class_of(item)
            if cls == "stock" and not market_open:
                continue
            got = self._decide(item, setups[cls][0], now)
            if got is not None:
                leans[item["symbol"]] = got

        # 2. open trades
        closed: set[str] = set()
        for symbol, trade in list(self.state.trades.items()):
            if trade.cls == "stock" and not market_open:
                continue
            setup = setups.get(trade.cls, (None, False))[0]
            why, price = self._watch(symbol, trade, news.get(symbol) if stretching else None,
                                     setup, now)
            if why and self._close(symbol, positions.get(symbol), why, price):
                actions += 1
                closed.add(symbol)

        # 3. new trades, surest first
        stop_new = self.state.halted or self._daily_loss(equity) >= self.max_daily_loss
        budget = min(equity, self.money) if self.money else equity
        exposure = sum(abs(float(p.get("market_value") or 0)) for s, p in positions.items()
                       if not self.money or s in self.state.trades)
        day_trades = self._day_trades_left(account, equity, now)
        wanted = sorted(((s, d, info) for s, (d, _, info) in leans.items()
                         if d and s not in self.state.trades and s not in closed),
                        key=lambda x: -x[2]["chance"])
        for symbol, direction, info in wanted:
            item = meta[symbol]
            cls = class_of(item)
            setup, proven = setups[cls]
            if stop_new:
                break
            if self.real_money and not proven:
                continue
            if cls == "stock":
                if day_trades is not None and day_trades <= 0:
                    self._note(symbol, "skipped: no day trades left this week (account under "
                                       f"${self.cfg.pdt_equity:,.0f})")
                    continue
                if direction < 0 and equity < SHORTS_FROM:
                    continue
            fraction = float(setup.fraction(info["chance"]))
            if exposure + fraction * budget > self.max_gross * budget:
                continue
            if self._open(symbol, item, setup, direction, fraction, budget, info, now):
                exposure += fraction * budget
                actions += 1
                if cls == "stock" and day_trades is not None:
                    day_trades -= 1
        self.state.equity.append([now.isoformat(timespec="seconds"), round(equity, 2)])
        self.state.save(self.state_path)
        self.cycles += 1
        self.say(f"{now:%Y-%m-%d %H:%M} UTC  equity ${equity:,.2f}, {len(self.state.trades)} "
                 f"open, {actions} order(s)"
                 + (f", {day_trades} day trade(s) left" if day_trades is not None else "")
                 + (" [no new trades: daily loss limit/halt]" if stop_new else "")
                 + ("" if market_open else " [stock market closed]"))

    def _decide(self, item: dict[str, Any], setup: Setup, now: pd.Timestamp
                ) -> tuple[int, float, dict[str, Any]] | None:
        """Read the three charts on the 1h candle that just closed, if one did.
        Returns (direction, 1h lean, details) or None when nothing is due."""
        symbol = item["symbol"]
        stock = class_of(item) == "stock"
        close = latest_close(now, stock)
        if close is None or self.state.last_decision.get(symbol) == close.isoformat():
            return None
        try:
            raw = self.candles(symbol, HISTORY_DAYS[class_of(item)])
        except Exception as exc:  # noqa: BLE001 - one symbol's data must not stop the rest
            self._note(symbol, f"no candles ({' '.join(str(exc).split())[:100]})")
            return None
        if len(raw) < 300 or raw.index[-1] + STEP < close:
            return None  # the hour's last 10-minute candle isn't in yet: next look
        bars, feats = decision_features(raw, stock, setup.names())
        rows = np.flatnonzero(bars["closes"].to_numpy() == close.value)
        self.state.last_decision[symbol] = close.isoformat()
        if not len(rows):
            return None
        row = int(rows[-1])
        scores = setup.chart_scores(feats[setup.features].to_numpy()[row:row + 1],
                                    setup.features)
        lean = float(scores["1h"][0])
        self.state.lean[symbol] = lean
        can_short = stock and bool(item.get("shortable"))  # Alpaca crypto can't be shorted
        d, p = setup.decide(scores, can_short)
        details = {"chance": float(p[0]), "atr": float(bars["atr"].iloc[row]),
                   "atr4h": float(bars["atr4h"].iloc[row]),
                   "price": float(raw["close"].iloc[-1]),
                   "session_end": int(bars["session_end"].iloc[row])}
        direction = int(d[0])
        if direction and stock:
            left = (details["session_end"] - now.value) / 3.6e12
            if left < self.cfg.min_session_left:
                direction = 0
        if direction and not (math.isfinite(details["atr"]) and details["atr"] > 0):
            direction = 0
        return direction, lean, details

    # ------------------------------------------------------------ open trades

    def _watch(self, symbol: str, t: Trade, news: dict[str, Any] | None, setup: Setup | None,
               now: pd.Timestamp) -> tuple[str, float]:
        """Why this trade should end now ("" to keep it), and the price now."""
        try:
            raw = self.candles(symbol, 3)
        except Exception as exc:  # noqa: BLE001
            self._note(symbol, f"no candles to watch ({' '.join(str(exc).split())[:100]})")
            return "", t.entry
        if raw.empty:
            return "", t.entry
        price = float(raw["close"].iloc[-1])
        seen = pd.Timestamp(t.last_seen) if t.last_seen else pd.Timestamp(t.opened)
        d = t.direction
        for when, bar in raw[raw.index >= seen].iterrows():
            if t.last_seen and when <= pd.Timestamp(t.last_seen):
                continue
            hi, lo = float(bar["high"]), float(bar["low"])
            t.last_seen = when.isoformat()
            if (d > 0 and lo <= t.stop) or (d < 0 and hi >= t.stop):
                return "stop", price
            if (d > 0 and hi >= t.take) or (d < 0 and lo <= t.take):
                return "take profit", price
            if t.stretched and d * (float(bar["close"]) - t.entry) >= 0.5 * t.atr:
                even = t.entry * (1 + d * 2 * self._cost(t.cls))
                if (d > 0 and even > t.stop) or (d < 0 and even < t.stop):
                    t.stop = even
                    self._note(symbol, f"stop moved to break-even ({even:g})")
        if t.stretched:
            if news and news["mood"] * d <= -news["threshold"]:
                return "news turned", price
            if now >= pd.Timestamp(t.stretch_until):
                return "stretch over", price
            return "", price
        wall = now.tz_convert(NY)
        closing = t.cls == "stock" and wall - wall.normalize() >= FLAT_BY
        if now < pd.Timestamp(t.until) and not closing:
            return "", price
        why = "day's close" if closing and now < pd.Timestamp(t.until) else "time limit"
        if news is not None and setup is not None and self._stretch(symbol, t, news, setup,
                                                                    price, why, now):
            return "", price
        return why, price

    def _stretch(self, symbol: str, t: Trade, news: dict[str, Any], setup: Setup, price: float,
                 why: str, now: pd.Timestamp) -> bool:
        d = t.direction
        lean = self.state.lean.get(symbol, 0.0)
        if news["mood"] * d < news["threshold"] or lean * d <= 0:
            return False
        signal = min(max((t.chance - setup.confidence) / max(1 - setup.confidence, 1e-6) + 0.5,
                         0.5), 1.0)
        hours = self.cfg.news_cap_hours * abs(news["mood"]) * signal
        if news.get("horizon"):
            hours = min(hours, float(news["horizon"]))
        if hours < 1:
            return False
        distance = abs(t.take - t.entry)
        if news.get("move_pct"):
            distance = min(max(distance, t.entry * abs(news["move_pct"]) / 100), 3 * distance)
        t.take = t.entry + d * distance
        t.stretched = True
        t.stretch_until = (now + pd.Timedelta(hours=hours)).isoformat()
        t.plain_exit, t.plain_why = price, why
        t.news = str(news.get("headline", ""))[:160]
        self._note(symbol, f"news agrees (mood {news['mood']:+.2f}): kept open {hours:.0f}h more "
                           f"instead of the {why}; target {t.take:g}")
        return True

    # ------------------------------------------------------------ orders

    def _open(self, symbol: str, item: dict[str, Any], setup: Setup, direction: int,
              fraction: float, budget: float, info: dict[str, Any], now: pd.Timestamp) -> bool:
        crypto = class_of(item) == "crypto"
        price = info["price"]
        qty = fraction * budget / price
        if not crypto and (direction < 0 or not item.get("fractionable")):
            qty = math.floor(qty)  # shorts and non-fractional stocks: whole shares
        else:
            qty = math.floor(qty * 1e6) / 1e6
        if qty <= 0 or qty * price < MIN_ORDER:
            return False
        side = "buy" if direction > 0 else "sell"
        what = (f"enter {'long' if direction > 0 else 'short'} "
                f"({info['chance']:.0%} sure of the target before the stop)")
        if not self._order(symbol, side, qty, crypto, what):
            return False
        stop = price - direction * setup.sl_atr * info["atr"]
        take = price + direction * setup.take_distance(info["atr"], info["atr4h"])
        until = now + pd.Timedelta(hours=setup.hours)
        self.state.trades[symbol] = Trade(
            "crypto" if crypto else "stock", direction, qty, price, float(stop), float(take),
            info["atr"], info["chance"], now.isoformat(timespec="seconds"),
            until.isoformat(timespec="seconds"), last_seen=self._last_candle(now),
            day=now.tz_convert(NY).strftime("%Y-%m-%d"))
        return True

    def _last_candle(self, now: pd.Timestamp) -> str:
        return (now.floor("10min") - STEP).isoformat()

    def _close(self, symbol: str, position: dict[str, Any] | None, why: str, price: float) -> bool:
        trade = self.state.trades.get(symbol)
        crypto = "/" in symbol
        if position is not None:
            qty = abs(float(position.get("qty") or 0))
            side = "sell" if float(position.get("qty") or 0) > 0 else "buy"
            if qty > 0 and not self._order(symbol, side, qty, crypto, f"exit ({why})"):
                return False
        if trade is not None:
            pnl = trade.direction * (price / trade.entry - 1)
            self._note(symbol, f"closed {'long' if trade.direction > 0 else 'short'}: {why}, "
                               f"{pnl:+.2%}")
            if trade.stretched:
                plain = trade.direction * (trade.plain_exit / trade.entry - 1)
                self.state.stretched.append({"symbol": symbol, "pnl": round(pnl, 5),
                                             "plain": round(plain, 5), "why": why,
                                             "news": trade.news})
                self._judge_stretching()
        self.state.trades.pop(symbol, None)
        return True

    def _judge_stretching(self) -> None:
        done = self.state.stretched
        if self.state.stretch_off or len(done) < self.cfg.news_judge_after:
            return
        better = float(np.mean([s["pnl"] - s["plain"] for s in done]))
        if better < 0:
            self.state.stretch_off = True
            self.say(f"News stretches did worse than the normal exits over {len(done)} trades "
                     f"({better:+.2%} a trade): no more stretching.")

    def _order(self, symbol: str, side: str, qty: float, crypto: bool, why: str) -> bool:
        line = f"{side} {qty:g} {symbol}: {why}"
        if self.dry_run:
            self._note(symbol, "[dry run] " + line)
            return True
        try:
            self.broker.place(symbol, side, qty, crypto)
        except Exception as exc:  # noqa: BLE001 - one rejected order must not stop the rest
            self._note(symbol, f"order failed ({line}): {' '.join(str(exc).split())[:160]}")
            return False
        self._note(symbol, line)
        return True

    def _note(self, symbol: str, text: str) -> None:
        self.say(f"  {symbol}: {text}")
        self.state.log.append({"at": self.now().isoformat(timespec="seconds"),
                               "symbol": symbol, "what": text})

    def _forget_closed(self, positions: dict[str, Any]) -> None:
        """Trades the account no longer holds (closed by hand) are dropped."""
        if self.dry_run:
            return
        for symbol in list(self.state.trades):
            if symbol not in positions:
                self._note(symbol, "no longer held on the account; forgetting it")
                self.state.trades.pop(symbol)

    def _cost(self, cls: str) -> float:
        return (self.lab.crypto_cost_bps if cls == "crypto" else self.lab.stock_cost_bps) / 1e4

    # ------------------------------------------------------------ limits

    def _day_trades_left(self, account: dict[str, Any], equity: float,
                         now: pd.Timestamp) -> int | None:
        """Stock day trades still allowed (None: no limit, the account is big enough)."""
        if "stock" not in self.setups or equity >= self.cfg.pdt_equity:
            return None
        used = int(float(account.get("daytrade_count") or 0))
        today = now.tz_convert(NY).strftime("%Y-%m-%d")
        reserved = sum(1 for t in self.state.trades.values() if t.cls == "stock" and t.day == today)
        return max(3 - used - reserved, 0)

    def _day_book(self, equity: float) -> None:
        today = self.now().strftime("%Y-%m-%d")
        if self.state.day != today:
            self.state.day, self.state.day_start_equity = today, equity
        self.state.peak_equity = max(self.state.peak_equity, equity)
        if self.state.peak_equity and equity < self.state.peak_equity * (1 - self.max_drawdown):
            if not self.state.halted:
                self.say(f"Equity is {self.max_drawdown:.0%} below its peak: halting new trades.")
            self.state.halted = True

    def _daily_loss(self, equity: float) -> float:
        start = self.state.day_start_equity
        return (start - equity) / start if start else 0.0

    # ------------------------------------------------------------ the report

    def session_report(self) -> Report:
        took = (self.now() - (self.started or self.now())).total_seconds()
        hours = f"{took / 3600:.1f} h" if took >= 3600 else f"{took / 60:.0f} min"
        log = self.state.log[self._log_start:]
        orders = [e for e in log if str(e.get("what", "")).startswith(("buy", "sell"))]
        curve = self.state.equity[self._equity_start:]
        change = (curve[-1][1] / curve[0][1] - 1) if len(curve) > 1 and curve[0][1] else 0.0
        summary = (f"{self.cycles} look(s) over {hours}, {len(orders)} order(s)"
                   + (f", equity ${curve[0][1]:,.2f} -> ${curve[-1][1]:,.2f} ({change:+.2%})"
                      if len(curve) > 1 else "")
                   + (" [dry run]" if self.dry_run else ""))
        setups = "; ".join(f"{c}: {s.describe()}" + ("" if p else " (not proven)")
                           for c, (s, p) in self.setups.items()) or "no champion setup"
        out = Report("trading", summary, tone_of(change), title="Paper trading (1h setups)",
                     subtitle=f"{setups}; strategy: {self.style.describe()}; money: "
                              f"{money_text(self.money)}")
        out.stat("Looks", self.cycles).stat("Orders", len(orders))
        if curve:
            out.stat("Equity", f"${curve[-1][1]:,.2f}")
        if len(curve) > 1:
            out.stat("Change", f"{change:+.2%}", tone_of(change))
        out.stat("Open", len(self.state.trades))
        done = self.state.stretched
        if done:
            better = float(np.mean([s["pnl"] - s["plain"] for s in done]))
            out.stat("News stretches", f"{len(done)} ({better:+.2%} vs normal)", tone_of(better))
        if self.state.halted:
            out.stat("Halted", "yes", "bad")
        out.table("Open trades", trade_rows(self.state.trades), "Nothing open.")
        out.table("What it did", [
            {"At": str(e.get("at", ""))[:16], "Symbol": e.get("symbol"), "What": e.get("what")}
            for e in log[::-1]], "No orders this session.")
        return out

    # ------------------------------------------------------------ the loop

    def run_forever(self) -> None:
        step = STEP.total_seconds()
        self.say("Deciding on each 1-hour close, watching every 10 minutes. Ctrl+C stops it "
                 "(open positions stay open on the account).")
        while True:
            try:
                self.cycle()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - a bad look (network) must not end it
                self.say(f"Look failed: {' '.join(str(exc).split())[:200]}")
            now = time.time()
            time.sleep(step - now % step + 15)  # just after the next 10-minute candle closes


def trade_rows(trades: dict[str, Trade]) -> list[dict[str, Any]]:
    return [{"Symbol": s, "Side": "LONG" if t.direction > 0 else "SHORT",
             "Entry": round(t.entry, 4), "Stop": round(t.stop, 4), "Target": round(t.take, 4),
             "Sure %": round(t.chance * 100), "Until": (t.stretch_until or t.until)[:16],
             "News stretch": "yes" if t.stretched else "", "Since": t.opened[:16]}
            for s, t in trades.items()]
