"""Risk engine: position sizing, stops, and portfolio-level circuit breakers.

Sizing is volatility-targeted: risk a fixed fraction of equity per trade,
with the stop distance defined in ATR multiples, so position size shrinks
automatically in wild markets. Portfolio guards (max drawdown, exposure cap,
per-position weight cap) can veto or scale any entry.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind
from .portfolio import Portfolio


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01  # fraction of equity risked between entry and stop
    atr_window: int = 14
    atr_stop_multiple: float = 3.0  # initial + trailing stop distance
    take_profit_multiple: float | None = None  # ATR multiples; None disables
    max_position_weight: float = 0.20  # |position| / equity cap
    max_gross_exposure: float = 1.0  # sum |positions| / equity cap
    max_drawdown: float = 0.25  # kill switch: liquidate & halt beyond this
    allow_short: bool = True

    def __post_init__(self):
        if not 0 < self.risk_per_trade <= 0.1:
            raise ValueError("risk_per_trade should be in (0, 0.1]")
        if self.max_drawdown <= 0 or self.max_drawdown >= 1:
            raise ValueError("max_drawdown must be in (0, 1)")


class RiskEngine:
    def __init__(self, config: RiskConfig):
        self.config = config
        self.peak_equity: float | None = None
        self.halted = False

    # ---------------- portfolio-level guards ----------------

    def check_circuit_breaker(self, equity: float) -> bool:
        """Update the high-water mark; returns True (and latches) when the
        max-drawdown kill switch trips."""
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
        if self.halted:
            return True
        drawdown = 1.0 - equity / self.peak_equity
        if drawdown >= self.config.max_drawdown:
            self.halted = True
        return self.halted

    # ---------------- per-trade sizing ----------------

    def atr(self, history: pd.DataFrame) -> float:
        value = ind.atr(
            history["high"], history["low"], history["close"], self.config.atr_window
        ).iloc[-1]
        return float(value) if not np.isnan(value) else 0.0

    def size_position(
        self,
        direction: int,
        conviction: float,
        price: float,
        history: pd.DataFrame,
        portfolio: Portfolio,
    ) -> float:
        """Return a signed target quantity for a new entry (0 = don't trade)."""
        cfg = self.config
        if self.halted or direction == 0 or price <= 0:
            return 0.0
        if direction < 0 and not cfg.allow_short:
            return 0.0

        atr_value = self.atr(history)
        if atr_value <= 0:
            return 0.0

        equity = portfolio.equity
        stop_distance = atr_value * cfg.atr_stop_multiple
        qty = (equity * cfg.risk_per_trade * conviction) / stop_distance

        # Cap by per-position weight.
        max_qty_weight = (equity * cfg.max_position_weight) / price
        qty = min(qty, max_qty_weight)

        # Cap by remaining gross exposure budget.
        room = equity * cfg.max_gross_exposure - portfolio.gross_exposure
        if room <= 0:
            return 0.0
        qty = min(qty, room / price)

        qty = float(np.floor(qty))  # whole shares
        return qty * direction if qty >= 1 else 0.0

    # ---------------- stops ----------------

    def initial_stops(
        self, direction: int, entry_price: float, atr_value: float
    ) -> tuple[float, float | None]:
        """(stop_price, take_profit_price) for a fresh entry."""
        cfg = self.config
        stop = entry_price - direction * atr_value * cfg.atr_stop_multiple
        take = None
        if cfg.take_profit_multiple is not None:
            take = entry_price + direction * atr_value * cfg.take_profit_multiple
        return stop, take

    def trail_stop(
        self, direction: int, current_stop: float, close: float, atr_value: float
    ) -> float:
        """Ratchet the stop toward price; never loosens."""
        candidate = close - direction * atr_value * self.config.atr_stop_multiple
        if direction > 0:
            return max(current_stop, candidate)
        return min(current_stop, candidate)

    def stop_hit(self, direction: int, stop: float, low: float, high: float) -> bool:
        return low <= stop if direction > 0 else high >= stop

    def take_profit_hit(
        self, direction: int, take: float | None, low: float, high: float
    ) -> bool:
        if take is None:
            return False
        return high >= take if direction > 0 else low <= take
