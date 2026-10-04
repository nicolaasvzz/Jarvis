"""Live trading loop: paper by default, real brokerage only if configured.

Each cycle:
  1. pull the latest bars for the universe,
  2. mark the persistent portfolio to market,
  3. enforce stops and the drawdown circuit breaker,
  4. compute ensemble signals and route risk-sized orders to the broker.

Portfolio state (cash, positions, stops, trade log, high-water mark) is
persisted to JSON between runs, so the loop — or a cron job running
`trade --once` — can be stopped and restarted safely.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from .broker.base import Broker, Order
from .config import BotConfig
from .data import DataFeed
from .jarvis_status import write_status
from .portfolio import Portfolio, Position, Trade
from .risk import RiskEngine
from .strategies import Ensemble


@dataclass
class LiveTrader:
    config: BotConfig
    feed: DataFeed
    strategy: Ensemble
    broker: Broker
    console: Console
    show_dashboard: bool = True  # off when a web UI is the display instead

    def __post_init__(self):
        settings = self.config.live_settings
        self.state_path = Path(settings["state_file"])
        self.lookback = settings["lookback"]
        self.risk_engine = RiskEngine(self.config.build_risk())
        self.learner = self.config.build_learning(self.strategy)
        self.signal_memory: dict[str, dict] = {}  # per-symbol votes on the last seen bar
        self.portfolio = self._load_state(settings["starting_cash"])
        if self.learner:
            self.strategy.apply_weights(self.learner.weights)
            self.console.print(f"[dim]learning on - weights: {self._fmt_weights()}[/dim]")

    # ---------------- state persistence ----------------

    def _load_state(self, starting_cash: float) -> Portfolio:
        portfolio = Portfolio(starting_cash=starting_cash)
        if not self.state_path.exists():
            return portfolio
        state = json.loads(self.state_path.read_text())
        portfolio.cash = state["cash"]
        for p in state.get("positions", []):
            portfolio.positions[p["symbol"]] = Position(
                symbol=p["symbol"],
                qty=p["qty"],
                avg_price=p["avg_price"],
                opened_at=pd.Timestamp(p["opened_at"]),
                stop_price=p.get("stop_price"),
                take_profit=p.get("take_profit"),
                last_price=p.get("last_price", p["avg_price"]),
                entry_votes=p.get("entry_votes"),
            )
        for t, e in state.get("equity_curve", []):
            portfolio.equity_curve.append((pd.Timestamp(t), e))
        self.risk_engine.peak_equity = state.get("peak_equity")
        self.risk_engine.halted = state.get("halted", False)
        for t in state.get("trades") or []:
            portfolio.trades.append(
                Trade(
                    symbol=t["symbol"],
                    direction=int(t["direction"]),
                    qty=float(t["qty"]),
                    entry_price=float(t["entry_price"]),
                    exit_price=float(t["exit_price"]),
                    entry_time=pd.Timestamp(t["entry_time"]),
                    exit_time=pd.Timestamp(t["exit_time"]),
                    pnl=float(t["pnl"]),
                    reason=t.get("reason", ""),
                )
            )
        self.signal_memory = state.get("signal_memory") or {}
        if self.learner and state.get("learning"):
            self.learner.restore(state["learning"])
        return portfolio

    def _save_state(self) -> None:
        state = {
            "cash": self.portfolio.cash,
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_price": p.avg_price,
                    "opened_at": str(p.opened_at),
                    "stop_price": p.stop_price,
                    "take_profit": p.take_profit,
                    "last_price": p.last_price,
                    "entry_votes": p.entry_votes,
                }
                for p in self.portfolio.positions.values()
            ],
            "equity_curve": [[str(t), e] for t, e in self.portfolio.equity_curve[-2520:]],
            "trades": [
                {
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "qty": t.qty,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time),
                    "pnl": t.pnl,
                    "reason": t.reason,
                }
                for t in self.portfolio.trades[-500:]
            ],
            "peak_equity": self.risk_engine.peak_equity,
            "halted": self.risk_engine.halted,
            "signal_memory": self.signal_memory,
            "learning": self.learner.to_dict() if self.learner else None,
        }
        self.state_path.write_text(json.dumps(state, indent=2))
        write_status(self.config)  # the summary Jarvis's TradeBot page shows

    # ---------------- one trading cycle ----------------

    def run_cycle(self) -> None:
        now = pd.Timestamp.now()
        universe = self.config.universe
        histories: dict[str, pd.DataFrame] = {}
        for symbol in universe:
            try:
                histories[symbol] = self.feed.latest(symbol, self.lookback)
            except Exception as exc:  # a dead symbol shouldn't kill the loop
                self.console.print(f"[yellow]data error {symbol}: {exc}[/yellow]")
        if not histories:
            self.console.print("[red]No data for any symbol; skipping cycle.[/red]")
            return

        prices = {s: float(df["close"].iloc[-1]) for s, df in histories.items()}
        self.portfolio.mark(prices, now)

        # Stops first — protective exits outrank new ideas.
        for symbol in list(self.portfolio.positions):
            pos = self.portfolio.positions[symbol]
            df = histories.get(symbol)
            if df is None or pos.stop_price is None:
                continue
            bar = df.iloc[-1]
            if self.risk_engine.stop_hit(
                pos.direction, pos.stop_price, float(bar["low"]), float(bar["high"])
            ):
                self._execute(Order(symbol, -pos.qty, "stop loss"), prices[symbol], now)
            elif self.risk_engine.take_profit_hit(
                pos.direction, pos.take_profit, float(bar["low"]), float(bar["high"])
            ):
                self._execute(Order(symbol, -pos.qty, "take profit"), prices[symbol], now)
            else:
                atr_value = self.risk_engine.atr(df)
                if atr_value > 0:
                    pos.stop_price = self.risk_engine.trail_stop(
                        pos.direction, pos.stop_price, prices[symbol], atr_value
                    )

        if self.risk_engine.check_circuit_breaker(self.portfolio.equity):
            self.console.print(
                "[bold red]CIRCUIT BREAKER: max drawdown exceeded — liquidating and halting.[/bold red]"
            )
            for symbol in list(self.portfolio.positions):
                pos = self.portfolio.positions[symbol]
                self._execute(
                    Order(symbol, -pos.qty, "circuit breaker"),
                    prices.get(symbol, pos.last_price),
                    now,
                )
            self._save_state()
            return

        # Signals -> orders. Learning first: score the votes each member cast
        # on the previous bar against the return that actually followed.
        for symbol, history in histories.items():
            if not self.strategy.ready(history):
                continue
            self._bar_feedback(symbol, history)
            sig, votes = self.strategy.signal_and_votes(history)
            self._remember_votes(symbol, history, votes)
            pos = self.portfolio.positions.get(symbol)
            held = pos.direction if pos else 0
            if sig.direction == held:
                continue
            if held != 0:
                self._execute(Order(symbol, -pos.qty, f"exit: {sig.reason}"), prices[symbol], now)
            if sig.direction != 0:
                qty = self.risk_engine.size_position(
                    sig.direction, sig.conviction, prices[symbol], history, self.portfolio
                )
                if qty != 0:
                    self._execute(
                        Order(symbol, qty, f"entry: {sig.reason}"),
                        prices[symbol],
                        now,
                        entry_votes=votes,
                    )

        self._save_state()
        if self.show_dashboard:
            self._render_dashboard(now)

    def _execute(
        self,
        order: Order,
        ref_price: float,
        now: pd.Timestamp,
        entry_votes: dict[str, float] | None = None,
    ) -> None:
        pos_before = self.portfolio.positions.get(order.symbol)
        votes_at_entry = pos_before.entry_votes if pos_before else None
        trades_before = len(self.portfolio.trades)
        try:
            fill = self.broker.submit(order, ref_price, now)
        except Exception as exc:
            self.console.print(f"[red]order failed {order.symbol} {order.qty:+.0f}: {exc}[/red]")
            return
        if fill is None:
            return
        self.portfolio.apply_fill(
            fill.symbol, fill.qty, fill.price, fill.commission, now, fill.reason
        )
        for trade in self.portfolio.trades[trades_before:]:
            self._trade_feedback(trade, votes_at_entry)
        pos = self.portfolio.positions.get(fill.symbol)
        if pos is not None and entry_votes and pos.entry_votes is None:
            pos.entry_votes = dict(entry_votes)
        if pos is not None and pos.stop_price is None:
            try:
                history = self.feed.latest(fill.symbol, self.lookback)
                atr_value = self.risk_engine.atr(history)
                if atr_value > 0:
                    stop, take = self.risk_engine.initial_stops(
                        pos.direction, pos.avg_price, atr_value
                    )
                    pos.stop_price, pos.take_profit = stop, take
            except Exception:
                pass
        side = "BUY" if fill.qty > 0 else "SELL"
        self.console.print(
            f"[cyan]{side} {abs(fill.qty):.0f} {fill.symbol} @ {fill.price:.2f}[/cyan] ({fill.reason})"
        )

    def liquidate_all(self, reason: str = "manual abort") -> None:
        """Close every open position at the latest available price."""
        now = pd.Timestamp.now()
        for symbol in list(self.portfolio.positions):
            pos = self.portfolio.positions[symbol]
            price = pos.last_price
            try:
                price = float(self.feed.latest(symbol, 30)["close"].iloc[-1])
            except Exception:
                pass  # fall back to the last mark
            self._execute(Order(symbol, -pos.qty, reason), price, now)
        self.portfolio.mark({}, now)
        self._save_state()

    # ---------------- learning ----------------

    def _bar_feedback(self, symbol: str, history: pd.DataFrame) -> None:
        """Score the votes cast on the previously seen bar against the new bar."""
        if not self.learner:
            return
        mem = self.signal_memory.get(symbol)
        bar_time = str(history.index[-1])
        close = float(history["close"].iloc[-1])
        if not mem or not mem.get("close") or mem.get("bar_time", "") >= bar_time:
            return  # same bar as last cycle: nothing new has happened yet
        ret = close / mem["close"] - 1
        if self.learner.bar_feedback(mem.get("votes") or {}, ret, label=symbol):
            self.strategy.apply_weights(self.learner.weights)
            self.console.print(
                f"[magenta]LEARN[/magenta] {symbol} new bar {ret:+.2%} vs prior votes"
                f" -> weights: {self._fmt_weights()}"
            )

    def _remember_votes(self, symbol: str, history: pd.DataFrame, votes: dict[str, float]) -> None:
        if not self.learner:
            return
        self.signal_memory[symbol] = {
            "bar_time": str(history.index[-1]),
            "close": float(history["close"].iloc[-1]),
            "votes": dict(votes),
        }

    def _trade_feedback(self, trade, votes: dict[str, float] | None) -> None:
        """A round trip closed: reward members that called it, punish the rest."""
        if not self.learner or not votes:
            return
        if self.learner.trade_feedback(
            votes, trade.direction, trade.return_pct, label=trade.symbol
        ):
            self.strategy.apply_weights(self.learner.weights)
            self.console.print(
                f"[magenta]LEARN[/magenta] {trade.symbol} closed {trade.return_pct:+.2%}"
                f" ({trade.reason}) -> weights: {self._fmt_weights()}"
            )

    def _fmt_weights(self) -> str:
        return ", ".join(f"{n} {w:.0%}" for n, w in self.strategy.weight_map.items())

    def learning_summary(self) -> dict:
        return self.learner.summary() if self.learner else {"enabled": False}

    # ---------------- output ----------------

    def _render_dashboard(self, now: pd.Timestamp) -> None:
        p = self.portfolio
        table = Table(title=f"Portfolio @ {now:%Y-%m-%d %H:%M}  |  equity ${p.equity:,.2f}  cash ${p.cash:,.2f}")
        for col in ("Symbol", "Qty", "Avg Px", "Last", "Stop", "Unreal P&L"):
            table.add_column(col, justify="right")
        for pos in sorted(p.positions.values(), key=lambda x: x.symbol):
            pnl = pos.unrealized_pnl
            color = "green" if pnl >= 0 else "red"
            table.add_row(
                pos.symbol,
                f"{pos.qty:+.0f}",
                f"{pos.avg_price:.2f}",
                f"{pos.last_price:.2f}",
                f"{pos.stop_price:.2f}" if pos.stop_price else "-",
                f"[{color}]{pnl:+,.2f}[/{color}]",
            )
        if not p.positions:
            table.add_row("(flat)", "-", "-", "-", "-", "-")
        self.console.print(table)

    def run_forever(self, poll_minutes: float) -> None:
        self.console.print(
            f"[bold]Live loop started[/bold] — polling every {poll_minutes:g} min. Ctrl-C to stop."
        )
        while True:
            try:
                self.run_cycle()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.console.print(f"[red]cycle error: {exc}[/red]")
            try:
                time.sleep(poll_minutes * 60)
            except KeyboardInterrupt:
                self.console.print("\n[bold]Stopped. State saved.[/bold]")
                return
