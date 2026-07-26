"""Event-driven daily backtest engine.

Bias control:
- Signals are computed on bar t's CLOSE; the resulting orders fill at bar
  t+1's OPEN (with slippage + commission) — the bot never trades on prices
  it hasn't seen yet.
- Stops and take-profits are checked intrabar against t+1's high/low, filling
  at the stop level (or the open, if the bar gapped through it).
- The max-drawdown circuit breaker liquidates everything and halts trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..broker.base import ExecutionModel, Order
from ..portfolio import Portfolio, Trade
from ..risk import RiskConfig, RiskEngine
from ..strategies.base import Strategy


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list[Trade]
    metrics: dict[str, float]
    signals_log: pd.DataFrame  # one row per (date, symbol) decision
    halted: bool


@dataclass
class BacktestEngine:
    strategy: Strategy
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionModel = field(default_factory=ExecutionModel)
    starting_cash: float = 100_000.0
    lookback: int = 300  # bars of history handed to the strategy

    def run(self, data: dict[str, pd.DataFrame]) -> BacktestResult:
        from .metrics import compute_metrics

        if not data:
            raise ValueError("No symbol data supplied to backtest")

        risk_engine = RiskEngine(self.risk)
        portfolio = Portfolio(starting_cash=self.starting_cash)
        calendar = sorted(set().union(*(df.index for df in data.values())))
        warmup = self.strategy.warmup
        pending: list[Order] = []
        log_rows: list[dict] = []

        for i, ts in enumerate(calendar):
            todays = {s: df.loc[ts] for s, df in data.items() if ts in df.index}
            if not todays:
                continue

            # 1) Fill orders queued at yesterday's close, at today's open.
            for order in pending:
                bar = todays.get(order.symbol)
                if bar is None:
                    continue
                fill = self.execution.simulate(order, float(bar["open"]), ts)
                self._apply_fill(portfolio, risk_engine, fill, data[order.symbol], ts)
            pending = []

            # 2) Intrabar stop / take-profit exits.
            self._process_exits(portfolio, risk_engine, todays, ts)

            # 3) Mark to market at close; trail stops; check the kill switch.
            closes = {s: float(bar["close"]) for s, bar in todays.items()}
            portfolio.mark(closes, ts)
            self._trail_stops(portfolio, risk_engine, data, ts)

            if risk_engine.check_circuit_breaker(portfolio.equity):
                if portfolio.positions:
                    # Liquidate at close (breaker exit is an emergency, not a signal).
                    for symbol in list(portfolio.positions):
                        pos = portfolio.positions[symbol]
                        price = closes.get(symbol, pos.last_price)
                        fill = self.execution.simulate(
                            Order(symbol, -pos.qty, "circuit breaker"), price, ts
                        )
                        portfolio.apply_fill(
                            symbol, fill.qty, fill.price, fill.commission, ts, "circuit breaker"
                        )
                continue  # halted: no new signals, keep marking equity

            # 4) Signals at close -> orders for tomorrow's open.
            if i < warmup:
                continue
            for symbol, df in data.items():
                if ts not in df.index:
                    continue
                history = df.loc[:ts].tail(self.lookback)
                if not self.strategy.ready(history):
                    continue
                sig = self.strategy.signal(history)
                log_rows.append(
                    {
                        "date": ts,
                        "symbol": symbol,
                        "direction": sig.direction,
                        "conviction": sig.conviction,
                        "reason": sig.reason,
                    }
                )
                order = self._order_for_signal(
                    symbol, sig.direction, sig.conviction, history, portfolio, risk_engine
                )
                if order is not None:
                    pending.append(order)

        equity = portfolio.equity_series()
        return BacktestResult(
            equity=equity,
            trades=portfolio.trades,
            metrics=compute_metrics(equity, portfolio.trades),
            signals_log=pd.DataFrame(log_rows),
            halted=risk_engine.halted,
        )

    # ------------------------------------------------------------------

    def _order_for_signal(
        self,
        symbol: str,
        direction: int,
        conviction: float,
        history: pd.DataFrame,
        portfolio: Portfolio,
        risk_engine: RiskEngine,
    ) -> Order | None:
        pos = portfolio.positions.get(symbol)
        held = pos.direction if pos else 0

        if direction == held:
            return None  # already positioned this way (or flat + no signal)

        if held != 0 and direction != held:
            # Exit current position; if reversing, also open the other way.
            exit_qty = -pos.qty
            if direction == 0:
                return Order(symbol, exit_qty, "signal flat")
            price = float(history["close"].iloc[-1])
            entry_qty = risk_engine.size_position(
                direction, conviction, price, history, portfolio
            )
            return Order(symbol, exit_qty + entry_qty, "signal reversal")

        # Flat -> new entry.
        price = float(history["close"].iloc[-1])
        qty = risk_engine.size_position(direction, conviction, price, history, portfolio)
        if qty == 0:
            return None
        return Order(symbol, qty, "signal entry")

    def _apply_fill(
        self,
        portfolio: Portfolio,
        risk_engine: RiskEngine,
        fill,
        df: pd.DataFrame,
        ts: pd.Timestamp,
    ) -> None:
        portfolio.apply_fill(fill.symbol, fill.qty, fill.price, fill.commission, ts, fill.reason)
        pos = portfolio.positions.get(fill.symbol)
        if pos is not None and pos.stop_price is None:
            history = df.loc[:ts]
            atr_value = risk_engine.atr(history)
            if atr_value > 0:
                stop, take = risk_engine.initial_stops(pos.direction, pos.avg_price, atr_value)
                pos.stop_price = stop
                pos.take_profit = take

    def _process_exits(
        self, portfolio: Portfolio, risk_engine: RiskEngine, todays: dict, ts: pd.Timestamp
    ) -> None:
        for symbol in list(portfolio.positions):
            bar = todays.get(symbol)
            pos = portfolio.positions.get(symbol)
            if bar is None or pos is None or pos.stop_price is None:
                continue
            low, high, open_ = float(bar["low"]), float(bar["high"]), float(bar["open"])
            exit_price = None
            reason = ""
            if risk_engine.stop_hit(pos.direction, pos.stop_price, low, high):
                # If the bar gapped through the stop, fill at the open, not the stop.
                exit_price = (
                    min(open_, pos.stop_price) if pos.direction > 0 else max(open_, pos.stop_price)
                )
                reason = "stop loss"
            elif risk_engine.take_profit_hit(pos.direction, pos.take_profit, low, high):
                exit_price = (
                    max(open_, pos.take_profit) if pos.direction > 0 else min(open_, pos.take_profit)
                )
                reason = "take profit"
            if exit_price is not None:
                fill = self.execution.simulate(Order(symbol, -pos.qty, reason), exit_price, ts)
                portfolio.apply_fill(symbol, fill.qty, fill.price, fill.commission, ts, reason)

    def _trail_stops(
        self,
        portfolio: Portfolio,
        risk_engine: RiskEngine,
        data: dict[str, pd.DataFrame],
        ts: pd.Timestamp,
    ) -> None:
        for symbol, pos in portfolio.positions.items():
            if pos.stop_price is None or symbol not in data or ts not in data[symbol].index:
                continue
            history = data[symbol].loc[:ts]
            atr_value = risk_engine.atr(history)
            if atr_value > 0:
                pos.stop_price = risk_engine.trail_stop(
                    pos.direction, pos.stop_price, float(history["close"].iloc[-1]), atr_value
                )
