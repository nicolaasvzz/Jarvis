"""Portfolio ledger: cash, positions, marks, equity curve, and trade log."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Position:
    symbol: str
    qty: float  # negative = short
    avg_price: float
    opened_at: pd.Timestamp
    stop_price: float | None = None
    take_profit: float | None = None
    last_price: float = 0.0

    @property
    def direction(self) -> int:
        return 1 if self.qty > 0 else -1 if self.qty < 0 else 0

    @property
    def market_value(self) -> float:
        return self.qty * self.last_price

    @property
    def unrealized_pnl(self) -> float:
        return self.qty * (self.last_price - self.avg_price)


@dataclass
class Trade:
    """A closed round trip (or partial close)."""

    symbol: str
    direction: int
    qty: float
    entry_price: float
    exit_price: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    pnl: float
    reason: str = ""

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return self.direction * (self.exit_price - self.entry_price) / self.entry_price


@dataclass
class Portfolio:
    starting_cash: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[pd.Timestamp, float]] = field(default_factory=list)

    def __post_init__(self):
        self.cash = self.starting_cash

    @property
    def positions_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + self.positions_value

    @property
    def gross_exposure(self) -> float:
        return sum(abs(p.market_value) for p in self.positions.values())

    def mark(self, prices: dict[str, float], timestamp: pd.Timestamp) -> None:
        """Mark positions to market and append to the equity curve."""
        for symbol, pos in self.positions.items():
            if symbol in prices:
                pos.last_price = prices[symbol]
        self.equity_curve.append((timestamp, self.equity))

    def apply_fill(
        self,
        symbol: str,
        qty: float,
        price: float,
        commission: float,
        timestamp: pd.Timestamp,
        reason: str = "",
    ) -> None:
        """Apply a fill (signed qty) to cash and positions, logging round trips."""
        self.cash -= qty * price + commission
        pos = self.positions.get(symbol)

        if pos is None or pos.qty == 0:
            self.positions[symbol] = Position(
                symbol=symbol, qty=qty, avg_price=price, opened_at=timestamp, last_price=price
            )
            return

        if pos.direction == (1 if qty > 0 else -1):
            # Scaling in: blend the average price.
            total = pos.qty + qty
            pos.avg_price = (pos.avg_price * pos.qty + price * qty) / total
            pos.qty = total
            pos.last_price = price
            return

        # Reducing / closing / flipping.
        closing_qty = min(abs(qty), abs(pos.qty)) * (1 if qty > 0 else -1)
        closed_against = -closing_qty  # portion of the existing position closed
        pnl = closed_against * (price - pos.avg_price)
        self.trades.append(
            Trade(
                symbol=symbol,
                direction=pos.direction,
                qty=abs(closing_qty),
                entry_price=pos.avg_price,
                exit_price=price,
                entry_time=pos.opened_at,
                exit_time=timestamp,
                pnl=pnl,
                reason=reason,
            )
        )
        remainder = pos.qty + qty
        if remainder == 0:
            del self.positions[symbol]
        elif (remainder > 0) == (pos.qty > 0):
            pos.qty = remainder  # partial close, same side
            pos.last_price = price
        else:
            # Flipped through zero: open a fresh position with the leftover.
            self.positions[symbol] = Position(
                symbol=symbol,
                qty=remainder,
                avg_price=price,
                opened_at=timestamp,
                last_price=price,
            )

    def equity_series(self) -> pd.Series:
        if not self.equity_curve:
            return pd.Series(dtype=float)
        idx, vals = zip(*self.equity_curve)
        return pd.Series(vals, index=pd.DatetimeIndex(idx), name="equity")
