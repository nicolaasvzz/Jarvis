"""Broker abstraction shared by backtests, paper trading, and live adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Order:
    symbol: str
    qty: float  # signed: >0 buy, <0 sell
    reason: str = ""

    def __post_init__(self):
        if self.qty == 0:
            raise ValueError("Order qty cannot be 0")


@dataclass(frozen=True)
class Fill:
    symbol: str
    qty: float  # signed
    price: float  # effective price incl. slippage
    commission: float
    timestamp: pd.Timestamp
    reason: str = ""


@dataclass
class ExecutionModel:
    """Turns a reference price into an effective fill price + commission.

    slippage_bps models market impact / spread crossing: buys fill above the
    reference price, sells below.
    """

    slippage_bps: float = 5.0
    commission_per_share: float = 0.0
    commission_pct: float = 0.0005  # 5 bps of notional
    min_commission: float = 0.0

    def fill_price(self, side: int, ref_price: float) -> float:
        return ref_price * (1 + side * self.slippage_bps / 10_000)

    def commission(self, qty: float, price: float) -> float:
        c = abs(qty) * self.commission_per_share + abs(qty) * price * self.commission_pct
        return max(c, self.min_commission)

    def simulate(self, order: Order, ref_price: float, timestamp: pd.Timestamp) -> Fill:
        side = 1 if order.qty > 0 else -1
        price = self.fill_price(side, ref_price)
        return Fill(
            symbol=order.symbol,
            qty=order.qty,
            price=price,
            commission=self.commission(order.qty, price),
            timestamp=timestamp,
            reason=order.reason,
        )


@dataclass
class Broker(ABC):
    """Order routing interface for the live loop."""

    fills: list[Fill] = field(default_factory=list, init=False)

    @abstractmethod
    def submit(self, order: Order, ref_price: float, timestamp: pd.Timestamp) -> Fill | None:
        """Execute an order; returns the Fill (or None if rejected)."""
