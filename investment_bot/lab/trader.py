"""Trade the lab's champion package on Alpaca, every 10 minutes.

    investment-bot trade-package            # Alpaca paper account, loops forever
    investment-bot trade-package --once     # one cycle (for a scheduler)
    investment-bot trade-package --dry-run  # decide, log, but place no orders

Each cycle, at every 10-minute candle close:

1. Reload the champion from ``lab.json`` (a lab running at the same time can
   improve it), and the symbol list from ``universe.json``.
2. For every symbol (stocks only while the market is open; crypto always):
   download the new candles, compute the package's indicators on every
   timeframe, and its score on the candle that just closed.
3. Same rules as the backtest: trailing stops and take-profits against the
   candle's high/low, exits when the score fades (after ``min_hold``),
   ``max_hold``, ``cooldown``, ``confirm``; entries sized by conviction.
4. Market orders to Alpaca. Positions on the account are the truth for
   quantities; ``package_trader.json`` keeps the rest (stops, candles held).

Safety: it refuses Alpaca's live (real-money) endpoint unless the config says
``live.allow_real_money: true``; it stops opening trades for the day after
``live.max_daily_loss`` (default 3%) and halts after ``risk.max_drawdown``
from the peak; exposure never passes ``risk.max_gross_exposure``.
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..config import BotConfig
from ..reports import Report, tone_of
from ..style import Style, money_text
from .features import ALPACA_TF, columns_by_tf, duration, multi_timeframe
from .package import Package
from .prepare import LabConfig

STATE_FILE = "package_trader.json"
HISTORY_DAYS = 120          # 10-minute candles kept for the indicators (4h ones need ~100 days)
DAILY_DAYS = 420            # daily candles (the 200-day averages need warm-up)
LIVE_URL = "https://api.alpaca.markets"


@dataclass
class Holding:
    direction: int
    entry_price: float
    stop: float
    take: float | None
    bars: int = 0
    fraction: float = 0.0
    opened: str = ""
    last_bar: str = ""


@dataclass
class TraderState:
    holdings: dict[str, Holding] = field(default_factory=dict)
    cooldown: dict[str, int] = field(default_factory=dict)   # candles left
    last_bar: dict[str, str] = field(default_factory=dict)
    day: str = ""
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    halted: bool = False
    log: list[dict[str, Any]] = field(default_factory=list)
    equity: list[list[Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> TraderState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        state = cls(**{k: v for k, v in raw.items() if k != "holdings"})
        state.holdings = {s: Holding(**h) for s, h in (raw.get("holdings") or {}).items()}
        return state

    def save(self, path: Path) -> None:
        data = {**self.__dict__, "holdings": {s: h.__dict__ for s, h in self.holdings.items()}}
        data["log"] = self.log[-300:]
        data["equity"] = self.equity[-3000:]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1, default=float), encoding="utf-8")
        tmp.replace(path)


class PackageTrader:
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
        now: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC"),
    ):
        self.config, self.broker, self.data, self.say = config, broker, data, say
        self.cfg = LabConfig.from_config(config)
        self.dry_run = dry_run
        self.style = style or Style()      # a strategy picked in Jarvis, laid over the champion
        self.money = max(float(money), 0.0)  # trade with at most this much (0: the whole account)
        self.state_path = Path(state_file)
        self.state = TraderState.load(self.state_path)
        self.now = now
        live = config.section("live")
        risk = config.section("risk")
        self.max_daily_loss = float(live.get("max_daily_loss", 0.03))
        self.max_drawdown = float(risk.get("max_drawdown", 0.25))
        self.max_gross = float(risk.get("max_gross_exposure", 1.0))
        base_url = getattr(broker, "base_url", "")
        if base_url.startswith(LIVE_URL) and not live.get("allow_real_money", False):
            raise SystemExit(
                "ALPACA_BASE_URL points at the live (real money) endpoint, but the config "
                "doesn't say live.allow_real_money: true. Refusing to trade.")
        self.package: Package | None = None
        self._pkg_stamp = 0.0
        # This session, for its report: where the log and equity curve stood at the start.
        self.started = self.now()
        self.cycles = 0
        self._log_start = len(self.state.log)
        self._equity_start = len(self.state.equity)

    # ------------------------------------------------------------ inputs

    def load_package(self) -> Package | None:
        path = Path(self.cfg.results_file)
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return None
        if stamp != self._pkg_stamp:
            champ = (json.loads(path.read_text(encoding="utf-8")).get("champion") or {})
            if champ:
                new = Package.from_dict(champ["package"])
                if self.style.pinned:
                    new = new.with_(**self.style.package_pins())
                if self.package is None or new.to_dict() != self.package.to_dict():
                    self.say(f"Trading the champion package: {new.describe()}.")
                self.package = new
            self._pkg_stamp = stamp
        return self.package

    def universe(self) -> list[dict[str, Any]]:
        path = Path(self.cfg.universe_file)
        try:
            return json.loads(path.read_text(encoding="utf-8"))["symbols"]
        except (OSError, ValueError, KeyError):
            return []

    def candles(self, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame | None]:
        """Closed candles only (the one still forming is dropped)."""
        base = self.data.bars(symbol, ALPACA_TF[self.cfg.base_tf], HISTORY_DAYS)
        step = duration(self.cfg.base_tf)
        base = base[base.index + step <= self.now()]
        daily = None
        if "1d" in self.cfg.timeframes:
            daily = self.data.bars(symbol, "1Day", DAILY_DAYS)
            daily = daily[daily.index + pd.Timedelta(days=1) <= self.now()]
        return base, daily

    def scores(self, pkg: Package, base: pd.DataFrame, daily: pd.DataFrame | None,
               tail: int) -> np.ndarray:
        features = multi_timeframe(base, self.cfg.timeframes, daily, columns_by_tf(pkg.features))
        matrix = features[pkg.features].tail(tail).to_numpy(dtype=np.float32)
        return pkg.score(matrix)

    # ------------------------------------------------------------ the cycle

    def cycle(self) -> None:
        pkg = self.load_package()
        if pkg is None:
            self.say("No champion package yet: run `investment-bot lab` first.")
            return
        account = self.broker.account()
        equity = float(account.get("equity") or account.get("portfolio_value") or 0)
        self._day_book(equity)
        universe = self.universe()
        # Alpaca reports crypto positions without the slash (BTCUSD for BTC/USD).
        names = {u["symbol"].replace("/", ""): u["symbol"] for u in universe}
        positions = {names.get(p["symbol"], p["symbol"]): p for p in self.broker.positions()}
        self._forget_closed(positions)
        market_open = bool(self.broker.clock().get("is_open"))
        # The money it trades with: the account, or less if you said so. With a
        # budget, only its own positions count against it (not ones you hold).
        budget = min(equity, self.money) if self.money else equity
        exposure = sum(abs(float(p.get("market_value") or 0)) for s, p in positions.items()
                       if not self.money or s in self.state.holdings)
        stop_new = self.state.halted or self._daily_loss(equity) >= self.max_daily_loss
        actions = 0
        for item in universe:
            symbol = item["symbol"]
            crypto = item.get("class") == "crypto"
            if not crypto and not market_open:
                continue
            try:
                base, daily = self.candles(symbol)
            except Exception as exc:
                self.say(f"  {symbol}: no candles ({' '.join(str(exc).split())[:100]})")
                continue
            if len(base) < 100:
                continue
            last = base.index[-1].isoformat()
            if self.state.last_bar.get(symbol) == last:
                continue  # nothing new since the last cycle
            new_bars = 1 if symbol in self.state.last_bar else 0
            self.state.last_bar[symbol] = last
            score = self.scores(pkg, base, daily, max(pkg.confirm, 1))
            bar = base.iloc[-1]
            atr = float(ind.atr(base["high"], base["low"], base["close"], 14).iloc[-1])
            price = float(bar["close"])
            holding = self.state.holdings.get(symbol)
            if holding is not None:
                holding.bars += new_bars
                why = self._exit_reason(pkg, holding, bar, score[-1])
                if why:
                    if self._close(symbol, positions.get(symbol), why, price):
                        actions += 1
                        held = positions.get(symbol) or {}
                        exposure -= abs(float(held.get("market_value") or 0))
                    continue
                self._trail(pkg, holding, price, atr)
                continue
            if self.state.cooldown.get(symbol, 0) > 0:
                self.state.cooldown[symbol] -= 1
                continue
            if stop_new or not math.isfinite(atr) or atr <= 0:
                continue
            direction = 0
            if np.all(score >= pkg.threshold):
                direction = 1
            elif (np.all(score <= -pkg.threshold) and pkg.shorts and item.get("shortable")
                  and not crypto):
                direction = -1
            if not direction:
                continue
            theta = pkg.threshold
            conviction = min(max((abs(score[-1]) - theta) / max(1 - theta, 1e-6), 0.0), 1.0)
            fraction = pkg.size * (0.5 + 0.5 * conviction)
            if (exposure + fraction * budget) > self.max_gross * budget:
                continue
            if self._open(symbol, item, direction, fraction, budget, price, atr, pkg, last,
                          score[-1]):
                exposure += fraction * budget
                actions += 1
        self.state.equity.append([self.now().isoformat(timespec="seconds"), round(equity, 2)])
        self.state.save(self.state_path)
        self.cycles += 1
        self.say(f"{self.now():%Y-%m-%d %H:%M} UTC  equity ${equity:,.0f}, "
                 f"{len(self.state.holdings)} open, {actions} order(s)"
                 + (" [no new trades: daily loss limit/halt]" if stop_new else "")
                 + ("" if market_open else " [stock market closed]"))

    # ------------------------------------------------------------ rules

    def _exit_reason(self, pkg: Package, h: Holding, bar: pd.Series, score: float) -> str:
        low, high = float(bar["low"]), float(bar["high"])
        if (h.direction > 0 and low <= h.stop) or (h.direction < 0 and high >= h.stop):
            return "stop"
        if h.take is not None and ((h.direction > 0 and high >= h.take)
                                   or (h.direction < 0 and low <= h.take)):
            return "take profit"
        if score * h.direction <= pkg.exit_threshold and h.bars + 1 >= pkg.min_hold:
            return "signal"
        if pkg.max_hold and h.bars + 1 >= pkg.max_hold:
            return "max hold"
        return ""

    def _trail(self, pkg: Package, h: Holding, price: float, atr: float) -> None:
        if not math.isfinite(atr):
            return
        level = price - h.direction * pkg.stop_atr * atr
        h.stop = float(max(h.stop, level) if h.direction > 0 else min(h.stop, level))

    def _open(self, symbol: str, item: dict[str, Any], direction: int, fraction: float,
              equity: float, price: float, atr: float, pkg: Package, bar: str,
              score: float) -> bool:
        crypto = item.get("class") == "crypto"
        qty = fraction * equity / price
        if not crypto and (direction < 0 or not item.get("fractionable")):
            qty = math.floor(qty)  # shorts and non-fractional stocks: whole shares
        else:
            qty = math.floor(qty * 1e6) / 1e6
        if qty <= 0:
            return False
        side = "buy" if direction > 0 else "sell"
        what = f"enter {'long' if direction > 0 else 'short'} (score {score:+.2f})"
        if not self._order(symbol, side, qty, crypto, what):
            return False
        stop = price - direction * pkg.stop_atr * atr
        take = price + direction * pkg.take_atr * atr if pkg.take_atr else None
        self.state.holdings[symbol] = Holding(direction, price, float(stop),
                                              None if take is None else float(take), 0, fraction,
                                              self.now().isoformat(timespec="seconds"), bar)
        return True

    def _close(self, symbol: str, position: dict[str, Any] | None, why: str, price: float) -> bool:
        holding = self.state.holdings.get(symbol)
        crypto = "/" in symbol
        if position is not None:
            qty = abs(float(position.get("qty") or 0))
            side = "sell" if float(position.get("qty") or 0) > 0 else "buy"
            if qty > 0 and not self._order(symbol, side, qty, crypto, f"exit ({why})"):
                return False
        if holding is not None:
            pnl = holding.direction * (price / holding.entry_price - 1)
            self._note(symbol, f"closed {'long' if holding.direction > 0 else 'short'}: {why}, "
                               f"{pnl:+.2%}")
        self.state.holdings.pop(symbol, None)
        if self.package is not None and self.package.cooldown:
            self.state.cooldown[symbol] = self.package.cooldown
        return True

    def _order(self, symbol: str, side: str, qty: float, crypto: bool, why: str) -> bool:
        line = f"{side} {qty:g} {symbol}: {why}"
        if self.dry_run:
            self._note(symbol, "[dry run] " + line)
            return True
        try:
            self.broker.place(symbol, side, qty, crypto)
        except Exception as exc:
            self._note(symbol, f"order failed ({line}): {' '.join(str(exc).split())[:160]}")
            return False
        self._note(symbol, line)
        return True

    def _note(self, symbol: str, text: str) -> None:
        self.say(f"  {symbol}: {text}")
        self.state.log.append({"at": self.now().isoformat(timespec="seconds"),
                               "symbol": symbol, "what": text})

    def _forget_closed(self, positions: dict[str, Any]) -> None:
        """Holdings the account no longer has (closed by hand, or a stop) are dropped."""
        if self.dry_run:
            return
        for symbol in list(self.state.holdings):
            if symbol not in positions:
                self._note(symbol, "no longer held on the account; forgetting it")
                self.state.holdings.pop(symbol)

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
        """This trading session, for reports/: orders, equity, what's still open."""
        took = (self.now() - self.started).total_seconds()
        hours = f"{took / 3600:.1f} h" if took >= 3600 else f"{took / 60:.0f} min"
        log = self.state.log[self._log_start:]
        orders = [e for e in log if str(e.get("what", "")).startswith(("buy", "sell"))]
        curve = self.state.equity[self._equity_start:]
        change = (curve[-1][1] / curve[0][1] - 1) if len(curve) > 1 and curve[0][1] else 0.0
        summary = (f"{self.cycles} cycle(s) over {hours}, {len(orders)} order(s)"
                   + (f", equity ${curve[0][1]:,.0f} -> ${curve[-1][1]:,.0f} ({change:+.2%})"
                      if len(curve) > 1 else "")
                   + (" [dry run]" if self.dry_run else ""))
        out = Report("trading", summary, tone_of(change),
                     subtitle=(self.package.describe() if self.package else "no champion package")
                     + f"; strategy: {self.style.describe()}; money: {money_text(self.money)}")
        out.stat("Cycles", self.cycles).stat("Orders", len(orders))
        if curve:
            out.stat("Equity", f"${curve[-1][1]:,.2f}")
        if len(curve) > 1:
            out.stat("Change", f"{change:+.2%}", tone_of(change))
        out.stat("Open", len(self.state.holdings))
        if self.state.halted:
            out.stat("Halted", "yes", "bad")
        out.table("Open positions", [
            {"Symbol": sym, "Side": "LONG" if h.direction > 0 else "SHORT",
             "Entry": round(h.entry_price, 2), "Stop": round(h.stop, 2),
             "Candles held": h.bars, "Since": h.opened[:16]}
            for sym, h in self.state.holdings.items()], "Nothing open.")
        out.table("What it did", [
            {"At": str(e.get("at", ""))[:16], "Symbol": e.get("symbol"), "What": e.get("what")}
            for e in log[::-1]], "No orders this session.")
        return out

    # ------------------------------------------------------------ the loop

    def run_forever(self) -> None:
        step = duration(self.cfg.base_tf).total_seconds()
        self.say(f"Trading every {int(step // 60)} minutes. Ctrl+C stops it "
                 "(open positions stay open on the account).")
        while True:
            try:
                self.cycle()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # a bad cycle (network) must not end the trader
                self.say(f"Cycle failed: {' '.join(str(exc).split())[:200]}")
            now = time.time()
            wait = step - now % step + 15  # just after the next candle closes
            time.sleep(wait)
